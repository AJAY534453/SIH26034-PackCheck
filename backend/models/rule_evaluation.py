"""Rule evaluations: immutable outcomes pinned to the rule version used at evaluation time."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow


class RuleEvaluation(Base):
    __tablename__ = "rule_evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id"))
    rule_version_id: Mapped[int] = mapped_column(ForeignKey("rule_versions.id"))  # version pin — never rewritten

    rule_number: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    # The check implementation that produced this outcome (declaration_present, net_quantity,
    # font_size, manual_only, …). Stored so a later re-decision never has to guess it from the
    # rule number.
    check_type: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16))  # PASS | FAIL | UNCERTAIN | NOT_APPLICABLE
    reason: Mapped[str] = mapped_column(Text, default="")  # human-readable explanation
    observed: Mapped[str] = mapped_column(Text, default="")  # observed value/evidence summary
    expected: Mapped[str] = mapped_column(Text, default="")  # what the rule requires
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_id: Mapped[int | None] = mapped_column(nullable=True)
    critical: Mapped[bool] = mapped_column(default=False)
    # The weight this requirement carried in the score AT THE TIME OF EVALUATION. Pinned here (not
    # read back from the rule library) so editing a rule's weight never retroactively moves a
    # stored scan's percentage.
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Why a NOT_APPLICABLE outcome was reached — "INAPPLICABLE" (out of scope by law, packaging or
    # a confirmed category) or "EVIDENCE_MISSING" (the package may well be in scope but no image
    # evidence established it). Only the first is legitimately excluded from the score; the second
    # counts as unverified, so it can never silently raise a percentage.
    applicability_kind: Mapped[str] = mapped_column(String(16), default="")
    # Structured decision traceability (JSON): for a measurable check such as Rule 7 font size it
    # carries required_mm / detected_mm / the legal reference / the measurements used.
    detail: Mapped[str] = mapped_column(Text, default="{}")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
