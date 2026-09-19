"""Product repository: deduplicated products seen across inspections."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    # A conservative dedupe key: normalized (name + brand + manufacturer). Human-editable.
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    brand: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(40), default="OTHER")
    category_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    category_state: Mapped[str] = mapped_column(String(16), default="UNCERTAIN")
    manufacturer: Mapped[str] = mapped_column(String(300), default="")
    packer: Mapped[str] = mapped_column(String(300), default="")
    importer: Mapped[str] = mapped_column(String(300), default="")
    known_net_quantities: Mapped[str] = mapped_column(String(300), default="")  # "; "-separated
    mrp_values: Mapped[str] = mapped_column(String(300), default="")  # "; "-separated
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    notes: Mapped[str] = mapped_column(Text, default="")

    # ---------- roll-up of the latest scan (the scan rows are the source of truth) ----------
    scan_count: Mapped[int] = mapped_column(default=0)
    latest_scan_id: Mapped[int | None] = mapped_column(nullable=True)
    latest_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_ai_verdict: Mapped[str] = mapped_column(String(32), default="")
    latest_review_status: Mapped[str] = mapped_column(String(24), default="")
    latest_official_decision: Mapped[str | None] = mapped_column(String(24), nullable=True)
    pending_finalization_count: Mapped[int] = mapped_column(default=0)
