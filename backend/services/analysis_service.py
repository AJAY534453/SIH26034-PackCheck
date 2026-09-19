"""Image capture -> enhancement -> OCR -> structured extraction.

The module is deliberately split into small, replaceable steps so a better OCR engine or a
computer-vision model can be dropped in without touching the API or the UI:

    decode -> assess quality -> enhance (full + text) -> recognise -> structure

Honesty rules carried over from the inspection pipeline:

* nothing is invented — an image with no legible text returns a ``NO_TEXT`` state and the message
  “No readable text detected in this image.”;
* low-confidence reads are returned as ``LOW_CONFIDENCE`` and their structured values are marked
  UNCERTAIN rather than presented as facts;
* the original image is stored byte-for-byte and every extracted value stays associated with it.
"""
from __future__ import annotations

import io
import json
import uuid

import cv2
import numpy as np
from PIL import Image
from sqlalchemy.orm import Session

from backend import audit
from backend.extraction.engine import pick_winner, run_extraction
from backend.models import ImageAnalysis
from backend.ocr import best_engine_name, recognize
from backend.ocr.base import OcrLine, merge_lines
from backend.preprocessing.enhance import enhance_full_image, enhance_text_image
from backend.preprocessing.quality import assess_quality

# Below this mean OCR confidence the result is offered for review, never asserted.
LOW_CONFIDENCE_MEAN = 0.60
# A line must be at least this confident and contain real characters to count as text.
MIN_LINE_CONFIDENCE = 0.40
MIN_ALNUM_CHARS = 3

STATE_NO_TEXT = "NO_TEXT"
STATE_LOW_CONFIDENCE = "LOW_CONFIDENCE"
STATE_TEXT = "TEXT_EXTRACTED"

NO_TEXT_MESSAGE = "No readable text detected in this image."
LOW_CONFIDENCE_MESSAGE = (
    "Text was recognised, but the reading confidence is low. Treat every value below as a "
    "review candidate and verify it against the package."
)


def decode_image(data: bytes) -> np.ndarray:
    """Decode uploaded bytes to a BGR image. Raises ValueError on anything undecodable."""
    try:
        pil = Image.open(io.BytesIO(data))
        pil.load()
        arr = np.array(pil.convert("RGB"))
        return arr[:, :, ::-1].copy()
    except Exception as exc:  # noqa: BLE001
        raise ValueError("The file could not be decoded as an image.") from exc


def _rescale_line(line: OcrLine, scale: float) -> OcrLine:
    """Map a bbox from the (possibly upscaled) preprocessed image back to the ORIGINAL image."""
    if scale == 1.0:
        return line
    x1, y1, x2, y2 = line.bbox
    return OcrLine(
        text=line.text,
        confidence=line.confidence,
        bbox=(int(x1 / scale), int(y1 / scale), int(x2 / scale) + 1, int(y2 / scale) + 1),
        engine=line.engine,
        variant=line.variant,
    )


def _usable_text(lines: list[OcrLine]) -> tuple[list[OcrLine], float, int]:
    usable = [l for l in lines if l.confidence >= MIN_LINE_CONFIDENCE and any(ch.isalnum() for ch in l.text)]
    chars = sum(sum(ch.isalnum() for ch in l.text) for l in usable)
    mean_conf = (sum(l.confidence for l in usable) / len(usable)) if usable else 0.0
    return usable, round(mean_conf, 3), chars


def _structure(lines: list[OcrLine], *, low_confidence: bool) -> dict[str, dict]:
    """Run the deterministic extractors over the OCR lines and rank the candidates."""
    if not lines:
        return {}
    per_field = run_extraction({0: lines})
    out: dict[str, dict] = {}
    for field, cands in per_field.items():
        winner, conflict = pick_winner(cands)
        if winner is None:
            continue
        value = winner.value if (winner.value or "").strip() else winner.raw_value
        if conflict:
            state = "CONFLICTING"
        elif low_confidence or winner.inferred or winner.role_uncertain or winner.confidence < 0.55:
            state = "UNCERTAIN"
        else:
            state = "DETECTED"
        out[field] = {
            "value": value,
            "raw_value": winner.raw_value,
            "confidence": round(float(winner.confidence), 3),
            "state": state,
            "reason": winner.reason,
            "bbox": list(winner.bbox) if winner.bbox else None,
        }
    return out


def analyze_image(image: np.ndarray, *, mode: str = "auto") -> dict:
    """Analyse an image. Returns the full result dict (no persistence, no side effects)."""
    quality = assess_quality(image)
    full_img, full_ops = enhance_full_image(image)
    text_img, text_ops, scale = enhance_text_image(image)

    # OCR both the untouched image and the text-optimised image, then merge in ORIGINAL
    # coordinates: the original keeps provenance, the text variant recovers small print.
    lines_original = recognize(image, "original")
    lines_text = [_rescale_line(l, scale) for l in recognize(text_img, "text-preprocessed")]
    lines = merge_lines(lines_original, lines_text) if (lines_original and lines_text) else (lines_original or lines_text)

    usable, mean_conf, alnum = _usable_text(lines)
    text_present = bool(usable) and mean_conf >= MIN_LINE_CONFIDENCE and alnum >= MIN_ALNUM_CHARS

    if not text_present:
        state, message = STATE_NO_TEXT, NO_TEXT_MESSAGE
    elif mean_conf < LOW_CONFIDENCE_MEAN:
        state, message = STATE_LOW_CONFIDENCE, LOW_CONFIDENCE_MESSAGE
    else:
        state = STATE_TEXT
        message = f"Extracted {len(usable)} text line(s)."

    structured = _structure(lines, low_confidence=(state != STATE_TEXT)) if text_present else {}

    return {
        "mode": mode,
        "quality": quality,
        "text_present": text_present,
        "state": state,
        "message": message,
        "engine": best_engine_name(),
        "line_count": len(usable),
        "mean_confidence": mean_conf,
        "full_enhancement": {"ops": full_ops},
        "text_enhancement": {"ops": text_ops},
        "ocr_lines": [
            {"text": l.text, "confidence": round(float(l.confidence), 3), "bbox": list(l.bbox), "variant": l.variant}
            for l in sorted(usable, key=lambda x: (x.bbox[1], x.bbox[0]))
        ],
        "raw_text": "\n".join(l.text for l in sorted(usable, key=lambda x: (x.bbox[1], x.bbox[0]))),
        "structured": structured,
        "_images": {"full": full_img, "text": text_img},
    }


def _write_png(image: np.ndarray, channel: str) -> str:
    """Persist an enhanced artifact under the immutable processed store."""
    from backend.config import settings

    settings.ensure_dirs()
    name = f"{channel}-{uuid.uuid4().hex}.png"
    cv2.imwrite(str(settings.STORAGE_DIR / "processed" / name), image)
    return name


def analyze_and_persist(
    db: Session,
    *,
    data: bytes,
    filename: str,
    created_by: str,
    organization_id: int | None = None,
    mode: str = "auto",
) -> ImageAnalysis:
    """Analyse, store the original + derived artifacts, and record the result."""
    from backend.services.image_service import validate_and_store

    image = decode_image(data)
    result = analyze_image(image, mode=mode)
    # Store the original byte-for-byte (no re-encoding) so the evidence is the true input.
    info = validate_and_store(data, filename or "capture.png", [], kind="originals")
    full_name = _write_png(result["_images"]["full"], "enhanced-full")
    text_name = _write_png(result["_images"]["text"], "enhanced-text")

    row = ImageAnalysis(
        created_by=created_by,
        organization_id=organization_id,
        original_filename=filename or "capture.png",
        original_stored=info["stored_filename"],
        enhanced_full_stored=full_name,
        enhanced_text_stored=text_name,
        state=result["state"],
        message=result["message"],
        mode=result["mode"],
        quality_score=float(result["quality"].get("score", 0.0)),
        quality_status=str(result["quality"].get("status", "")),
        quality_json=json.dumps(result["quality"]),
        text_present=result["text_present"],
        line_count=result["line_count"],
        mean_confidence=result["mean_confidence"],
        engine=result["engine"],
        ocr_json=json.dumps(result["ocr_lines"], ensure_ascii=False),
        structured_json=json.dumps(result["structured"], ensure_ascii=False),
        full_ops=json.dumps(result["full_enhancement"]["ops"]),
        text_ops=json.dumps(result["text_enhancement"]["ops"]),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit.log_action(
        created_by, "image_analysis",
        after=f"{row.state} lines={row.line_count} mean_conf={row.mean_confidence}",
    )
    return row


def analysis_view(row: ImageAnalysis) -> dict:
    """Serialize an analysis row, including URLs for the original and both enhancements."""
    def _load(value: str, fallback):
        try:
            return json.loads(value or "")
        except (json.JSONDecodeError, TypeError):
            return fallback

    return {
        "id": row.id,
        "created_by": row.created_by,
        "created_at": str(row.created_at),
        "original_filename": row.original_filename,
        "original_url": f"/files/originals/{row.original_stored}",
        "enhanced_full_url": f"/files/processed/{row.enhanced_full_stored}",
        "enhanced_text_url": f"/files/processed/{row.enhanced_text_stored}",
        "state": row.state,
        "message": row.message,
        "mode": row.mode,
        "quality": _load(row.quality_json, {}),
        "quality_score": row.quality_score,
        "quality_status": row.quality_status,
        "text_present": row.text_present,
        "line_count": row.line_count,
        "mean_confidence": row.mean_confidence,
        "engine": row.engine,
        "ocr_lines": _load(row.ocr_json, []),
        "structured": _load(row.structured_json, {}),
        "raw_text": "\n".join(l.get("text", "") for l in _load(row.ocr_json, [])),
        "full_ops": _load(row.full_ops, []),
        "text_ops": _load(row.text_ops, []),
    }
