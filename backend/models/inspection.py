"""Inspection: the central aggregate for one package-inspection workflow."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.enums import FinalDecision, InspectionStatus
from backend.models.user import utcnow


class Inspection(Base):
    __tablename__ = "inspections"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # INS-2026-000001
    product_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    # Isolation boundary: set server-side from the creating user's organization. A REGULATED_ENTITY
    # user only ever sees inspections belonging to their own organization.
    organization_id: Mapped[int | None] = mapped_column(nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(24), default=InspectionStatus.CREATED.value)
    # ``final_decision`` is the AUTOMATED (AI/system) preliminary verdict produced by the
    # deterministic rule engine. ``official_decision`` is written ONLY by an authorized human and
    # is the final decision. The two are never conflated in the UI, API or reports.
    final_decision: Mapped[str | None] = mapped_column(String(24), nullable=True)
    official_decision: Mapped[str | None] = mapped_column(String(24), nullable=True)
    finalized_by: Mapped[str] = mapped_column(String(64), default="")
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finalization_remarks: Mapped[str] = mapped_column(Text, default="")
    inspector: Mapped[str] = mapped_column(String(64), default="")  # username of creator/reviewer

    category: Mapped[str] = mapped_column(String(40), default="")
    category_state: Mapped[str] = mapped_column(String(16), default="UNCERTAIN")
    category_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    overall_quality: Mapped[str] = mapped_column(String(16), default="")
    overall_quality_score: Mapped[float] = mapped_column(Float, default=0.0)

    # ---------- physical-scale calibration (Rule 7 font-size checking) ----------
    # A millimetre figure needs a scale. Either the reviewer calibrates (a known length and its
    # pixel span) or the image file's own DPI metadata is trustworthy; otherwise the Rule 7 check
    # reports that measurement is impossible rather than guessing.
    calibration_px_per_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibration_source: Mapped[str] = mapped_column(String(40), default="")
    calibration_note: Mapped[str] = mapped_column(Text, default="")
    # Principal display panel size in millimetres (Rule 7(4) defines the area).
    panel_width_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    panel_height_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    pdp_area_cm2: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 'normal' | 'moulded' — selects the Rule 7 'blown, formed or molded' column.
    packaging_form: Mapped[str] = mapped_column(String(16), default="normal")

    stage_status: Mapped[dict] = mapped_column(JSON, default=dict)  # live pipeline progress
    # Per-stage wall-clock milliseconds for the run that produced the ACTIVE result. Recorded so a
    # slow scan can be diagnosed from the record itself instead of from server logs.
    stage_timings: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(default=0)  # end-to-end pipeline duration
    # ---------- perception provenance ----------
    # Which perception sources actually produced the ACTIVE result. The on-device vision engine
    # always runs; a third-party provider is additive. These values are written from what happened,
    # never from what was configured — the UI, the report and PRO all read them verbatim.
    vision_status: Mapped[str] = mapped_column(String(40), default="")  # COMPLETED | ... (see backend.vision)
    vision_engine: Mapped[str] = mapped_column(String(48), default="")
    vision_note: Mapped[str] = mapped_column(Text, default="")
    provider_status: Mapped[str] = mapped_column(String(40), default="")  # NOT_CONFIGURED | USED | FAILED
    provider_note: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")  # human-readable "reason for status"
    notes: Mapped[str] = mapped_column(Text, default="")

    # ---------- processing provenance / migration ----------
    # Which engine version produced the ACTIVE result. A record analysed by an older engine is
    # listed for reprocessing so corrected logic is never left unapplied to existing data.
    pipeline_version: Mapped[str] = mapped_column(String(48), default="")
    reprocess_count: Mapped[int] = mapped_column(default=0)
    last_reprocessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def set_stage(self, stage: str, state: str) -> None:
        ss = dict(self.stage_status or {})
        ss[stage] = state
        self.stage_status = ss

    @property
    def decision(self) -> FinalDecision | None:
        if not self.final_decision:
            return None
        try:
            return FinalDecision(self.final_decision)
        except ValueError:
            return None
