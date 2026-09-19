"""Evidence records: links between results and the artifacts that back them.

Every evidence row points only to files that actually exist in storage/ (originals, processed
variants, crops). Never fabricated.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)

    kind: Mapped[str] = mapped_column(String(32), default="crop")  # original | processed | crop
    field_name: Mapped[str] = mapped_column(String(64), default="")
    related_type: Mapped[str] = mapped_column(String(32), default="field")  # field | rule | violation | image
    related_id: Mapped[int | None] = mapped_column(nullable=True)

    image_id: Mapped[int | None] = mapped_column(nullable=True)
    bbox: Mapped[str] = mapped_column(String(120), default="")
    stored_filename: Mapped[str] = mapped_column(String(255), default="")  # crop/processed artifact
    original_filename: Mapped[str] = mapped_column(String(255), default="")

    raw_text: Mapped[str] = mapped_column(Text, default="")
    normalized_value: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    extraction_method: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
