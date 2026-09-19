"""RapidOCR engine — primary local OCR. Models are bundled with the wheel; fully offline."""
from __future__ import annotations

import threading

import numpy as np

from backend.ocr.base import OcrEngine, OcrLine

_lock = threading.Lock()
_engine = None
_init_failed = False


def _get_engine():
    global _engine, _init_failed
    if _engine is not None or _init_failed:
        return _engine
    with _lock:
        if _engine is None and not _init_failed:
            try:
                from rapidocr_onnxruntime import RapidOCR

                _engine = RapidOCR()
            except Exception:
                _init_failed = True
                return None
    return _engine


def reset_engine() -> None:
    """Reset the cached engine (used by tests and after config changes)."""
    global _engine, _init_failed
    with _lock:
        _engine = None
        _init_failed = False


class RapidOcrEngine(OcrEngine):
    name = "rapidocr"

    def is_available(self) -> bool:
        return _get_engine() is not None

    def recognize(self, image: np.ndarray, variant: str = "original") -> list[OcrLine]:
        engine = _get_engine()
        if engine is None or image is None:
            return []
        try:
            result, _ = engine(image)
        except Exception:
            return []
        lines: list[OcrLine] = []
        if not result:
            return lines
        for i, item in enumerate(result):
            try:
                box, text, conf = item[0], str(item[1]), float(item[2])
            except Exception:
                continue
            text = text.strip()
            if not text:
                continue
            xs = [int(p[0]) for p in box]
            ys = [int(p[1]) for p in box]
            bbox = (min(xs), min(ys), max(xs), max(ys))
            lines.append(OcrLine(text=text, confidence=conf, bbox=bbox, engine=self.name, variant=variant))
        return lines
