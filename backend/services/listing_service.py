"""Online listing service: store a listing, read its declarations, compare it with the package.

Three responsibilities, deliberately kept apart:

1. **Capture** — a listing (pasted text, or text captured from a page/screenshot) is stored with
   the exact text it was read from, so every extracted value can be traced back to a printed line.
2. **Declarations** — the mandatory declarations are looked for in the listing text against the
   data-driven catalog (``backend/rules/definitions/listings/``). A declaration that is not in the
   captured text is reported as NOT FOUND IN THE CAPTURED TEXT, never as proof of absence.
3. **Consistency** — package declarations against listing declarations, field by field. A
   difference is a POTENTIAL INCONSISTENCY: the application does not assert that any particular
   difference breaches any provision, because establishing the applicable legal basis is the
   officer's determination on the facts.

Nothing here invents a legal requirement: each catalog entry carries its own ``legal_status``.
"""
from __future__ import annotations

import difflib
import json
import re

from sqlalchemy.orm import Session

from backend import audit
from backend.authz import scope_org_id
from backend.extraction.listing import extract_listing
from backend.models import ExtractedField, Inspection, ProductListing
from backend.models.user import utcnow
from backend.models.product_listing import SOURCES, SOURCE_MANUAL_TEXT
from backend.rules import listing_catalog

#: Verdict vocabulary for the package/listing comparison.
MATCH = "MATCH"
INCONSISTENCY = "INCONSISTENCY_POTENTIAL"
PACKAGE_ONLY = "PACKAGE_ONLY"
LISTING_ONLY = "LISTING_ONLY"
NOT_COMPARABLE = "NOT_COMPARABLE"

_TEXT_NOISE_RE = re.compile(r"[^a-z0-9]+")

#: Multipliers to a common base so '750 g' and '0.75 kg' compare equal. Species-preserving: mass and
#: volume never convert into each other.
_UNIT_BASE: dict[str, tuple[str, float]] = {
    "G": ("MASS", 1.0),
    "GM": ("MASS", 1.0),
    "GMS": ("MASS", 1.0),
    "GRAM": ("MASS", 1.0),
    "GRAMS": ("MASS", 1.0),
    "KG": ("MASS", 1000.0),
    "KGS": ("MASS", 1000.0),
    "GMS.": ("MASS", 1.0),
    "ML": ("VOLUME", 1.0),
    "ML.": ("VOLUME", 1.0),
    "L": ("VOLUME", 1000.0),
    "LT": ("VOLUME", 1000.0),
    "LTR": ("VOLUME", 1000.0),
    "LITRE": ("VOLUME", 1000.0),
    "LITER": ("VOLUME", 1000.0),
    "MM": ("LENGTH", 1.0),
    "CM": ("LENGTH", 10.0),
    "M": ("LENGTH", 1000.0),
}


def _norm_text(value: str) -> str:
    return _TEXT_NOISE_RE.sub(" ", (value or "").lower()).strip()


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm_text(a), _norm_text(b)).ratio()


def _to_base(quantity, unit: str | None):
    if quantity is None:
        return None
    key = (unit or "").strip().upper()
    entry = _UNIT_BASE.get(key)
    if entry is None:
        return None
    family, multiplier = entry
    try:
        return family, float(quantity) * multiplier
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- capture


def refresh_extraction(db: Session, listing: ProductListing, text: str | None = None) -> ProductListing:
    """(Re-)run listing extraction over the stored/raw text. Deterministic and idempotent."""
    if text is not None:
        listing.raw_text = text
    result = extract_listing(listing.raw_text, source=listing.source)
    listing.extracted_json = json.dumps(result, ensure_ascii=False)
    listing.title = (
        (result.get("listing_only", {}).get("listing_title", {}) or {}).get("display_value", "")
        or listing.title
    )[:255]
    listing.updated_at = utcnow()
    return listing


def create_listing(
    db: Session,
    *,
    actor: str,
    organization_id: int | None,
    text: str,
    source: str = SOURCE_MANUAL_TEXT,
    source_url: str = "",
    platform: str = "",
    inspection_id: int | None = None,
) -> ProductListing:
    source = (source or SOURCE_MANUAL_TEXT).upper()
    if source not in SOURCES:
        source = SOURCE_MANUAL_TEXT
    if not (text or "").strip():
        raise ValueError("Listing text is required — paste the listing text so it can be read.")
    if source == "LISTING_URL" and not (source_url or "").strip():
        raise ValueError("A listing URL is required when the source is a listing URL.")
    if inspection_id is not None:
        exists = db.query(Inspection).filter(Inspection.id == inspection_id).first()
        if exists is None:
            raise ValueError(f"Inspection {inspection_id} does not exist")
    listing = ProductListing(
        organization_id=organization_id,
        inspection_id=inspection_id,
        source=source,
        source_url=(source_url or "").strip()[:500],
        platform=(platform or "").strip()[:80],
        raw_text=text,
        created_by=actor,
    )
    db.add(listing)
    db.flush()
    refresh_extraction(db, listing)
    db.commit()
    db.refresh(listing)
    audit.log_action(
        actor,
        "listing_captured",
        f"listing #{listing.id}",
        after=f"source={listing.source}; {len(listing.raw_text)} characters; inspection={inspection_id or '—'}",
    )
    return listing


def update_listing(db: Session, listing: ProductListing, *, actor: str, text: str) -> ProductListing:
    if not (text or "").strip():
        raise ValueError("Listing text cannot be emptied — supply the listing text to replace it.")
    before = len(listing.raw_text)
    refresh_extraction(db, listing, text=text)
    listing.consistency_json = ""
    listing.consistency_at = None
    db.commit()
    db.refresh(listing)
    audit.log_action(
        actor,
        "listing_updated",
        f"listing #{listing.id}",
        before=f"{before} characters",
        after=f"{len(listing.raw_text)} characters",
    )
    return listing


def attach_to_inspection(db: Session, listing: ProductListing, inspection_id: int, *, actor: str) -> ProductListing:
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None:
        raise ValueError(f"Inspection {inspection_id} does not exist")
    listing.inspection_id = inspection_id
    listing.consistency_json = ""
    listing.consistency_at = None
    listing.updated_at = utcnow()
    db.commit()
    db.refresh(listing)
    audit.log_action(actor, "listing_attached", f"listing #{listing.id}", after=inspection.inspection_number)
    return listing


def delete_listing(db: Session, listing: ProductListing, *, actor: str) -> None:
    audit.log_action(actor, "listing_deleted", f"listing #{listing.id}", before=listing.source_url or listing.title)
    db.delete(listing)
    db.commit()


# --------------------------------------------------------------------------- reads


def _payload(listing: ProductListing) -> dict:
    try:
        value = json.loads(listing.extracted_json or "{}")
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def listing_view(listing: ProductListing) -> dict:
    return {
        "id": listing.id,
        "inspection_id": listing.inspection_id,
        "source": listing.source,
        "source_url": listing.source_url,
        "platform": listing.platform,
        "title": listing.title,
        "raw_text": listing.raw_text,
        "created_by": listing.created_by,
        "created_at": str(listing.created_at),
        "updated_at": str(listing.updated_at),
        "extraction": _payload(listing),
        "consistency": _stored_consistency(listing),
    }


def _stored_consistency(listing: ProductListing) -> dict | None:
    try:
        value = json.loads(listing.consistency_json or "null")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def list_listings(db: Session, *, user, query: str = "", page: int = 1, page_size: int = 20) -> dict:
    q = db.query(ProductListing)
    scoped = scope_org_id(user)
    if scoped is not None:
        q = q.filter(ProductListing.organization_id == scoped)
    if (query or "").strip():
        needle = f"%{query.strip().lower()}%"
        q = q.filter(
            ProductListing.title.ilike(needle)
            | ProductListing.platform.ilike(needle)
            | ProductListing.source_url.ilike(needle)
            | ProductListing.raw_text.ilike(needle)
        )
    total = q.count()
    rows = (
        q.order_by(ProductListing.created_at.desc(), ProductListing.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "inspection_id": r.inspection_id,
                "source": r.source,
                "source_url": r.source_url,
                "platform": r.platform,
                "title": r.title,
                "created_by": r.created_by,
                "created_at": str(r.created_at),
                "fields_read": len(_payload(r).get("fields", {})),
                "consistency": (_stored_consistency(r) or {}).get("summary"),
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# --------------------------------------------------------------------------- declaration check

#: Applicability caveats the application must state rather than assume.
_APPLICABILITY_NOTES = {
    "country_of_origin": "Requirement depends on whether the commodity is imported — not assumed here.",
    "unit_sale_price": "Unit sale price applies where the legally effective ruleset requires it — not assumed here.",
    "consumer_care_phone": "Consumer-care details are required where applicable; confirm on the facts.",
    "consumer_care_email": "Consumer-care details are required where applicable; confirm on the facts.",
}


def declaration_check(listing: ProductListing) -> dict:
    """Look for the Rule 6(10) watched declarations in the captured listing text.

    A declaration absent from the captured text is reported as ABSENT FROM CAPTURED TEXT. It is not
    reported as non-displayed, because the text supplied may not cover the whole page (and a listing
    page can carry declarations in a tab or specification table the capture missed).
    """
    catalog = listing_catalog.declaration_check()
    if catalog is None:  # the catalog file was removed — say so instead of inventing a check
        return {
            "check_id": "LIST-6-10-DECLARATIONS",
            "status": "NOT_EVALUATED",
            "reason": "The e-commerce rule catalog could not be loaded, so no listing declaration check was run.",
        }
    payload = _payload(listing)
    fields = payload.get("fields", {})
    listing_only = payload.get("listing_only", {})
    watched = list(catalog.get("watched_fields", []))
    presented: list[dict] = []
    present = 0
    for name in watched:
        entry = fields.get(name)
        value = (entry or {}).get("display_value", "")
        if name == "mrp" and not value:
            # A listing commonly prints the price without the words "MRP"; the price line is shown
            # as evidence for the officer, but it is NOT recorded as an MRP declaration.
            price = listing_only.get("listing_price") or {}
            if price.get("display_value"):
                presented.append(
                    {
                        "field": name,
                        "status": "ABSENT_FROM_CAPTURED_TEXT",
                        "note": "A price is displayed, but no MRP declaration was read from the listing text.",
                        "related_price_evidence": price.get("display_value"),
                    }
                )
                continue
        if (value or "").strip() and entry.get("state", "DETECTED") != "HUMAN_CONFIRMED_ABSENT":
            present += 1
            presented.append(
                {
                    "field": name,
                    "status": "FOUND",
                    "value": entry.get("display_value"),
                    "confidence": entry.get("confidence"),
                    "source_text": entry.get("source_text"),
                    "inferred": bool(entry.get("inferred")),
                    # The value may be read perfectly while the ROLE it fills is not established by
                    # the page itself. That is a judgement for the officer, so it travels with the
                    # result instead of being silently resolved.
                    "role_uncertain": bool(entry.get("role_uncertain")),
                    "applicability_note": _APPLICABILITY_NOTES.get(name, ""),
                }
            )
        else:
            presented.append(
                {
                    "field": name,
                    "status": "ABSENT_FROM_CAPTURED_TEXT",
                    "note": _APPLICABILITY_NOTES.get(name, ""),
                }
            )
    ratio = (present / len(watched)) if watched else 0.0
    minimum = float(catalog.get("minimum_ratio", 0.6))
    if not watched:
        status = "NOT_EVALUATED"
        reason = "No declarations are configured for this check."
    elif ratio >= minimum:
        status = "PASS"
        reason = (
            f"{present} of {len(watched)} watched declarations were read from the listing text "
            f"(coverage {ratio:.0%}, threshold {minimum:.0%})."
        )
    else:
        status = "UNCERTAIN"
        reason = (
            f"Only {present} of {len(watched)} watched declarations were read from the captured listing "
            "text. A declaration that is absent from the captured text is not proof that it is not "
            "displayed on the listing — the capture may not cover the whole page. Confirm on the "
            "listing page before treating this as a shortfall."
        )
    return {
        "check_id": catalog.get("check_id"),
        "rule_number": catalog.get("rule_number"),
        "title": catalog.get("title"),
        "requirement": catalog.get("requirement"),
        "status": status,
        "reason": reason,
        "confidence": round(min(0.9, 0.4 + ratio * 0.5), 3),
        "coverage": round(ratio, 3),
        "minimum_ratio": minimum,
        "declarations": presented,
        # The legal-status marker travels with the result: the UI renders it verbatim so a check
        # whose citation is not confidently verified can never look like settled law.
        "legal_status": catalog.get("legal_status"),
        "legal_status_label": listing_catalog.status_label(catalog.get("legal_status", "")),
        "legal_status_note": catalog.get("legal_status_note", ""),
        "source_reference": catalog.get("source_reference"),
        "source_url": catalog.get("source_url"),
    }


# --------------------------------------------------------------------------- consistency


def _package_fields(db: Session, inspection_id: int) -> dict[str, dict]:
    rows = db.query(ExtractedField).filter(ExtractedField.inspection_id == inspection_id).all()
    out: dict[str, dict] = {}
    for row in rows:
        if row.state in ("DETECTED", "MANUALLY_CORRECTED", "CONFLICTING"):
            out[row.field_name] = {
                "display_value": row.display_value,
                "normalized_value": row.normalized_value,
                "state": row.state,
                "confidence": round(float(row.confidence or 0.0), 3),
                "source_image_id": row.source_image_id,
            }
    return out


def _compare(field: str, package: dict | None, listing: dict | None) -> dict:
    pkg_value = (package or {}).get("display_value", "") or ""
    lst_value = (listing or {}).get("display_value", "") or ""
    base = {
        "field": field,
        "package_value": pkg_value,
        "listing_value": lst_value,
        "package_state": (package or {}).get("state", ""),
    }
    if not pkg_value and not lst_value:
        return {**base, "verdict": NOT_COMPARABLE, "method": "neither side carries this declaration"}
    if not pkg_value:
        return {
            **base,
            "verdict": LISTING_ONLY,
            "method": "declaration read from the listing only",
            "note": "The package side has no value for this field (not read from the supplied images).",
        }
    if not lst_value:
        return {
            **base,
            "verdict": PACKAGE_ONLY,
            "method": "declaration read from the package only",
            "note": "The listing text supplied does not carry this declaration.",
        }
    if (package or {}).get("state") == "CONFLICTING":
        return {
            **base,
            "verdict": NOT_COMPARABLE,
            "method": "package value is itself conflicting across images",
            "note": "Resolve the conflicting package candidates before comparing with the listing.",
        }

    if field in ("mrp", "unit_sale_price"):
        return _compare_amount(base, package or {}, listing or {})
    if field == "net_quantity":
        return _compare_quantity(base, package or {}, listing or {})
    return _compare_text(base, field)


def _numbers(value: str):
    return re.findall(r"[0-9][0-9,]*(?:\.[0-9]{1,2})?", value or "")


def _compare_amount(base: dict, package: dict, listing: dict) -> dict:
    pkg_nums = _numbers(package.get("normalized_value") or package.get("display_value", ""))
    lst_nums = _numbers(listing.get("normalized_value") or listing.get("display_value", ""))
    if not pkg_nums or not lst_nums:
        return {
            **base,
            "verdict": NOT_COMPARABLE,
            "method": "amounts could not both be reduced to a number",
        }
    pkg = float(pkg_nums[0].replace(",", ""))
    lst = float(lst_nums[0].replace(",", ""))
    if abs(pkg - lst) < 0.01:
        return {**base, "verdict": MATCH, "method": f"numeric comparison ({pkg:g} = {lst:g})"}
    return {
        **base,
        "verdict": INCONSISTENCY,
        "method": f"numeric comparison ({pkg:g} vs {lst:g})",
        "note": "The two figures differ. This is a potential inconsistency to inspect — the applicable "
                "legal basis for the difference has to be established on the facts.",
    }


def _compare_quantity(base: dict, package: dict, listing: dict) -> dict:
    try:
        pkg_q = json.loads(package.get("normalized_value") or "{}")
    except json.JSONDecodeError:
        pkg_q = {}
    try:
        lst_q = json.loads(listing.get("normalized_value") or "{}")
    except json.JSONDecodeError:
        lst_q = {}
    pkg_base = _to_base(pkg_q.get("value"), pkg_q.get("unit"))
    lst_base = _to_base(lst_q.get("value"), lst_q.get("unit"))
    if pkg_base and lst_base:
        if pkg_base[0] != lst_base[0]:
            return {
                **base,
                "verdict": NOT_COMPARABLE,
                "method": f"different quantity families ({pkg_base[0]} vs {lst_base[0]})",
            }
        if abs(pkg_base[1] - lst_base[1]) < 0.01:
            return {
                **base,
                "verdict": MATCH,
                "method": f"unit-normalised comparison ({pkg_base[1]:g} = {lst_base[1]:g} in base units)",
            }
        return {
            **base,
            "verdict": INCONSISTENCY,
            "method": f"unit-normalised comparison ({pkg_base[1]:g} vs {lst_base[1]:g} in base units)",
            "note": "The declared net quantities differ once both are converted to a common unit.",
        }
    if _norm_text(package.get("display_value", "")) == _norm_text(listing.get("display_value", "")):
        return {**base, "verdict": MATCH, "method": "printed forms are identical"}
    return {
        **base,
        "verdict": NOT_COMPARABLE,
        "method": "one or both quantities could not be normalised for comparison",
    }


def _compare_text(base: dict, field: str) -> dict:
    pkg = base["package_value"]
    lst = base["listing_value"]
    similarity = _similarity(pkg, lst)
    # Brand names are printed inconsistently (MEDIMIX / Medimix / Medimix Ayurvedic); an entity name
    # is longer and less forgiving. One threshold would be wrong for both, so use two.
    strong = 0.86 if field == "brand" else 0.72
    weak = 0.6 if field == "brand" else 0.45
    if similarity >= strong:
        return {**base, "verdict": MATCH, "method": f"text similarity {similarity:.0%}"}
    if similarity >= weak:
        return {
            **base,
            "verdict": NOT_COMPARABLE,
            "method": f"text similarity {similarity:.0%} — too close to call mechanically",
            "note": "Wording differs in a way the comparison cannot settle; a reviewer must read both.",
        }
    return {
        **base,
        "verdict": INCONSISTENCY,
        "method": f"text similarity {similarity:.0%}",
        "note": "The two descriptions differ substantially. This is a potential inconsistency to "
                "inspect — not, by itself, a finding that any provision has been breached.",
    }


def consistency(db: Session, listing: ProductListing, *, persist: bool = True) -> dict:
    """Compare the package declarations with the listing declarations, field by field."""
    catalog = listing_catalog.consistency_check()
    if listing.inspection_id is None:
        return {
            "status": "NO_PACKAGE_SIDE",
            "reason": "This listing is not linked to a package inspection, so there is nothing to "
                      "compare it against. Attach it to an inspection to run the comparison.",
            "comparisons": [],
            "summary": None,
        }
    inspection = db.query(Inspection).filter(Inspection.id == listing.inspection_id).first()
    if inspection is None:
        return {
            "status": "NO_PACKAGE_SIDE",
            "reason": "The linked inspection no longer exists.",
            "comparisons": [],
            "summary": None,
        }
    payload = _payload(listing)
    listing_fields = payload.get("fields", {})
    package_fields = _package_fields(db, listing.inspection_id)
    watched = list((catalog or {}).get("watched_fields", [])) or sorted(
        set(listing_fields) | set(package_fields)
    )
    comparisons = [_compare(name, package_fields.get(name), listing_fields.get(name)) for name in watched]
    matched = sum(1 for c in comparisons if c["verdict"] == MATCH)
    inconsistent = sum(1 for c in comparisons if c["verdict"] == INCONSISTENCY)
    one_sided = sum(1 for c in comparisons if c["verdict"] in (PACKAGE_ONLY, LISTING_ONLY))
    comparable = sum(1 for c in comparisons if c["verdict"] in (MATCH, INCONSISTENCY))
    summary = {
        "compared": len(comparisons),
        "matched": matched,
        "potential_inconsistencies": inconsistent,
        "one_sided": one_sided,
        "not_comparable": sum(1 for c in comparisons if c["verdict"] == NOT_COMPARABLE),
        "agreement_ratio": round(matched / comparable, 3) if comparable else None,
        "headline": (
            "POTENTIAL INCONSISTENCY between the package and the listing"
            if inconsistent
            else "No inconsistency found between the declarations read from the package and the listing"
            if comparable
            else "Nothing could be compared — neither side carried a comparable declaration"
        ),
    }
    result = {
        "status": "COMPARED",
        "inspection_id": listing.inspection_id,
        "inspection_number": inspection.inspection_number,
        "comparisons": comparisons,
        "summary": summary,
        "legal_status": (catalog or {}).get("legal_status", "APPLICATION_POLICY_NOT_A_LEGAL_PROVISION"),
        "legal_status_label": listing_catalog.status_label(
            (catalog or {}).get("legal_status", "APPLICATION_POLICY_NOT_A_LEGAL_PROVISION")
        ),
        "legal_status_note": (catalog or {}).get("legal_status_note", ""),
        "source_reference": (catalog or {}).get("source_reference", ""),
        "not_a_violation_note": (
            "A difference between the package and the listing is reported as a potential "
            "inconsistency. This application does not determine that a difference breaches any "
            "provision; the applicable legal basis must be established for the facts of the case."
        ),
    }
    if persist:
        listing.consistency_json = json.dumps(result, ensure_ascii=False)
        listing.consistency_at = utcnow()
        listing.updated_at = utcnow()
        db.commit()
        db.refresh(listing)
    return result


def listings_for_inspection(db: Session, inspection_id: int) -> list[ProductListing]:
    return (
        db.query(ProductListing)
        .filter(ProductListing.inspection_id == inspection_id)
        .order_by(ProductListing.id.desc())
        .all()
    )
