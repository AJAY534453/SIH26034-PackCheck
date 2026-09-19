"""Preprocessing variants. Originals are never modified; every variant is a new artifact."""
from __future__ import annotations

import cv2
import numpy as np

from backend.preprocessing.geometry import deskew_image

VARIANT_NAMES = ("original", "upscaled", "clahe", "denoised", "sharpened", "thresholded", "deskewed")


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def build_variant(img: np.ndarray, name: str) -> np.ndarray | None:
    """Build a named preprocessing variant. Returns a NEW image; input is never mutated."""
    try:
        if name == "original":
            return img.copy()
        if name == "upscaled":
            h, w = img.shape[:2]
            if max(h, w) >= 2000:
                return img.copy()
            scale = min(2.0, 2000.0 / max(h, w))
            return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        if name == "clahe":
            gray = to_gray(img)
            clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
            return clahe.apply(gray)
        if name == "denoised":
            return cv2.fastNlMeansDenoisingColored(img, None, 6, 6, 7, 21)
        if name == "sharpened":
            blur = cv2.GaussianBlur(img, (0, 0), 3)
            return cv2.addWeighted(img, 1.6, blur, -0.6, 0)
        if name == "thresholded":
            gray = to_gray(img)
            gray = cv2.bilateralFilter(gray, 7, 40, 40)
            _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            return th
        if name == "deskewed":
            return deskew_image(img)
    except Exception:
        return None
    return None


def build_variants(img: np.ndarray, max_variants: int = 4) -> dict[str, np.ndarray]:
    """Build the 'original' variant plus adaptive enhancement variants for OCR.

    Variants are ordered by expected usefulness for difficult package photos.
    """
    variants: dict[str, np.ndarray] = {"original": img.copy()}
    for name in ("upscaled", "clahe", "sharpened", "thresholded", "denoised", "deskewed"):
        if len(variants) >= max_variants:
            break
        out = build_variant(img, name)
        if out is not None:
            variants[name] = out
    return variants


# ---------------------------------------------------------------------------
# Two-purpose enhancement.
#
# (A) FULL-IMAGE enhancement keeps the photograph LOOKING like the photograph — it corrects
#     lighting, contrast and sharpness and cleans noise, but never discards colour or texture, so
#     the result can still be used as visual evidence.
# (B) TEXT-FOCUSED enhancement deliberately throws colour away and binarizes, because that is what
#     makes small printed text OCR-readable.
#
# Both return the ordered list of operations applied, so the UI can say exactly what was done and
# the pipeline stays auditable. The input array is never mutated.
# ---------------------------------------------------------------------------

_FULL_MAX_DIM = 1600
_TEXT_MAX_DIM = 1800


def _adaptive_gamma(img: np.ndarray, target_mean: float = 140.0) -> tuple[np.ndarray, str | None]:
    """Normalise overall exposure toward a legible mean brightness (bounded gamma correction)."""
    gray = to_gray(img)
    mean = float(gray.mean())
    if 90.0 <= mean <= 200.0:
        return img, None
    import math

    gamma = math.log(max(target_mean, 1.0) / 255.0) / math.log(max(mean, 1.0) / 255.0)
    gamma = max(0.5, min(2.0, gamma))  # bounded: never a wild exposure rewrite
    lut = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)]).astype("uint8")
    return cv2.LUT(img, lut), f"exposure (gamma {gamma:.2f}, mean {mean:.0f})"


def enhance_full_image(img: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Mode A — improve the WHOLE image while preserving its visual information.

    Upscale (when small) -> light denoise -> exposure -> local contrast -> sharpen -> deskew.
    Colour is preserved throughout, and every step is bounded so the original appearance survives.
    """
    ops: list[str] = []
    out = img.copy()
    h, w = out.shape[:2]
    if max(h, w) < _FULL_MAX_DIM:
        scale = min(2.0, _FULL_MAX_DIM / max(h, w))
        out = cv2.resize(out, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        ops.append(f"upscale x{scale:.2f}")
    out = cv2.fastNlMeansDenoisingColored(out, None, 4, 4, 7, 21)
    ops.append("noise reduction (light)")
    out, gamma_op = _adaptive_gamma(out)
    if gamma_op:
        ops.append(gamma_op)
    lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    out = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
    ops.append("local contrast (CLAHE on L, colour preserved)")
    blur = cv2.GaussianBlur(out, (0, 0), 2.0)
    out = cv2.addWeighted(out, 1.35, blur, -0.35, 0)
    ops.append("sharpening (unsharp mask)")
    out = deskew_image(out)
    ops.append("perspective/skew correction")
    return out, ops


def enhance_text_image(img: np.ndarray) -> tuple[np.ndarray, list[str], float]:
    """Mode B — preprocess for OCR. Returns (image, operations, upscale_factor).

    Grayscale -> upscale -> edge-preserving denoise -> local contrast -> deskew -> sharpen ->
    adaptive threshold. The upscale factor is returned so OCR bounding boxes can be mapped back
    to the ORIGINAL image's coordinates (evidence stays attached to the original).
    """
    ops: list[str] = ["grayscale"]
    gray = to_gray(img)
    scale = 1.0
    h, w = gray.shape[:2]
    if max(h, w) < _TEXT_MAX_DIM:
        scale = min(2.0, _TEXT_MAX_DIM / max(h, w))
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        ops.append(f"upscale x{scale:.2f}")
    gray = cv2.bilateralFilter(gray, 7, 45, 45)
    ops.append("denoise (edge-preserving bilateral)")
    gray = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
    ops.append("contrast (CLAHE)")
    deskewed = deskew_image(gray)
    if deskewed.shape != gray.shape:
        gray = to_gray(deskewed)
        ops.append("skew correction")
    blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
    gray = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)
    ops.append("sharpening")
    gray = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
    )
    ops.append("adaptive threshold (binarisation)")
    return gray, ops, scale
