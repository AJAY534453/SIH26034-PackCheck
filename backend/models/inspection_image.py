"""Uploaded package images with roles, quality metrics and stored files."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.enums import QualityStatus
from backend.models.user import utcnow


class InspectionImage(Base):
    __tablename__ = "inspection_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)

    role: Mapped[str] = mapped_column(String(24), default="ADDITIONAL_EVIDENCE")
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    stored_filename: Mapped[str] = mapped_column(String(255), default="")  # UUID name in storage/originals
    mime_type: Mapped[str] = mapped_column(String(64), default="")
    size_bytes: Mapped[int] = mapped_column(default=0)

    width: Mapped[int] = mapped_column(default=0)
    height: Mapped[int] = mapped_column(default=0)
    phash: Mapped[str] = mapped_column(String(32), default="")  # perceptual hash for duplicate detection

    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    quality_status: Mapped[str] = mapped_column(String(16), default="")
    quality_metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # resolution/blur/brightness/glare/...

    ocr_text: Mapped[str] = mapped_column(Text, default="")  # full-frame OCR text (kept for audit)
    ocr_line_count: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
