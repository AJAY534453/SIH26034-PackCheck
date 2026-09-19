"""Extracted fields: the winning, human-facing extraction result per field."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.enums import FieldState
from backend.models.user import utcnow


class ExtractedField(Base):
    __tablename__ = "extracted_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)

    field_name: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(24), default=FieldState.DETECTED.value)

    raw_value: Mapped[str] = mapped_column(Text, default="")
    normalized_value: Mapped[str] = mapped_column(Text, default="")  # canonical form, JSON where structured
    display_value: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    source: Mapped[str] = mapped_column(String(64), default="ocr")  # ocr | vision | manual
    source_image_id: Mapped[int | None] = mapped_column(nullable=True)
    source_engine: Mapped[str] = mapped_column(String(32), default="")
    preprocessing_variant: Mapped[str] = mapped_column(String(32), default="original")
    bbox: Mapped[str] = mapped_column(String(120), default="")  # "x1,y1,x2,y2"
    source_text: Mapped[str] = mapped_column(Text, default="")  # raw OCR line(s) backing the value

    extraction_reason: Mapped[str] = mapped_column(Text, default="")
    uncertainty_reason: Mapped[str] = mapped_column(Text, default="")
    conflict_status: Mapped[str] = mapped_column(String(16), default="NONE")  # NONE | CONFLICTING
    manually_corrected: Mapped[bool] = mapped_column(default=False)
    evidence_id: Mapped[int | None] = mapped_column(nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
