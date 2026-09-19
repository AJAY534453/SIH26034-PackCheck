"""Geometry: skew estimation and deskewing (into new artifacts only)."""
from __future__ import annotations

import cv2
import numpy as np


def estimate_skew_angle(gray: np.ndarray) -> float:
    """Estimate text skew angle in degrees via minAreaRect over binarized text pixels.

    Returns 0.0 when unreliable (few contours, degenerate boxes, or near-square images).
    """
    try:
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, None, fx=0.5, fy=0.5) if max(gray.shape[:2]) > 1500 else gray
        edges = cv2.Canny(small, 50, 150)
        coords = cv2.findNonZero(edges)
        if coords is None or len(coords) < 200:
            return 0.0
        rect = cv2.minAreaRect(coords)
        (cx, cy), (w, h), angle = rect
        if w == 0 or h == 0:
            return 0.0
        # Normalize OpenCV's angle convention to [-45, 45].
        if w < h:
            angle = angle - 90
        if angle < -45:
            angle += 90
        if abs(angle) < 0.5 or abs(angle) > 15:
            return 0.0
        return float(angle)
    except Exception:
        return 0.0


def deskew_image(img: np.ndarray, max_correction_deg: float = 15.0) -> np.ndarray:
    """Rotate the image by the negative of the estimated skew. Input is not modified."""
    angle = estimate_skew_angle(img)
    if angle == 0.0:
        return img.copy()
    angle = max(-max_correction_deg, min(max_correction_deg, angle))
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    m = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    n_w, n_h = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += n_w / 2 - center[0]
    m[1, 2] += n_h / 2 - center[1]
    border = cv2.BORDER_CONSTANT if img.ndim == 2 else cv2.BORDER_REPLICATE
    return cv2.warpAffine(img, m, (n_w, n_h), flags=cv2.INTER_CUBIC, borderMode=border)
