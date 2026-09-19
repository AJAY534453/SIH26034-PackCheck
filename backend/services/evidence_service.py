"""Evidence service: crops, provenance records. Only references artifacts that exist."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Evidence


def save_crop(image_path: Path, bbox: tuple[int, int, int, int], stored_name: str) -> str | None:
    """Crop a bbox region from an image into storage/crops. Returns stored filename or None."""
    try:
        img = cv2.imread(str(image_path))
        if img is None:
            return None
        h, w = img.shape[:2]
        x1, y1, x2, y2 = bbox
        pad = 8
        x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
        x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
        if x2 - x1 < 4 or y2 - y1 < 4:
            return None
        crop = img[y1:y2, x1:x2]
        out = settings.STORAGE_DIR / "crops" / stored_name
        cv2.imwrite(str(out), crop)
        return out.name
    except Exception:
        return None


def create_evidence(
    db: Session,
    inspection_id: int,
    kind: str,
    field_name: str,
    related_type: str,
    related_id: int | None,
    image_id: int | None,
    bbox: str,
    stored_filename: str,
    original_filename: str,
    raw_text: str,
    normalized_value: str,
    confidence: float,
    extraction_method: str,
    note: str = "",
) -> Evidence:
    ev = Evidence(
        inspection_id=inspection_id,
        kind=kind,
        field_name=field_name,
        related_type=related_type,
        related_id=related_id,
        image_id=image_id,
        bbox=bbox,
        stored_filename=stored_filename,
        original_filename=original_filename,
        raw_text=raw_text,
        normalized_value=normalized_value,
        confidence=confidence,
        extraction_method=extraction_method,
        note=note,
    )
    db.add(ev)
    db.flush()
    return ev
