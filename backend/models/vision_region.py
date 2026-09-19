"""Persisted on-device vision observations.

One row per REGION or MEASUREMENT produced by :mod:`backend.vision.engine` for one image. Rows
carry only what was measured from the pixels — a bbox, a prominence score, contrast, sharpness,
ink density, the recognised text that happens to sit inside the region, and a human-readable
note. No declaration value is ever stored here: the vision engine observes the image, the
extraction pipeline reads values, and the rule engine decides. Keeping them apart is what stops a
visual impression from silently becoming a legal fact.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow


class VisionRegion(Base):
    __tablename__ = "vision_regions"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)
    image_id: Mapped[int | None] = mapped_column(ForeignKey("inspection_images.id"), nullable=True, index=True)

    # PANEL | TEXT_BLOCK | SYMBOL | LEGIBILITY
    kind: Mapped[str] = mapped_column(String(24), default="")
    label: Mapped[str] = mapped_column(String(160), default="")
    bbox: Mapped[str] = mapped_column(String(120), default="")  # "x1,y1,x2,y2" in ORIGINAL pixels

    text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    prominence: Mapped[float] = mapped_column(Float, default=0.0)
    contrast: Mapped[float] = mapped_column(Float, default=0.0)
    sharpness: Mapped[float] = mapped_column(Float, default=0.0)
    text_density: Mapped[float] = mapped_column(Float, default=0.0)

    engine: Mapped[str] = mapped_column(String(48), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
