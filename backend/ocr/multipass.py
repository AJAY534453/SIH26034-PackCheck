"""Multi-pass OCR with intelligent routing.

PASS 1 (always): original + the standard light variants — results are UNIONED (merge by
bbox IoU in original coordinates), not first-variant-wins: declarations that only ONE
variant reads (e.g. small print readable only after upscale) survive the union.
PASS 2 (conditional): targeted passes chosen from PASS-1 evidence gaps:
  - no MRP found            → numeric/date-optimized pass (small-text upscale + sharpen)
  - no dates found          → thresholded pass (inkjet date codes)
  - few lines / poor quality→ denoised + upscaled pass
  - non-Latin script expected but none detected → (engine-dependent; recorded honestly)
PASS 3 (conditional, bounded): bottom-strip region pass — the lower declaration panel
(MRP / net quantity / batch / dates on most packages) is re-OCR'd as a 2× upscaled crop
when critical declarations are STILL missing after targeted passes. The predicate checks the
SPECIFIC declarations (MRP, net quantity, batch, manufacturing date, expiry) rather than
"any date field", so a duration such as 'BEST BEFORE 12 MONTHS' cannot mask a genuinely
unread manufacturing/use-by date. Crop bboxes are mapped back to original-image coordinates
so evidence crops/highlighting stay valid.

Routing is evidence-driven: a GOOD-quality image whose PASS-1 already yields the critical
declarations runs no extra passes. Uncertainty drives extra effort, never blind repetition.
Every pass result is merged into one bbox-aware line set with provenance preserved; no
result is ever invented — extra passes can only ADD observations, not replace them.
"""
from __future__ import annotations

from backend.extraction.association import associate_label_values
from backend.extraction.base import nutrition_line_flags
from backend.extraction.engine import run_extraction
from backend.ocr import ocr_available, recognize
from backend.ocr.base import OcrLine, merge_lines
from backend.preprocessing.enhance import build_variant


def _field_set(lines: list[OcrLine]) -> set[str]:
    """Fields with a usable value, evaluated exactly as the pipeline does (association first)."""
    try:
        per_field = run_extraction({1: associate_label_values(lines)})
    except Exception:
        return set()
    return {f for f, cands in per_field.items() if any(c.value for c in cands)}


def _targeted_passes(img, lines: list[OcrLine], quality_status: str) -> list[str]:
    """Decide which targeted preprocessing passes are worth running."""
    fields = _field_set(lines)
    passes: list[str] = []
    if "mrp" not in fields:
        passes.append("smalltext")       # upscale + sharpen: MRP is often small print
    if not any(f.startswith("date_") for f in fields):
        passes.append("thresholded")     # inkjet date codes respond to thresholding
    if "net_quantity" not in fields:
        passes.append("upscaled")
    if "fssai_license" not in fields and "consumer_care_phone" not in fields:
        passes.append("smalltext")
    if quality_status in ("POOR", "UNUSABLE") and "denoised" not in passes:
        passes.append("denoised")
    return passes[:2]  # bounded: at most two targeted passes per image (CPU budget)


def _bottom_strip_lines(img) -> list[OcrLine]:
    """PASS 3 region pass: OCR the bottom declaration strip at 2× upscale.

    The strip is where MRP, net quantity, batch and dates typically live; small print +
    glare there defeats full-image passes. Crop coordinates are mapped back to ORIGINAL
    image coordinates (x/scale, y0 + y/scale) so evidence crops and the EvidenceViewer
    highlight the true source region.
    """
    import cv2

    h, w = img.shape[:2]
    y0 = int(h * 0.78)
    strip = img[y0:h, 0:w]
    if strip.size == 0 or not ocr_available():
        return []
    scale = 2.0
    up = cv2.resize(strip, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    lines = recognize(up, variant="bottom_strip")
    out: list[OcrLine] = []
    for l in lines:
        x1, y1, x2, y2 = l.bbox
        out.append(
            OcrLine(
                text=l.text,
                confidence=l.confidence,  # engine-reported, never discounted or inflated
                bbox=(
                    max(0, int(x1 / scale)),
                    y0 + max(0, int(y1 / scale)),
                    min(w, int(x2 / scale) + 1),
                    min(h, y0 + int(y2 / scale) + 1),
                ),
                engine=l.engine,
                variant="bottom_strip",
            )
        )
    return out


def run_multipass_ocr(img, base_lines: list[OcrLine], quality_status: str = "GOOD") -> tuple[list[OcrLine], list[str]]:
    """Run conditional targeted + region passes and merge into the base line set.

    Returns (merged_lines, pass_names_run). Never fabricates: only actual OCR results
    (bbox-rescaled to original coordinates) are added.
    """
    passes = _targeted_passes(img, base_lines, quality_status)
    if not passes and not ocr_available():
        return base_lines, []
    import numpy as np

    merged = list(base_lines)
    h0, w0 = img.shape[:2]
    for name in passes:
        variant_img = build_variant(img, name)
        if variant_img is None:
            continue
        lines = recognize(variant_img, variant=name)
        if not lines:
            continue
        vh, vw = variant_img.shape[:2]
        sx, sy = vw / w0, vh / h0
        rescaled: list[OcrLine] = []
        for l in lines:
            x1, y1, x2, y2 = l.bbox
            rescaled.append(OcrLine(
                text=l.text, confidence=l.confidence,
                bbox=(max(0, int(x1 / sx)), max(0, int(y1 / sy)), int(x2 / sx) + 1, int(y2 / sy) + 1),
                engine=l.engine, variant=name,
            ))
        merged = merge_lines(merged, rescaled)

    # PASS 3 (bounded): if critical declarations are still missing after targeted passes,
    # re-OCR the bottom declaration strip as an upscaled crop.
    #
    # A single satisfied date field is NOT enough to skip this pass: on most packages the
    # manufacturing / use-by DATES are tiny inkjet codes in the bottom panel, while a
    # 'BEST BEFORE 12 MONTHS' duration elsewhere would otherwise mask their absence. The
    # escalation therefore checks the SPECIFIC date declarations that carry a calendar value.
    fields = _field_set(merged)
    # The two date declarations that carry an actual compliance signal (Rule 6(1)(d) / 6(1)(g)).
    # `date_packing` is deliberately excluded: it is optional on many packages and would make
    # this pass fire unconditionally.
    date_gap = any(f not in fields for f in ("date_manufacturing", "date_expiry"))
    critical_missing = (
        "mrp" not in fields
        or "net_quantity" not in fields
        or "batch_lot" not in fields
        or date_gap
    )
    if critical_missing and "bottom_strip" not in passes:
        strip_lines = _bottom_strip_lines(img)
        if strip_lines:
            merged = merge_lines(merged, strip_lines)
            passes.append("bottom_strip")
    return merged, passes


def pass_summary(passes: list[str]) -> str:
    if not passes:
        return "Single-pass OCR was sufficient (all critical declarations found in PASS 1)."
    return "Targeted OCR passes run: " + ", ".join(passes) + " (evidence gaps after PASS 1)."
