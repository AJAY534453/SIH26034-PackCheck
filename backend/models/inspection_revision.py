"""Immutable record of a reprocessing / migration event on one inspection.

The ACTIVE result of an inspection is always the corrected one; the analysis it replaced is kept
here so the previous result stays recoverable and the change is auditable. Each row records:

* **who** ran the migration and **why** (actor, reason),
* **which engine and rule set** produced the corrected result (engine_version, rule_fingerprint),
* the complete **before** and **after** snapshots (fields, rules, violations, score, verdict),
* a structured **diff** so the quality-control view can show BEFORE vs AFTER without recomputing.

Nothing here is inferred: the snapshots are read from the persisted rows immediately before and
after the pipeline run.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow

REVISION_KIND_REPROCESS = "REPROCESS"   # an authorized user re-ran the pipeline on one record
REVISION_KIND_MIGRATION = "MIGRATION"   # a bulk migration run re-ran the pipeline over many records


class InspectionRevision(Base):
    __tablename__ = "inspection_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(16), default=REVISION_KIND_REPROCESS)

    actor: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    engine_version: Mapped[str] = mapped_column(String(48), default="")
    rule_fingerprint: Mapped[str] = mapped_column(String(16), default="")

    before_json: Mapped[str] = mapped_column(Text, default="{}")
    after_json: Mapped[str] = mapped_column(Text, default="{}")
    changes_json: Mapped[str] = mapped_column(Text, default="{}")

    # Counts are denormalised for fast list rendering; the JSON remains the full record.
    fields_changed: Mapped[int] = mapped_column(Integer, default=0)
    rules_changed: Mapped[int] = mapped_column(Integer, default=0)
    score_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict_before: Mapped[str] = mapped_column(String(32), default="")
    verdict_after: Mapped[str] = mapped_column(String(32), default="")
    review_status_before: Mapped[str] = mapped_column(String(24), default="")
    review_status_after: Mapped[str] = mapped_column(String(24), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
