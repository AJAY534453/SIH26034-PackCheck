"""Review actions: the human-in-the-loop record. Original AI output is never erased."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow


class ReviewAction(Base):
    __tablename__ = "review_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)
    field_id: Mapped[int | None] = mapped_column(nullable=True)

    action: Mapped[str] = mapped_column(String(24))  # ACCEPT | REJECT | EDIT_FIELD | ADD_FIELD | NOTE | REPROCESS
    reviewer: Mapped[str] = mapped_column(String(64), default="")
    original_value: Mapped[str] = mapped_column(Text, default="")
    corrected_value: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
