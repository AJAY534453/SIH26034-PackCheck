"""Per-line OCR results (engine + preprocessing variant aware) — raw, retained for audit."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow


class OcrResult(Base):
    __tablename__ = "ocr_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("inspection_images.id"), index=True)

    engine: Mapped[str] = mapped_column(String(32), default="rapidocr")
    variant: Mapped[str] = mapped_column(String(32), default="original")
    line_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(String(500), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    bbox: Mapped[str] = mapped_column(String(120), default="")  # "x1,y1,x2,y2" pixel coords

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
