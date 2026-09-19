"""Persisted image-analysis result: enhancement + OCR + structured extraction.

The original image is stored byte-for-byte in the immutable `originals` store; the two enhanced
representations are derived artifacts in `processed/`. This row keeps the OCR text, per-line
confidence and structured fields ASSOCIATED with the original, so the result survives a page
reload and can be traced back to the exact evidence.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow


class ImageAnalysis(Base):
    __tablename__ = "image_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    original_filename: Mapped[str] = mapped_column(String(255), default="")
    original_stored: Mapped[str] = mapped_column(String(200), default="")   # originals/<name>
    enhanced_full_stored: Mapped[str] = mapped_column(String(200), default="")  # processed/<name>
    enhanced_text_stored: Mapped[str] = mapped_column(String(200), default="")  # processed/<name>

    # NO_TEXT | LOW_CONFIDENCE | TEXT_EXTRACTED
    state: Mapped[str] = mapped_column(String(24), default="NO_TEXT")
    message: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(16), default="auto")  # auto | full | text

    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    quality_status: Mapped[str] = mapped_column(String(16), default="")
    quality_json: Mapped[str] = mapped_column(Text, default="{}")

    text_present: Mapped[bool] = mapped_column(Boolean, default=False)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    mean_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    engine: Mapped[str] = mapped_column(String(32), default="")

    ocr_json: Mapped[str] = mapped_column(Text, default="[]")        # [{text, confidence, bbox}]
    structured_json: Mapped[str] = mapped_column(Text, default="{}")  # {field: {value, ...}}
    full_ops: Mapped[str] = mapped_column(Text, default="[]")
    text_ops: Mapped[str] = mapped_column(Text, default="[]")
