"""On-device vision engine.

Why this module exists
----------------------
The platform's perception was "OCR + an OPTIONAL third-party vision provider". With no provider
key configured the second source did not run at all, so a deployment reported
``Vision extraction skipped`` on every single scan — the pipeline was honest but the capability
was simply absent. This engine is the always-available second source: it runs locally, offline,
with no API key, and reports FACTS ABOUT THE IMAGE:

* **layout** — where the printed regions are, which one is the most prominent text block (the
  wordmark / brand band on a typical pack), which regions are graphic marks rather than text;
* **readability** — measured sharpness, contrast, glare and OCR visibility per image;
* **the candidate principal display region** — the largest contiguous inked area.

What it deliberately does NOT do
--------------------------------
It never reads a *value*. It emits no declaration, no MRP, no date. Its output is (a) auditable
regions persisted as evidence rows so an inspector can open the exact area, (b) a readability
measurement that is kept strictly separate from the physical font-size requirement — a
declaration can be perfectly readable on a photograph and still fail the Rule 7 millimetre
minimum — and (c) layout facts handed to the placement check as observations, not verdicts.

Two-phase, so it can run concurrently with OCR
----------------------------------------------
``prepare_image`` is pure pixel work (decode, downscale, ink mask, panel, marks, readability
metrics) and needs no OCR at all — it is safe on a worker thread while the recogniser runs.
``finish_image`` then adds the text-block/prominence analysis from the OCR lines that came back.
The pipeline runs the two phases around the OCR step, which is where the wall-clock saving comes
from. Everything here is deterministic arithmetic on pixels: same image in, same regions out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # OpenCV + NumPy ship with the OCR stack; the engine degrades honestly without them.
    import cv2
    import numpy as np
except Exception:  # pragma: no cover - environment without the vision stack
    cv2 = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]

from backend.config import settings

ENGINE_NAME = "on-device-vision"
ENGINE_VERSION = "1"
ENGINE_LABEL = f"{ENGINE_NAME}:{ENGINE_VERSION}"

# Analysis is done on a downscaled copy for speed; every bbox is mapped back to ORIGINAL pixels so
# vision regions line up exactly with the OCR boxes and the evidence crops.
MAX_ANALYSIS_DIM = 1100
GRID = 4  # the coarse lattice used to find contiguous inked areas

# ---------- vision status vocabulary (persisted verbatim on the inspection) ----------
STATUS_COMPLETED = "COMPLETED"                              # on-device vision produced regions
STATUS_COMPLETED_WITH_PROVIDER = "COMPLETED_WITH_PROVIDER"  # + a provider contributed readings
STATUS_PROVIDER_FALLBACK = "ON_DEVICE_ONLY_PROVIDER_FAILED"  # provider configured but failed
STATUS_PROVIDER_NOT_CONFIGURED = "ON_DEVICE_ONLY_NO_PROVIDER"  # no provider key — still ran
STATUS_FAILED = "FAILED"                                    # on-device analysis itself failed

READABLE = "VISUALLY_READABLE"
MARGINAL = "VISUALLY_MARGINAL"
UNREADABLE = "VISUALLY_UNREADABLE"

# Region kinds stored in the vision_regions table.
KIND_PANEL = "PANEL"
KIND_TEXT_BLOCK = "TEXT_BLOCK"
KIND_SYMBOL = "SYMBOL"
KIND_LEGIBILITY = "LEGIBILITY"


def available() -> bool:
    """Whether the on-device vision engine can actually run in this environment."""
    return cv2 is not None and np is not None


def availability_reason() -> str:
    if available():
        return f"{ENGINE_LABEL} available (OpenCV + NumPy present)."
    return (
        "On-device vision cannot run: OpenCV/NumPy are not importable in this environment. "
        "Install the imaging stack and reprocess — no vision result is fabricated."
    )


def bbox_str(box: tuple[int, int, int, int] | None) -> str:
    return "" if box is None else f"{box[0]},{box[1]},{box[2]},{box[3]}"


def parse_bbox(raw: str) -> tuple[int, int, int, int] | None:
    parts = (raw or "").split(",")
    if len(parts) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(float(p)) for p in parts)
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


@dataclass
class Region:
    """One visual region or measurement. Facts only — never a declaration value."""

    kind: str
    label: str
    bbox: tuple[int, int, int, int] | None = None
    confidence: float = 0.0
    prominence: float = 0.0
    contrast: float = 0.0
    sharpness: float = 0.0
    text_density: float = 0.0
    text: str = ""
    note: str = ""


@dataclass
class ImageVision:
    image_id: int
    filename: str
    width: int
    height: int
    regions: list[Region] = field(default_factory=list)
    hero_bbox: tuple[int, int, int, int] | None = None
    hero_text: str = ""
    hero_prominence: float = 0.0
    legibility: str = ""
    error: str = ""


@dataclass
class ImagePrep:
    """Pixel-phase output. Holds arrays only for the duration of one pipeline run."""

    image_id: int
    filename: str
    width: int = 0
    height: int = 0
    scale: float = 1.0
    gray: object | None = None  # numpy 2-D arrays (downscaled)
    mask: object | None = None
    panel_box: tuple[int, int, int, int] | None = None
    panel_density: float = 0.0
    marks: list[dict] = field(default_factory=list)
    sharpness: float = 0.0
    contrast: float = 0.0
    glare: float = 0.0
    shadow: float = 0.0
    error: str = ""

    @property
    def small_width(self) -> int:
        return 0 if self.gray is None else int(self.gray.shape[1])

    @property
    def small_height(self) -> int:
        return 0 if self.gray is None else int(self.gray.shape[0])


@dataclass
class VisionResult:
    engine: str
    images: list[ImageVision] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and any(v.regions for v in self.images)

    @property
    def region_count(self) -> int:
        return sum(len(v.regions) for v in self.images)


# ---------- pixel helpers ----------


def _load(path: Path):
    return cv2.imread(str(path))


def _downscale(img, max_dim: int = MAX_ANALYSIS_DIM):
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= max_dim:
        return img, 1.0
    scale = longest / float(max_dim)
    small = cv2.resize(img, (max(1, int(w / scale)), max(1, int(h / scale))), interpolation=cv2.INTER_AREA)
    return small, scale


def _map_box(box: tuple[int, int, int, int] | None, scale: float, w: int, h: int) -> tuple[int, int, int, int] | None:
    """Scale a box INTO the analysis raster (scale = small / original)."""
    if box is None:
        return None
    x1, y1, x2, y2 = (int(round(v * scale)) for v in box)
    return (max(0, min(w - 1, x1)), max(0, min(h - 1, y1)), max(1, min(w, x2)), max(1, min(h, y2)))


def _unmap_box(box: tuple[int, int, int, int] | None, scale: float, w: int, h: int) -> tuple[int, int, int, int] | None:
    """Scale a box BACK to ORIGINAL pixels (scale = original / small)."""
    return _map_box(box, scale, w, h)


def _ink_mask(gray):
    """Binary print mask: dark strokes against the local background, glare excluded."""
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12)


def _sharpness(gray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _region_metrics(gray, mask, box: tuple[int, int, int, int] | None) -> tuple[float, float, float]:
    """(contrast, sharpness, ink_density) inside a box, measured on real pixels."""
    if box is None or gray is None or mask is None:
        return 0.0, 0.0, 0.0
    x1, y1, x2, y2 = box
    sub_gray = gray[y1:y2, x1:x2]
    sub_mask = mask[y1:y2, x1:x2]
    if sub_gray.size == 0:
        return 0.0, 0.0, 0.0
    contrast = float(sub_gray.std()) / 128.0
    sharp = _sharpness(sub_gray)
    density = float((sub_mask > 0).sum()) / float(sub_mask.size)
    return round(contrast, 3), round(sharp, 1), round(density, 4)


# ---------- region finding ----------


def _lined_blocks(lines) -> list[dict]:
    """Group OCR lines into text blocks using only geometry (vertical gap + overlap)."""
    boxes = []
    for line in lines or []:
        box = getattr(line, "bbox", None)
        text = (getattr(line, "text", "") or "").strip()
        if not box or not text:
            continue
        x1, y1, x2, y2 = (int(v) for v in box)
        if x2 <= x1 or y2 <= y1:
            continue
        boxes.append(
            {
                "box": (x1, y1, x2, y2),
                "height": y2 - y1,
                "text": text,
                "confidence": float(getattr(line, "confidence", 0.0) or 0.0),
            }
        )
    if not boxes:
        return []
    boxes.sort(key=lambda b: (b["box"][1], b["box"][0]))
    heights = sorted(b["height"] for b in boxes)
    median_h = heights[len(heights) // 2] or 1
    gap_tolerance = max(int(median_h * 1.6), 14)

    groups: list[dict] = []
    for box in boxes:
        x1, y1, x2, y2 = box["box"]
        placed = False
        for group in groups:
            gx1, gy1, gx2, gy2 = group["box"]
            vertical_gap = y1 - gy2
            overlap = min(gx2, x2) - max(gx1, x1)
            min_width = max(1, min(gx2 - gx1, x2 - x1))
            if -gap_tolerance <= vertical_gap <= gap_tolerance and overlap / min_width >= 0.15:
                group["box"] = (min(gx1, x1), min(gy1, y1), max(gx2, x2), max(gy2, y2))
                group["lines"].append(box)
                placed = True
                break
        if not placed:
            groups.append({"box": (x1, y1, x2, y2), "lines": [box]})

    out: list[dict] = []
    for group in groups:
        line_heights = sorted(l["height"] for l in group["lines"])
        out.append(
            {
                "box": group["box"],
                "line_count": len(group["lines"]),
                "line_height_px": line_heights[len(line_heights) // 2],
                "confidence": round(sum(l["confidence"] for l in group["lines"]) / len(group["lines"]), 3),
                "text": "\n".join(l["text"] for l in group["lines"][:6]),
            }
        )
    return out


def _inked_panel(mask, w: int, h: int) -> tuple[tuple[int, int, int, int] | None, float]:
    """Largest contiguous inked area on a coarse lattice — the candidate display region."""
    cell_w, cell_h = max(1, w // GRID), max(1, h // GRID)
    inked: set[tuple[int, int]] = set()
    for gy in range(GRID):
        for gx in range(GRID):
            y1, y2 = gy * cell_h, min(h, (gy + 1) * cell_h)
            x1, x2 = gx * cell_w, min(w, (gx + 1) * cell_w)
            cell = mask[y1:y2, x1:x2]
            if cell.size == 0:
                continue
            if float((cell > 0).sum()) / float(cell.size) >= 0.015:
                inked.add((gx, gy))
    if not inked:
        return None, 0.0
    # Largest flood-fill connected component of inked cells (4-neighbourhood).
    best: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for start in list(inked):
        if start in seen:
            continue
        stack, component = [start], []
        seen.add(start)
        while stack:
            cx, cy = stack.pop()
            component.append((cx, cy))
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if (nx, ny) in inked and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        if len(component) > len(best):
            best = component
    xs = [c[0] for c in best]
    ys = [c[1] for c in best]
    box = (min(xs) * cell_w, min(ys) * cell_h, min(w, (max(xs) + 1) * cell_w), min(h, (max(ys) + 1) * cell_h))
    moments = mask[box[1] : box[3], box[0] : box[2]]
    density = float((moments > 0).sum()) / float(moments.size) if moments.size else 0.0
    return box, round(density, 4)


def _graphic_marks(mask, w: int, h: int, text_boxes: list[tuple[int, int, int, int]], limit: int = 5) -> list[dict]:
    """Compact, filled, non-textual components — logo marks, symbols, licence blocks."""
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    image_area = float(w * h)
    marks: list[dict] = []
    for index in range(1, count):
        x, y, bw, bh, area = (int(v) for v in stats[index])
        if bw < 6 or bh < 6:
            continue
        area_ratio = area / image_area
        if area_ratio < 0.00008 or area_ratio > 0.02:
            continue
        aspect = bw / float(bh)
        if aspect < 0.35 or aspect > 4.0:
            continue
        fill = area / float(bw * bh)
        if fill < 0.18:
            continue
        # Skip anything already accounted for as recognised text.
        covered = False
        for tx1, ty1, tx2, ty2 in text_boxes:
            ix = max(0, min(tx2, x + bw) - max(tx1, x))
            iy = max(0, min(ty2, y + bh) - max(ty1, y))
            if ix * iy >= 0.6 * bw * bh:
                covered = True
                break
        if covered:
            continue
        marks.append({"box": (x, y, x + bw, y + bh), "area_ratio": round(area_ratio, 5), "fill": round(fill, 3)})
    marks.sort(key=lambda m: m["area_ratio"], reverse=True)
    return marks[:limit]


# ---------- phase 1: pixels only (runs concurrently with OCR) ----------


def prepare_image(path: Path, *, image_id: int = 0, filename: str = "") -> ImagePrep:
    """Decode, downscale, mask and measure one image. Never raises; failures land in `error`."""
    if not available():
        return ImagePrep(image_id=image_id, filename=filename, error=availability_reason())
    try:
        img = _load(path)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        return ImagePrep(image_id=image_id, filename=filename, error=f"Image could not be read: {exc}")
    if img is None:
        return ImagePrep(image_id=image_id, filename=filename, error="Image could not be decoded for visual analysis.")
    h, w = img.shape[:2]
    small, scale = _downscale(img)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    mask = _ink_mask(gray)
    panel_box, panel_density = _inked_panel(mask, gray.shape[1], gray.shape[0])
    return ImagePrep(
        image_id=image_id,
        filename=filename,
        width=w,
        height=h,
        scale=scale,
        gray=gray,
        mask=mask,
        panel_box=panel_box,
        panel_density=panel_density,
        sharpness=round(_sharpness(gray), 1),
        contrast=round(float(gray.std()) / 128.0, 3),
        glare=round(float((gray >= 245).sum()) / float(gray.size), 4),
        shadow=round(float((gray <= 12).sum()) / float(gray.size), 4),
    )


def prepare_bytes(data: bytes, *, image_id: int = 0, filename: str = "") -> ImagePrep:
    """Same pixel phase as :func:`prepare_image`, for an in-memory upload (self-test endpoint)."""
    if not available():
        return ImagePrep(image_id=image_id, filename=filename, error=availability_reason())
    try:
        buffer = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    except Exception as exc:  # pragma: no cover - malformed upload
        return ImagePrep(image_id=image_id, filename=filename, error=f"Upload could not be decoded: {exc}")
    if img is None:
        return ImagePrep(image_id=image_id, filename=filename, error="Upload could not be decoded as an image.")
    h, w = img.shape[:2]
    small, scale = _downscale(img)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    mask = _ink_mask(gray)
    panel_box, panel_density = _inked_panel(mask, gray.shape[1], gray.shape[0])
    return ImagePrep(
        image_id=image_id,
        filename=filename,
        width=w,
        height=h,
        scale=scale,
        gray=gray,
        mask=mask,
        panel_box=panel_box,
        panel_density=panel_density,
        sharpness=round(_sharpness(gray), 1),
        contrast=round(float(gray.std()) / 128.0, 3),
        glare=round(float((gray >= 245).sum()) / float(gray.size), 4),
        shadow=round(float((gray <= 12).sum()) / float(gray.size), 4),
    )


def decode_bytes(data: bytes):
    """Decode an upload to a BGR array (used by the self-test to share one decode)."""
    if not available():
        return None
    try:
        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:  # pragma: no cover - malformed upload
        return None


def prepare_all(images) -> list[ImagePrep]:
    """Prepare every attached image. `images` are InspectionImage rows."""
    out: list[ImagePrep] = []
    for image in images:
        path = settings.STORAGE_DIR / "originals" / (image.stored_filename or "")
        out.append(
            prepare_image(
                path,
                image_id=image.id,
                filename=image.original_filename or image.stored_filename or "",
            )
        )
    return out


# ---------- phase 2: adds the OCR-dependent analysis ----------


def finish_image(prep: ImagePrep, lines=None) -> ImageVision:
    """Turn a prepared image + its OCR lines into the retained region set."""
    vision = ImageVision(image_id=prep.image_id, filename=prep.filename, width=prep.width, height=prep.height)
    if prep.error:
        vision.error = prep.error
        return vision
    if prep.gray is None or prep.mask is None:  # pragma: no cover - defensive
        vision.error = "Image preparation produced no pixel data."
        return vision

    h, w = prep.height, prep.width
    sw, sh = prep.small_width, prep.small_height
    to_small = 1.0 / prep.scale if prep.scale else 1.0
    blocks = _lined_blocks(lines)

    # --- text blocks + prominence (measured: printed height share, local contrast, OCR confidence)
    prominence_scores: list[tuple[float, dict]] = []
    for block in blocks:
        contrast, sharp, density = _region_metrics(prep.gray, prep.mask, _map_box(block["box"], to_small, sw, sh))
        height_share = min(1.0, (block["line_height_px"] / float(h or 1)) / 0.04)  # 4% of image height ⇒ 1.0
        prominence = round(0.5 * height_share + 0.25 * min(1.0, contrast) + 0.25 * block["confidence"], 3)
        vision.regions.append(
            Region(
                kind=KIND_TEXT_BLOCK,
                label=f"text block · {block['line_count']} line(s)",
                bbox=block["box"],
                confidence=round(block["confidence"], 3),
                prominence=prominence,
                contrast=contrast,
                sharpness=sharp,
                text_density=density,
                text=block["text"],
                note=(
                    f"Printed height {block['line_height_px']} px "
                    f"({block['line_height_px'] / float(h or 1) * 100:.2f}% of image height), local contrast "
                    f"{contrast:.2f}, OCR confidence {block['confidence']:.2f}. Prominence is measured from the "
                    "photograph, not asserted as a legal declaration."
                ),
            )
        )
        prominence_scores.append((prominence, block))
    if prominence_scores:
        hero_prominence, hero = max(prominence_scores, key=lambda item: item[0])
        vision.hero_bbox = hero["box"]
        vision.hero_text = hero["text"].split("\n")[0]
        vision.hero_prominence = hero_prominence
        hero_contrast = _region_metrics(prep.gray, prep.mask, _map_box(hero["box"], to_small, sw, sh))[0]
        vision.regions.append(
            Region(
                kind=KIND_TEXT_BLOCK,
                label="most prominent text block (wordmark / brand band candidate)",
                bbox=hero["box"],
                confidence=round(hero["confidence"], 3),
                prominence=hero_prominence,
                contrast=hero_contrast,
                text=hero["text"],
                note=(
                    "The largest, most contrasted printed block on this face. On a typical pack this is the brand "
                    "band — it is recorded as a visual observation and is never used as a field value by itself."
                ),
            )
        )

    # --- candidate principal display region
    if prep.panel_box is not None:
        vision.regions.append(
            Region(
                kind=KIND_PANEL,
                label="candidate principal display region",
                bbox=_unmap_box(prep.panel_box, prep.scale, w, h),
                confidence=0.5,
                text_density=prep.panel_density,
                note=(
                    f"Largest contiguous inked area (ink density {prep.panel_density:.3f}) on the {GRID}x{GRID} "
                    "lattice. This is a photographic observation about where print sits — which surface legally "
                    "constitutes the principal display panel depends on how the pack is presented for sale and is "
                    "decided by the placement check, not here."
                ),
            )
        )

    # --- graphic marks
    text_boxes_small = [_map_box(b["box"], to_small, sw, sh) for b in blocks]
    for mark in _graphic_marks(prep.mask, sw, sh, [b for b in text_boxes_small if b is not None]):
        vision.regions.append(
            Region(
                kind=KIND_SYMBOL,
                label="graphic mark region",
                bbox=_unmap_box(mark["box"], prep.scale, w, h),
                confidence=0.4,
                text_density=mark["fill"],
                note=(
                    f"Compact non-textual component (area {mark['area_ratio'] * 100:.3f}% of the image, fill "
                    f"{mark['fill']:.2f}) — a logo or symbol area rather than printed text. Its meaning is not "
                    "interpreted here."
                ),
            )
        )

    # --- readability (deliberately separate from the Rule 7 physical font-size check)
    all_lines = list(lines or [])
    visible = [l for l in all_lines if float(getattr(l, "confidence", 0.0) or 0.0) >= 0.6]
    visibility = round(len(visible) / len(all_lines), 3) if all_lines else 0.0
    if not all_lines:
        verdict = UNREADABLE
    elif visibility >= 0.6 and prep.sharpness >= 40.0 and prep.contrast >= 0.15 and prep.glare <= 0.25:
        verdict = READABLE
    elif visibility >= 0.3:
        verdict = MARGINAL
    else:
        verdict = UNREADABLE
    vision.legibility = verdict
    vision.regions.append(
        Region(
            kind=KIND_LEGIBILITY,
            label=f"readability — {verdict.replace('_', ' ').lower()}",
            bbox=None,
            confidence=visibility,
            contrast=prep.contrast,
            sharpness=prep.sharpness,
            note=(
                f"Sharpness (Laplacian variance) {prep.sharpness}, contrast {prep.contrast:.2f}, specular glare "
                f"{prep.glare * 100:.2f}%, crushed shadow {prep.shadow * 100:.2f}%, OCR lines at ≥0.60 confidence "
                f"{len(visible)}/{len(all_lines)}. Readability on a photograph is a different question from the "
                "legal minimum print height: a declaration can be perfectly readable here and still fail the Rule 7 "
                "millimetre requirement, which needs a physical scale."
            ),
        )
    )
    return vision


def finish_all(preps: list[ImagePrep], lines_by_image: dict[int, list] | None = None) -> VisionResult:
    lines_by_image = lines_by_image or {}
    result = VisionResult(engine=ENGINE_LABEL)
    for prep in preps:
        result.images.append(finish_image(prep, lines_by_image.get(prep.image_id)))
    errors = [v.error for v in result.images if v.error]
    if errors and not result.ok:
        result.error = errors[0]
    return result


# ---------- single-shot convenience entry points ----------


def analyse_image(path: Path, lines=None, *, image_id: int = 0, filename: str = "") -> ImageVision:
    """Analyse one image in a single call (used by the vision test endpoint)."""
    return finish_image(prepare_image(path, image_id=image_id, filename=filename), lines)


def analyse_all(images, lines_by_image: dict[int, list] | None = None) -> VisionResult:
    """Run both phases over every attached image in one call."""
    if not available():
        return VisionResult(engine=ENGINE_LABEL, error=availability_reason())
    return finish_all(prepare_all(images), lines_by_image)


def analyse_bytes(data: bytes, lines=None, *, filename: str = "") -> tuple[ImageVision, ImagePrep]:
    """Single-shot analysis of an in-memory upload; returns the reading and its pixel metrics."""
    prep = prepare_bytes(data, filename=filename)
    return finish_image(prep, lines), prep


# ---------- facts handed to the rest of the pipeline ----------


def fact_rows(result: VisionResult) -> list[dict]:
    """Flatten every region into a persistence-ready row (one row per observation)."""
    rows: list[dict] = []
    for vision in result.images:
        for region in vision.regions:
            rows.append(
                {
                    "image_id": vision.image_id,
                    "kind": region.kind,
                    "label": region.label,
                    "bbox": bbox_str(region.bbox),
                    "text": region.text,
                    "confidence": region.confidence,
                    "prominence": region.prominence,
                    "contrast": region.contrast,
                    "sharpness": region.sharpness,
                    "text_density": region.text_density,
                    "engine": result.engine,
                    "note": region.note,
                }
            )
    return rows


def hero_by_image(result: VisionResult) -> dict[int, ImageVision]:
    return {v.image_id: v for v in result.images if v.hero_bbox is not None}


def hero_note_for(image_id: int | None, bbox: tuple[int, int, int, int] | None, result: VisionResult) -> str:
    """A traceability note when a field's evidence sits inside the most prominent block."""
    if image_id is None or bbox is None:
        return ""
    vision = hero_by_image(result).get(image_id)
    if vision is None or vision.hero_bbox is None:
        return ""
    hx1, hy1, hx2, hy2 = vision.hero_bbox
    x1, y1, x2, y2 = bbox
    ix = max(0, min(hx2, x2) - max(hx1, x1))
    iy = max(0, min(hy2, y2) - max(hy1, y1))
    if ix * iy <= 0:
        return ""
    field_area = max(1, (x2 - x1) * (y2 - y1))
    if ix * iy / field_area < 0.6:
        return ""
    return f"Read from the most prominent printed block on this face ({ENGINE_LABEL})."


def panel_facts(result: VisionResult) -> dict[int, dict]:
    """Panel observations keyed by image id, attached to the placement evidence as raw facts."""
    facts: dict[int, dict] = {}
    for vision in result.images:
        panel = next((r for r in vision.regions if r.kind == KIND_PANEL), None)
        facts[vision.image_id] = {
            "legibility": vision.legibility,
            "hero_bbox": bbox_str(vision.hero_bbox),
            "hero_prominence": vision.hero_prominence,
            "principal_region": bbox_str(panel.bbox) if panel else "",
            "principal_region_density": panel.text_density if panel else 0.0,
            "engine": result.engine,
        }
    return facts


def summarise(result: VisionResult, elapsed_ms: int | None = None) -> str:
    """One honest line for the inspection record / notes field."""
    if result.error:
        return f"On-device vision: {result.error}"
    heroes = [v for v in result.images if v.hero_bbox is not None]
    readable = [v.legibility for v in result.images if v.legibility]
    parts = [
        f"{len(result.images)} image(s) analysed, {result.region_count} region(s) retained",
        f"{len(heroes)} prominent text block(s) localised",
    ]
    if readable:
        parts.append("readability " + ", ".join(sorted(set(readable))))
    if elapsed_ms is not None:
        parts.append(f"{elapsed_ms} ms")
    return f"{ENGINE_LABEL} — " + "; ".join(parts) + "."


def status_for(result: VisionResult, provider_configured: bool, provider_used: bool, provider_error: str = "") -> str:
    """The single honest status the UI, the report and PRO all read."""
    if result.error and not result.ok:
        return STATUS_FAILED
    if provider_used:
        return STATUS_COMPLETED_WITH_PROVIDER
    if provider_error:
        return STATUS_PROVIDER_FALLBACK
    if provider_configured:
        return STATUS_COMPLETED
    return STATUS_PROVIDER_NOT_CONFIGURED


def status_text(status: str) -> str:
    return {
        STATUS_COMPLETED: "On-device vision completed; the configured provider returned no usable reading.",
        STATUS_COMPLETED_WITH_PROVIDER: "On-device vision completed and the vision provider contributed readings.",
        STATUS_PROVIDER_FALLBACK: "Vision provider failed — the result rests on on-device vision and OCR only.",
        STATUS_PROVIDER_NOT_CONFIGURED: "On-device vision completed; no external vision provider is configured.",
        STATUS_FAILED: "On-device vision could not run — the result rests on OCR only.",
    }.get(status, status)
