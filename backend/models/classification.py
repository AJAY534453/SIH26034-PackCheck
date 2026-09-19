"""Product classification outcome per inspection, with all contributing evidence signals."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow


class Classification(Base):
    __tablename__ = "classifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)

    category: Mapped[str] = mapped_column(String(40), default="OTHER")
    state: Mapped[str] = mapped_column(String(16), default="UNCERTAIN")  # DETECTED | CONFLICTING | UNCERTAIN
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    signals: Mapped[str] = mapped_column(String(500), default="")  # evidence signals, semicolon-separated
    confirmed_by: Mapped[str] = mapped_column(String(64), default="")  # reviewer who confirmed/changed category
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
