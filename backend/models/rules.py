"""Rules and rule versions: the legal knowledge base. Rules are DATA, versioned, auditable.

Historical evaluations pin the exact version used — later edits never rewrite history.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.enums import RuleApplicability
from backend.models.user import utcnow


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # e.g. LM-PCR-6A
    rule_number: Mapped[str] = mapped_column(String(16))  # e.g. "6(1)(a)"
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    source_reference: Mapped[str] = mapped_column(String(300), default="")  # official DoCA/LM source
    current_version: Mapped[int] = mapped_column(default=1)
    applicability: Mapped[str] = mapped_column(String(16), default=RuleApplicability.MANDATORY.value)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    # Relative weight this requirement carries in the compliance percentage, and whether a
    # confirmed failure of it is a critical non-compliance. BOTH come from the rule definition
    # (backend/rules/definitions/*.json) — never from code, so a regulatory re-prioritisation is
    # a data edit. NULL means "not declared": the engine then falls back to its derived default.
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id_fk: Mapped[int] = mapped_column(ForeignKey("rules.id"), index=True)
    version: Mapped[int] = mapped_column(default=1)

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    requirement: Mapped[str] = mapped_column(Text, default="")  # the operative requirement text
    source_reference: Mapped[str] = mapped_column(String(300), default="")
    amendment: Mapped[str] = mapped_column(String(200), default="")
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    check_type: Mapped[str] = mapped_column(String(32), default="")  # deterministic check key for the engine
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # parameters for the check
    applicability: Mapped[dict] = mapped_column(JSON, default=dict)  # category/package/context predicates
    # Scoring data for THIS version (see Rule.weight/critical). Pinned per version so raising a
    # weight creates a new version instead of rewriting how past evaluations were scored.
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
