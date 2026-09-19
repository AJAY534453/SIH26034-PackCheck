"""OCR service facade. Chooses an available engine, degrades safely, never fabricates text."""
from __future__ import annotations

import numpy as np

from backend.ocr.base import OcrEngine, OcrLine, merge_lines
from backend.ocr.rapidocr_engine import RapidOcrEngine

_engines: list[OcrEngine] = [RapidOcrEngine()]


def get_engines() -> list[OcrEngine]:
    return [e for e in _engines if e.is_available()]


def ocr_available() -> bool:
    return bool(get_engines())


def recognize(image: np.ndarray, variant: str = "original") -> list[OcrLine]:
    """Run OCR with the first available engine. Empty list means: nothing recognized (never fake text)."""
    for engine in get_engines():
        try:
            lines = engine.recognize(image, variant=variant)
            if lines:
                return lines
        except Exception:
            continue
    return []


def _rescale_line(line: OcrLine, sx: float, sy: float, new_variant: str) -> OcrLine:
    """Rescale a line's bbox back to original-image coordinates (rounded outward)."""
    x1, y1, x2, y2 = line.bbox
    return OcrLine(
        text=line.text,
        confidence=line.confidence,
        bbox=(
            max(0, int(x1 / sx)),
            max(0, int(y1 / sy)),
            int(x2 / sx) + 1,
            int(y2 / sy) + 1,
        ),
        engine=line.engine,
        variant=new_variant,
    )


def recognize_multivariant(image: np.ndarray, variants: dict[str, np.ndarray], max_variants: int = 2) -> list[OcrLine]:
    """OCR the original plus selected variants; merge results by bbox IoU in ORIGINAL coordinates.

    Variant results are rescaled to original-image coordinates before merging so bounding
    boxes remain valid for evidence crops. Provenance (variant name) is preserved.
    """
    h0, w0 = image.shape[:2]
    results: list[OcrLine] = []
    names = list(variants.keys())[:max_variants]
    if "original" in variants and "original" not in names:
        names.insert(0, "original")

    for name in names:
        img = variants[name]
        lines = recognize(img, variant=name)
        if not lines:
            continue
        if name == "original":
            rescaled = lines
        else:
            vh, vw = img.shape[:2]
            sx, sy = vw / w0, vh / h0
            rescaled = [_rescale_line(l, sx, sy, name) for l in lines]
        if not results:
            results = rescaled
        else:
            results = merge_lines(results, rescaled)
    return results


def best_engine_name() -> str:
    engines = get_engines()
    return engines[0].name if engines else "none"
