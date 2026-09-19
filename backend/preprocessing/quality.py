"""Image quality assessment — measures quality facts, never legal conclusions.

Output: per-metric GOOD/ACCEPTABLE/POOR + overall score + GOOD/ACCEPTABLE/POOR/UNUSABLE.
Poor quality routes to preprocessing retries and ultimately NEEDS MANUAL REVIEW — never a legal FAIL.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from backend.preprocessing.geometry import estimate_skew_angle


def assess_quality(img: np.ndarray) -> dict[str, Any]:
    """Compute quality metrics for a BGR image. Returns metrics, values and overall status."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape[:2]

    # --- resolution ---
    if min(h, w) >= 1400:
        resolution = "GOOD"
    elif min(h, w) >= 700:
        resolution = "ACCEPTABLE"
    else:
        resolution = "POOR"

    # --- blur (Laplacian variance, scale-normalized) ---
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    adj = lap_var / max(max(h, w) / 1000.0, 0.4)
    blur = "GOOD" if adj > 120 else ("ACCEPTABLE" if adj > 30 else "POOR")

    # --- brightness (labels are predominantly light; only penalize true dark frames) ---
    mean_v = float(gray.mean())
    brightness = "GOOD" if mean_v >= 80 else ("ACCEPTABLE" if mean_v >= 45 else "POOR")

    # --- contrast (RMS); printed labels on plain background have moderate RMS ---
    rms = float(gray.std())
    contrast = "GOOD" if rms >= 40 else ("ACCEPTABLE" if rms >= 22 else "POOR")

    # --- glare: near-saturated pixels excluding flat/bright label backgrounds.
    # Printed labels are predominantly bright; glare is a LOCAL blowout inside a darker image.
    # If the median brightness is already high (light label stock), saturation is background,
    # not glare.
    median_v = float(np.median(gray))
    bright_frac = float((gray > 245).mean())
    flat_image = median_v > 180  # light label stock: saturation is background, not glare
    glare_frac = 0.0 if flat_image else bright_frac
    glare = "GOOD" if glare_frac < 0.02 else ("ACCEPTABLE" if glare_frac < 0.08 else "POOR")

    # --- exposure extremes (histogram clipping, same label-stock guard) ---
    over = 0.0 if flat_image else bright_frac
    under = float((gray < 5).mean())
    under = float((gray < 5).mean())
    overexposure = "POOR" if over > 0.15 else ("ACCEPTABLE" if over > 0.05 else "GOOD")
    underexposure = "POOR" if under > 0.35 else ("ACCEPTABLE" if under > 0.15 else "GOOD")

    # --- orientation ---
    skew = estimate_skew_angle(gray)
    orientation = "GOOD" if abs(skew) <= 2 else ("ACCEPTABLE" if abs(skew) <= 7 else "POOR")

    # --- compression artifacts (blockiness proxy) ---
    blockiness = _blockiness(gray)
    compression = "GOOD" if blockiness < 2.5 else ("ACCEPTABLE" if blockiness < 6 else "POOR")

    metrics = {
        "resolution": resolution,
        "blur": blur,
        "brightness": brightness,
        "contrast": contrast,
        "glare": glare,
        "overexposure": overexposure,
        "underexposure": underexposure,
        "orientation": orientation,
        "compression": compression,
    }

    weights = {
        "resolution": 1.5,
        "blur": 2.0,
        "brightness": 1.2,
        "contrast": 1.2,
        "glare": 1.5,
        "overexposure": 0.8,
        "underexposure": 0.8,
        "orientation": 1.0,
        "compression": 1.0,
    }
    band_scores = {"GOOD": 1.0, "ACCEPTABLE": 0.6, "POOR": 0.0}
    total_w = sum(weights.values())
    score = sum(band_scores.get(v, 0.0) * weights.get(k, 1.0) for k, v in metrics.items()) / total_w

    # A single POOR metric among soft factors should not sink an otherwise readable image.
    hard_fail = metrics["resolution"] == "POOR" and metrics["blur"] == "POOR"
    poor_count = sum(1 for v in metrics.values() if v == "POOR")
    if hard_fail:
        status = "UNUSABLE"
    elif score >= 0.75 and poor_count <= 1:
        status = "GOOD"
    elif score >= 0.45:
        status = "ACCEPTABLE"
    else:
        status = "POOR"

    return {
        "metrics": metrics,
        "values": {
            "laplacian_variance": round(lap_var, 1),
            "mean_brightness": round(mean_v, 1),
            "rms_contrast": round(rms, 1),
            "glare_fraction": round(glare_frac, 4),
            "overexposure_fraction": round(over, 4),
            "underexposure_fraction": round(under, 4),
            "skew_degrees": round(skew, 2),
            "blockiness": round(blockiness, 2),
            "width": w,
            "height": h,
        },
        "score": round(float(score), 3),
        "status": status,
    }


def _blockiness(gray: np.ndarray) -> float:
    """Estimate JPEG-like blockiness: mean gradient across 8px grid vs off-grid rows/cols."""
    try:
        g = gray.astype(np.float32)
        gx = np.abs(np.diff(g, axis=1)).mean(axis=0)
        gy = np.abs(np.diff(g, axis=0)).mean(axis=1)
        grid = (gx[7::8].mean() + gy[7::8].mean()) / 2.0
        off = (np.delete(gx, np.arange(7, len(gx), 8)).mean() + np.delete(gy, np.arange(7, len(gy), 8)).mean()) / 2.0
        return float(max(0.0, grid - off))
    except Exception:
        return 0.0
