"""Image service: upload validation, original preservation, quality, duplicates.

Originals are saved byte-for-byte with UUID names and never modified afterwards.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from backend.config import settings
from backend.preprocessing.quality import assess_quality


class UploadValidationError(Exception):
    pass


def _phash(img: np.ndarray) -> str:
    """Simple perceptual hash (8x8 DCT-less average hash) for duplicate detection."""
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        small = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
        avg = small.mean()
        bits = (small > avg).flatten()
        return "".join("1" if b else "0" for b in bits)
    except Exception:
        return ""


def validate_and_store(
    data: bytes, original_filename: str, existing_phashes: list[str], kind: str = "originals"
) -> dict:
    """Validate an uploaded image and store the ORIGINAL byte-for-byte.

    `kind` selects the immutable store: "originals" for package photographs (the inspection
    evidence chain) or "bills" for bill/receipt images. They are kept apart so a bill can never
    be mistaken for package evidence, and both are served only through the protected file route.

    Raises UploadValidationError with a useful message on any problem.
    Returns {stored_filename, path, width, height, phash, mime_type, size_bytes}.
    """
    if kind not in ("originals", "bills"):
        raise UploadValidationError(f"Unknown storage kind '{kind}'.")
    # --- extension ---
    ext = Path(original_filename or "").suffix.lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise UploadValidationError(
            f"Unsupported file type '{ext or 'unknown'}'. Allowed: JPG, JPEG, PNG, WEBP."
        )
    # --- size ---
    if len(data) == 0:
        raise UploadValidationError("Uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise UploadValidationError(
            f"File too large ({len(data) / 1024 / 1024:.1f} MB). Maximum is {settings.MAX_UPLOAD_MB} MB."
        )
    # --- decode (catches corruption + true format) ---
    try:
        pil = Image.open(__import__("io").BytesIO(data))
        pil.load()
        mime = pil.format or ""
    except Exception:
        raise UploadValidationError("File could not be decoded as an image (corrupt or unsupported).")
    if mime.upper() not in ("JPEG", "PNG", "WEBP"):
        raise UploadValidationError(f"Actual image format '{mime}' is not supported.")
    try:
        arr = np.array(pil.convert("RGB"))
        img = arr[:, :, ::-1].copy()  # RGB -> BGR
    except Exception:
        raise UploadValidationError("Image could not be processed after decoding.")
    h, w = img.shape[:2]
    if w < 50 or h < 50:
        raise UploadValidationError(f"Image too small ({w}x{h}). Minimum is 50x50 pixels.")
    if max(h, w) > 8000:
        raise UploadValidationError(f"Image too large ({w}x{h}). Maximum dimension is 8000 pixels.")

    # --- duplicate detection ---
    ph = _phash(img)
    if ph and ph in existing_phashes:
        raise UploadValidationError("This image appears to be a duplicate of an already-uploaded image.")

    # --- store original byte-for-byte ---
    settings.ensure_dirs()
    stored_name = f"{uuid.uuid4().hex}{ext}"
    out_path = settings.STORAGE_DIR / kind / stored_name
    out_path.write_bytes(data)  # exact bytes, no re-encoding

    return {
        "stored_filename": stored_name,
        "path": out_path,
        "width": w,
        "height": h,
        "phash": ph,
        "mime_type": mime.lower(),
        "size_bytes": len(data),
    }


def assess_image_quality(path: Path) -> dict:
    """Assess quality of a stored image file."""
    img = cv2.imread(str(path))
    if img is None:
        return {"metrics": {}, "values": {}, "score": 0.0, "status": "UNUSABLE"}
    return assess_quality(img)


def read_image(path: Path) -> np.ndarray | None:
    return cv2.imread(str(path))
