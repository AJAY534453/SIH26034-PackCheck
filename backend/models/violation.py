"""Violations: derived ONLY from rule evaluations that failed with sufficient evidence."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.enums import Severity, ViolationStatus
from backend.models.user import utcnow


class Violation(Base):
    __tablename__ = "violations"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)
    rule_evaluation_id: Mapped[int | None] = mapped_column(nullable=True)
    rule_number: Mapped[str] = mapped_column(String(32), default="")

    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    observed: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default=Severity.MEDIUM.value)
    status: Mapped[str] = mapped_column(String(16), default=ViolationStatus.OPEN.value)

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
