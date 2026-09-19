"""Product scan repository entry — one row per inspected product scan.

This is the structured, queryable repository the requirements ask for: every scan keeps the
product identity, the evidence reference, the extracted text, the individual compliance checks
performed, the AI preliminary verdict and score, the human/official final decision, the remarks
and the finalization trail.

Two statuses are deliberately kept apart and can never be confused:

``ai_verdict``      the AUTOMATED preliminary verdict (from the deterministic rule engine),
                    always present after processing;
``official_decision`` set ONLY by an authorized human finalization. Until it is set,
                    ``review_status`` says the result is still preliminary / pending finalization.

History is the ordered set of scans that share a ``product_id``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow

# Review lifecycle of a scan's compliance result.
STATUS_AI_PRELIMINARY = "AI_PRELIMINARY"          # >= threshold, no confirmed failure: preliminary pass
STATUS_PENDING_FINALIZATION = "PENDING_FINALIZATION"  # below threshold or a failure: official sign-off required
STATUS_FINALIZED = "FINALIZED"                    # an authorized human decided

PENDING_MESSAGE = "This compliance result is pending finalization by the higher officials."


class ProductScan(Base):
    __tablename__ = "product_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), unique=True, index=True)
    # Same isolation boundary as the inspection it came from.
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    scan_number: Mapped[str] = mapped_column(String(32), default="")  # inspection number
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    scanned_by: Mapped[str] = mapped_column(String(64), default="")

    # ---------- product identity snapshot (as extracted at scan time) ----------
    product_name: Mapped[str] = mapped_column(String(200), default="")
    brand: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(40), default="")
    category_state: Mapped[str] = mapped_column(String(16), default="UNCERTAIN")
    manufacturer: Mapped[str] = mapped_column(String(300), default="")
    net_quantity: Mapped[str] = mapped_column(String(80), default="")
    mrp: Mapped[str] = mapped_column(String(40), default="")
    batch_lot: Mapped[str] = mapped_column(String(80), default="")

    # ---------- evidence + extracted text ----------
    image_filename: Mapped[str] = mapped_column(String(255), default="")
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    structured_fields: Mapped[str] = mapped_column(Text, default="{}")  # JSON snapshot of the fields

    # ---------- AI compliance review ----------
    compliance_score: Mapped[float] = mapped_column(Float, default=0.0)  # 0-100
    # Share of the applicable, machine-checkable requirements that could actually be DECIDED from
    # the evidence (0-100). Reported next to the compliance score so "we could not read it" is
    # never mistaken for "it half-complies".
    coverage_score: Mapped[float] = mapped_column(Float, default=0.0)
    ai_verdict: Mapped[str] = mapped_column(String(32), default="")  # COMPLIANT | NON_COMPLIANT | NEEDS_MANUAL_REVIEW
    ai_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    recommended_action: Mapped[str] = mapped_column(Text, default="")
    checks_json: Mapped[str] = mapped_column(Text, default="[]")       # per-requirement checks
    violations_json: Mapped[str] = mapped_column(Text, default="[]")   # detected violations
    legal_refs_json: Mapped[str] = mapped_column(Text, default="[]")   # references cited by the review
    rules_snapshot: Mapped[str] = mapped_column(Text, default="[]")    # rule versions used at scan time
    ai_scoring: Mapped[str] = mapped_column(Text, default="{}")         # how the score was computed
    threshold: Mapped[float] = mapped_column(Float, default=85.0)
    coverage_floor: Mapped[float] = mapped_column(Float, default=70.0)

    # ---------- human / official finalization ----------
    review_status: Mapped[str] = mapped_column(String(24), default=STATUS_PENDING_FINALIZATION, index=True)
    official_decision: Mapped[str | None] = mapped_column(String(24), nullable=True)
    remarks: Mapped[str] = mapped_column(Text, default="")
    finalized_by: Mapped[str] = mapped_column(String(64), default="")
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def is_finalized(self) -> bool:
        return self.review_status == STATUS_FINALIZED and bool(self.official_decision)

    @property
    def public_status(self) -> str:
        """What a non-reviewer may be told. Never presents a preliminary result as final."""
        if self.is_finalized:
            return f"Final decision: {self.official_decision}"
        return PENDING_MESSAGE
