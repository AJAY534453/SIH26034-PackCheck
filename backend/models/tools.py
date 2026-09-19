"""Consumer-facing tool models: Bill Scanner, Grocery tracker, Complaint Center.

These tables are deliberately separate from the inspection evidence chain so a consumer tool can
never mutate an inspection record. A bill may LINK to an inspection/product and a complaint may
reference both — but the linkage is a reference, never an overwrite.

Provenance honesty: every stored reading carries `extraction_source` (one of
`vision` | `manual` | `demo`) so the UI can always say where a number came from. Demo/sample
values are never blended into real readings.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.user import utcnow


class Bill(Base):
    """A scanned or manually entered bill, compared against the product's MRP.

    The comparison here is the ONLY thing this row asserts: whether a price was charged above a
    printed MRP. It is not a legal finding — the UI words it as a potential price difference
    requiring verification.
    """

    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inspection_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    store_name: Mapped[str] = mapped_column(String(200), default="")
    bill_number: Mapped[str] = mapped_column(String(80), default="")
    bill_date: Mapped[str] = mapped_column(String(40), default="")
    product_name: Mapped[str] = mapped_column(String(200), default="")
    brand: Mapped[str] = mapped_column(String(120), default="")
    quantity: Mapped[str] = mapped_column(String(80), default="")
    line_items: Mapped[str] = mapped_column(Text, default="")

    billed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    mrp: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Deterministic comparison outcome (computed in Python, never by the model).
    price_difference: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_difference_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    comparison_status: Mapped[str] = mapped_column(String(40), default="INSUFFICIENT_DATA")

    # Evidence: the uploaded bill image (kept immutable, served through the protected file route).
    stored_filename: Mapped[str] = mapped_column(String(200), default="")
    extraction_source: Mapped[str] = mapped_column(String(20), default="manual")  # vision|manual|demo
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    extraction_detail: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class GroceryItem(Base):
    """A product the user EXPLICITLY added to their grocery tracker.

    Expiry tracking exists only for these rows. Scanning or inspecting a product never adds it
    here — that decision is the user's, and it is recorded with who made it and when.
    """

    __tablename__ = "grocery_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(80), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inspection_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    product_name: Mapped[str] = mapped_column(String(200), default="")
    brand: Mapped[str] = mapped_column(String(120), default="")
    quantity: Mapped[str] = mapped_column(String(80), default="")
    mrp: Mapped[str] = mapped_column(String(40), default="")
    batch_lot: Mapped[str] = mapped_column(String(80), default="")
    # Where the product's own panel data was read from (grocery card -> product detail).
    category: Mapped[str] = mapped_column(String(60), default="")

    purchase_date: Mapped[str] = mapped_column(String(30), default="")
    expiry_date: Mapped[str] = mapped_column(String(30), default="")
    best_before_text: Mapped[str] = mapped_column(String(120), default="")
    # Where the expiry date came from: the label reading, or a duration the user was told to
    # count from the purchase/manufacturing date. Never inferred silently.
    expiry_basis: Mapped[str] = mapped_column(String(60), default="NOT_PROVIDED")
    notes: Mapped[str] = mapped_column(Text, default="")


class Complaint(Base):
    """A complaint about a product, optionally backed by an inspection and/or a bill.

    Status transitions are append-only history in `timeline` (JSON list), so the complaint's
    journey is auditable exactly like an inspection's.
    """

    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inspection_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bill_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    product_name: Mapped[str] = mapped_column(String(200), default="")
    store_name: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(60), default="OTHER")
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    issue: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="SUBMITTED")
    timeline: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of {at, status, actor, note}
    resolution: Mapped[str] = mapped_column(Text, default="")
