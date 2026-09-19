"""OCR abstraction — engines are swappable; the pipeline never depends on one implementation."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OcrLine:
    """One recognized text line with position and provenance."""

    text: str
    confidence: float  # 0..1, engine-reported (never invented)
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2 in pixels of the image OCR'd
    engine: str
    variant: str  # preprocessing variant name

    @property
    def bbox_str(self) -> str:
        return f"{self.bbox[0]},{self.bbox[1]},{self.bbox[2]},{self.bbox[3]}"


class OcrEngine(ABC):
    """Interface for OCR engines. `available` must reflect reality, never aspiration."""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def recognize(self, image, variant: str = "original") -> list[OcrLine]:
        """Recognize text lines in a BGR/grayscale numpy image."""


def _digit_signature(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _code_like_token(text: str) -> bool:
    """A short, space-free alphanumeric token — the shape of an inkjet code, MRP amount, batch
    code or date code, i.e. exactly the text where per-character OCR reads genuinely differ."""
    t = text.strip()
    return 0 < len(t) <= 14 and " " not in t and any(ch.isdigit() for ch in t)


def merge_lines(primary: list[OcrLine], secondary: list[OcrLine], iou_threshold: float = 0.5) -> list[OcrLine]:
    """Merge two OCR line sets by bbox IoU.

    Of an overlapping pair the higher-confidence read is kept. However, when two reads of the same
    region DISAGREE in their digits and both are code-like tokens (e.g. one pass reads the inkjet
    date code as `040225` and another as `04702725`), they are not the same observation: the other
    read is retained as an additional `+alt` observation so extraction can rank the candidates
    instead of the disagreement being silently discarded. The raw reads are never rewritten.
    """
    def iou(a: OcrLine, b: OcrLine) -> float:
        ax1, ay1, ax2, ay2 = a.bbox
        bx1, by1, bx2, by2 = b.bbox
        ix = max(0, min(ax2, bx2) - max(ax1, bx1))
        iy = max(0, min(ay2, by2) - max(ay1, by1))
        inter = ix * iy
        if inter == 0:
            return 0.0
        area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
        area_b = max(1, (bx2 - bx1) * (by2 - by1))
        return inter / float(area_a + area_b - inter)

    merged: list[OcrLine] = list(primary)
    for line in secondary:
        overlapped = False
        for i, keep in enumerate(merged):
            if iou(line, keep) >= iou_threshold:
                overlapped = True
                winner, loser = (line, keep) if line.confidence > keep.confidence else (keep, line)
                merged[i] = winner
                if (
                    winner.text.strip() != loser.text.strip()
                    and _code_like_token(winner.text)
                    and _code_like_token(loser.text)
                    and _digit_signature(winner.text) != _digit_signature(loser.text)
                ):
                    variant = loser.variant if loser.variant.endswith("+alt") else loser.variant + "+alt"
                    alt = OcrLine(
                        text=loser.text,
                        confidence=loser.confidence,
                        bbox=loser.bbox,
                        engine=loser.engine,
                        variant=variant,
                    )
                    if not any(a.bbox == alt.bbox and a.text == alt.text for a in merged):
                        merged.append(alt)
                break
        if not overlapped:
            merged.append(line)
    return merged
