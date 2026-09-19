"""Deterministic logic behind the consumer tools.

Everything here is plain Python arithmetic / date handling: no model output is trusted for a
conclusion. The vision provider may READ a printed price or date; this module decides whether
two prices differ, how many days remain before an expiry date, and what a complaint's status is.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from backend.config import settings
from backend.models.tools import Bill, Complaint, GroceryItem

# One clock convention across the project (see backend.models.user.utcnow).
from backend.models.user import utcnow

# ---------- bills ----------

_MONEY_RE = re.compile(r"(?:₹|rs\.?|inr)?\s*([0-9]+(?:\.[0-9]{1,2})?)", re.IGNORECASE)


def parse_money(value: object) -> float | None:
    """Parse a printed price into a number. Returns None when nothing numeric is present."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _MONEY_RE.search(str(value))
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def compare_bill_to_mrp(billed: float | None, mrp: float | None) -> dict:
    """Compare a charged price with a printed MRP. Never returns a legal verdict."""
    if billed is None or mrp is None:
        return {
            "status": "INSUFFICIENT_DATA",
            "difference": None,
            "difference_pct": None,
            "reason": (
                "Both the billed price and the product's MRP are required for a comparison. "
                "One of them is not available yet."
            ),
        }
    if mrp <= 0:
        return {
            "status": "INSUFFICIENT_DATA",
            "difference": None,
            "difference_pct": None,
            "reason": "The MRP value read is not a usable price (zero or negative).",
        }
    difference = round(billed - mrp, 2)
    pct = round((difference / mrp) * 100, 2)
    tolerance = max(0.0, settings.BILL_PRICE_TOLERANCE_PCT) / 100.0
    if difference <= mrp * tolerance:
        return {
            "status": "PRICE_AT_OR_BELOW_MRP",
            "difference": difference,
            "difference_pct": pct,
            "reason": f"Billed ₹{billed:.2f} against a printed MRP of ₹{mrp:.2f}.",
        }
    return {
        "status": "POTENTIAL_PRICE_DIFFERENCE",
        "difference": difference,
        "difference_pct": pct,
        "reason": (
            f"Billed ₹{billed:.2f} is ₹{difference:.2f} ({pct:.1f}%) above the printed MRP of "
            f"₹{mrp:.2f}. This is a potential price difference — verify the bill and product "
            "evidence before treating it as a finding."
        ),
    }


# ---------- grocery ----------

_DURATION_RE = re.compile(
    r"(\d{1,3})\s*(day|days|week|weeks|month|months|year|years)", re.IGNORECASE
)
_UNIT_DAYS = {
    "day": 1, "days": 1, "week": 7, "weeks": 7,
    "month": 30, "months": 30, "year": 365, "years": 365,
}


def parse_iso_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%m/%Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        return parsed
    return None


def duration_to_days(text: str | None) -> int | None:
    """'Best before 12 months' → 365 days. Returns None when no duration is stated."""
    match = _DURATION_RE.search(text or "")
    if not match:
        return None
    return int(match.group(1)) * _UNIT_DAYS.get(match.group(2).lower(), 1)


def resolve_expiry(
    *,
    expiry_date: str = "",
    best_before_text: str = "",
    mfg_date: str = "",
    purchase_date: str = "",
) -> dict:
    """Work out when a grocery item expires, and say HONESTLY where that came from.

    A printed expiry date is authoritative. A duration is only converted into a calendar date
    when it is anchored to a date that actually exists in the evidence (the manufacturing date
    the duration counts from). Anchoring a 'best before 12 months' to the PURCHASE date would be
    an invention, so it is not done — the item is simply recorded as date-unknown.
    """
    explicit = parse_iso_date(expiry_date)
    if explicit:
        return {"expiry": explicit.isoformat(), "basis": "PRINTED_ON_PACKAGE", "provisional": False}
    days = duration_to_days(best_before_text)
    anchor = parse_iso_date(mfg_date)
    if days and anchor:
        return {
            "expiry": (anchor + timedelta(days=days)).isoformat(),
            "basis": "DURATION_FROM_MANUFACTURING_DATE",
            "provisional": True,
        }
    if days:
        return {
            "expiry": "",
            "basis": "DURATION_ONLY_NO_ANCHOR",
            "provisional": True,
            "note": (
                f"The package declares '{best_before_text.strip()}' but no manufacturing/packing "
                "date was captured to count it from. Add the printed expiry or the mfg date to "
                "enable tracking."
            ),
        }
    if purchase_date:
        return {
            "expiry": "",
            "basis": "NOT_DECLARED",
            "provisional": False,
            "note": "No expiry or best-before declaration was found in the evidence.",
        }
    return {"expiry": "", "basis": "NOT_PROVIDED", "provisional": False}


def grocery_alert(expiry_iso: str, *, warn_days: int = 7) -> dict:
    """Days remaining + alert state. 'EXPIRED' and 'EXPIRING_SOON' are the only alert states."""
    parsed = parse_iso_date(expiry_iso)
    if not parsed:
        return {
            "days_remaining": None,
            "alert": "NO_EXPIRY_DATA",
            "detail": "No expiry date is tracked for this item, so no alert can be raised.",
        }
    remaining = (parsed - date.today()).days
    if remaining < 0:
        return {
            "days_remaining": remaining,
            "alert": "EXPIRED",
            "detail": f"Expiry date {parsed.isoformat()} has passed by {abs(remaining)} day(s).",
        }
    if remaining <= warn_days:
        return {
            "days_remaining": remaining,
            "alert": "EXPIRING_SOON",
            "detail": f"Expires in {remaining} day(s) on {parsed.isoformat()}.",
        }
    return {
        "days_remaining": remaining,
        "alert": "FRESH",
        "detail": f"Expires on {parsed.isoformat()}.",
    }


def grocery_view(item: GroceryItem) -> dict:
    """Serialize a grocery row, computing live alert state (never stored stale)."""
    alert = grocery_alert(item.expiry_date)
    return {
        "id": item.id,
        "owner": item.owner,
        "product_id": item.product_id,
        "inspection_id": item.inspection_id,
        "product_name": item.product_name,
        "brand": item.brand,
        "category": item.category,
        "quantity": item.quantity,
        "mrp": item.mrp,
        "batch_lot": item.batch_lot,
        "purchase_date": item.purchase_date,
        "expiry_date": item.expiry_date,
        "best_before_text": item.best_before_text,
        "expiry_basis": item.expiry_basis,
        "notes": item.notes,
        "created_at": str(item.created_at),
        **alert,
    }


# ---------- complaints ----------

COMPLAINT_STAGES = ["SUBMITTED", "UNDER_REVIEW", "EVIDENCE_VERIFIED", "RESOLVED"]


def next_complaint_number(db: Session) -> str:
    year = date.today().year
    count = db.query(Complaint).count() + 1
    return f"CMP-{year}-{count:04d}"


def append_timeline(db: Session, complaint: Complaint, status: str, actor: str, note: str = "") -> None:
    """Append-only status history — the complaint's audit trail."""
    try:
        timeline = json.loads(complaint.timeline or "[]")
        if not isinstance(timeline, list):
            timeline = []
    except json.JSONDecodeError:
        timeline = []
    timeline.append(
        {
            "at": utcnow().isoformat(timespec="seconds"),
            "status": status,
            "actor": actor,
            "note": note,
        }
    )
    complaint.timeline = json.dumps(timeline, ensure_ascii=False)
    complaint.status = status
    complaint.updated_at = utcnow()
    db.commit()


def complaint_view(complaint: Complaint) -> dict:
    try:
        timeline = json.loads(complaint.timeline or "[]")
    except json.JSONDecodeError:
        timeline = []
    return {
        "id": complaint.id,
        "complaint_number": complaint.complaint_number,
        "created_by": complaint.created_by,
        "created_at": str(complaint.created_at),
        "updated_at": str(complaint.updated_at),
        "product_id": complaint.product_id,
        "inspection_id": complaint.inspection_id,
        "bill_id": complaint.bill_id,
        "product_name": complaint.product_name,
        "store_name": complaint.store_name,
        "category": complaint.category,
        "severity": complaint.severity,
        "issue": complaint.issue,
        "status": complaint.status,
        "timeline": timeline,
        "resolution": complaint.resolution,
        "stages": COMPLAINT_STAGES,
    }


def bill_view(bill: Bill) -> dict:
    return {
        "id": bill.id,
        "created_by": bill.created_by,
        "created_at": str(bill.created_at),
        "product_id": bill.product_id,
        "inspection_id": bill.inspection_id,
        "store_name": bill.store_name,
        "bill_number": bill.bill_number,
        "bill_date": bill.bill_date,
        "product_name": bill.product_name,
        "brand": bill.brand,
        "quantity": bill.quantity,
        "line_items": bill.line_items,
        "billed_price": bill.billed_price,
        "mrp": bill.mrp,
        "price_difference": bill.price_difference,
        "price_difference_pct": bill.price_difference_pct,
        "comparison_status": bill.comparison_status,
        "stored_filename": bill.stored_filename,
        "extraction_source": bill.extraction_source,
        "extraction_confidence": bill.extraction_confidence,
        "extraction_detail": bill.extraction_detail,
        "notes": bill.notes,
    }
