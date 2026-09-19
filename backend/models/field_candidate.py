"""Field candidates: every extraction candidate considered for a field, with scores.

Never deleted — even losing candidates are retained so conflicts and audit remain inspectable.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow


class FieldCandidate(Base):
    __tablename__ = "field_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(64), index=True)

    value: Mapped[str] = mapped_column(Text, default="")  # normalized candidate value
    raw_value: Mapped[str] = mapped_column(Text, default="")  # raw OCR text
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)  # extractor composite score

    source_image_id: Mapped[int | None] = mapped_column(nullable=True)
    source_engine: Mapped[str] = mapped_column(String(32), default="")
    variant: Mapped[str] = mapped_column(String(32), default="original")
    bbox: Mapped[str] = mapped_column(String(120), default="")
    source_text: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")  # why scored as it did

    conflict: Mapped[bool] = mapped_column(default=False)
    is_winner: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
