"""Product scan repository + compliance history.

Every processed inspection becomes exactly one repository scan row (``product_scans``) linked to a
deduplicated product (``products``). The row is the durable record of:

* what was scanned (product identity snapshot, evidence image reference, extracted text),
* when and by whom,
* every compliance check performed (with its legal reference),
* the AI preliminary verdict and weighted score,
* the detected violations,
* the human/official final decision, remarks, finalizer and finalization time,
* the previous compliance history (the other scan rows that share the product).

Nothing here re-implements rule evaluation: the checks and score come from the stored
``rule_evaluations`` through :mod:`backend.services.compliance_service`, so the repository can
never disagree with the engine that produced them.
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from backend import audit
from backend.authz import Permission, has_permission, scope_org_id
from backend.models import (
    ExtractedField,
    ImageAnalysis,
    Inspection,
    InspectionImage,
    Product,
    ProductScan,
)
from backend.models.product_scan import (
    PENDING_MESSAGE,
    STATUS_AI_PRELIMINARY,
    STATUS_FINALIZED,
    STATUS_PENDING_FINALIZATION,
)
from backend.models.user import utcnow
from backend.services import compliance_service

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _SLUG_RE.sub("-", (value or "").lower()).strip("-")


def dedupe_key(identity: dict, inspection: Inspection) -> str:
    """Conservative identity key: normalised name + brand + manufacturer.

    A scan with no readable product name is NOT merged with every other unnamed scan — it keys on
    its own inspection instead, so the repository never invents an identity.
    """
    name = _slug(identity.get("product_name", ""))
    if not name:
        return f"unnamed-{inspection.id}"
    return "|".join([name, _slug(identity.get("brand", "")), _slug(identity.get("manufacturer", ""))])[:200]


def _get_product(db: Session, identity: dict, inspection: Inspection) -> Product:
    key = dedupe_key(identity, inspection)
    product = db.query(Product).filter(Product.dedupe_key == key).first()
    if product is None:
        product = Product(
            dedupe_key=key,
            name=identity.get("product_name", ""),
            brand=identity.get("brand", ""),
            category=identity.get("category", "") or "OTHER",
            category_state=identity.get("category_state", "UNCERTAIN"),
            manufacturer=identity.get("manufacturer", ""),
        )
        db.add(product)
        db.flush()
    else:
        # Fill in gaps only — never overwrite a value that is already known with a blank one.
        for field in ("name", "brand", "manufacturer"):
            if not (getattr(product, field) or "").strip() and identity.get(field):
                setattr(product, field, identity[field])
        if identity.get("category"):
            product.category = identity["category"]
            product.category_state = identity.get("category_state", product.category_state)
    return product


def _append_unique(existing: str, value: str) -> str:
    parts = [p.strip() for p in (existing or "").split(";") if p.strip()]
    if value and value not in parts:
        parts.append(value)
    return "; ".join(parts)[:300]


def refresh_scan(db: Session, inspection: Inspection) -> ProductScan:
    """Create or update the repository row for an inspection (idempotent)."""
    identity = compliance_service.identity_fields(db, inspection)
    product = _get_product(db, identity, inspection)

    images = (
        db.query(InspectionImage)
        .filter(InspectionImage.inspection_id == inspection.id)
        .order_by(InspectionImage.id)
        .all()
    )
    fields = db.query(ExtractedField).filter(ExtractedField.inspection_id == inspection.id).all()

    review = compliance_service.build_ai_review(db, inspection)

    scan = db.query(ProductScan).filter(ProductScan.inspection_id == inspection.id).first()
    if scan is None:
        scan = ProductScan(inspection_id=inspection.id)
        db.add(scan)
        db.flush()

    previous_status = scan.review_status
    previous_decision = scan.official_decision

    scan.product_id = product.id
    scan.organization_id = inspection.organization_id
    scan.scan_number = inspection.inspection_number
    scan.scanned_at = inspection.processed_at or inspection.created_at or utcnow()
    scan.scanned_by = inspection.inspector or ""
    scan.product_name = identity.get("product_name", "")
    scan.brand = identity.get("brand", "")
    scan.category = identity.get("category", "")
    scan.category_state = identity.get("category_state", "UNCERTAIN")
    scan.manufacturer = identity.get("manufacturer", "")
    scan.net_quantity = identity.get("net_quantity", "")
    scan.mrp = identity.get("mrp", "")
    scan.batch_lot = identity.get("batch_lot", "")
    scan.image_count = len(images)
    scan.image_filename = images[0].stored_filename if images else ""
    scan.extracted_text = "\n".join((img.ocr_text or "") for img in images).strip()
    scan.structured_fields = json.dumps(
        {
            f.field_name: {
                "value": f.display_value,
                "state": f.state,
                "confidence": round(float(f.confidence or 0.0), 3),
                "source": f.source,
                "uncertainty_reason": f.uncertainty_reason or "",
                "manually_corrected": bool(f.manually_corrected),
            }
            for f in fields
        },
        ensure_ascii=False,
    )
    scan.compliance_score = review["score"]
    scan.coverage_score = review["coverage"]
    scan.coverage_floor = review["coverage_floor"]
    scan.ai_verdict = review["verdict"]
    scan.ai_confidence = review["confidence"]
    scan.ai_summary = review["summary"]
    scan.recommended_action = review["recommended_action"]
    scan.checks_json = json.dumps(review["checks"], ensure_ascii=False)
    scan.violations_json = json.dumps(review["violations"], ensure_ascii=False)
    scan.legal_refs_json = json.dumps(review["legal_references"], ensure_ascii=False)
    scan.rules_snapshot = json.dumps(
        [
            {
                "rule_number": c["rule_number"],
                "title": c["title"],
                "status": c["status"],
                "legal_reference": c["legal_reference"],
                "confidence": c["confidence"],
            }
            for c in review["checks"]
        ],
        ensure_ascii=False,
    )
    scan.threshold = review["threshold"]
    scan.ai_scoring = json.dumps(review["scoring"], ensure_ascii=False)

    # A recorded human finalization is never silently discarded by a re-run of the pipeline.
    if previous_status == STATUS_FINALIZED and previous_decision:
        scan.review_status = STATUS_FINALIZED
        scan.official_decision = previous_decision
    else:
        # A preliminary pass needs BOTH the compliance threshold and the evidence-coverage
        # floor — the verdict already encodes that gate, so it is not re-derived here.
        scan.review_status = (
            STATUS_AI_PRELIMINARY
            if review["verdict"] == compliance_service.VERDICT_COMPLIANT
            else STATUS_PENDING_FINALIZATION
        )
        scan.official_decision = None
    scan.updated_at = utcnow()

    # ---------- product roll-up ----------
    scans = (
        db.query(ProductScan)
        .filter(ProductScan.product_id == product.id, ProductScan.id != scan.id)
        .all()
    )
    history = scans + [scan]
    product.scan_count = len(history)
    product.last_seen = max((s.scanned_at for s in history if s.scanned_at), default=utcnow())
    product.first_seen = min((s.scanned_at for s in history if s.scanned_at), default=product.first_seen)
    product.latest_scan_id = scan.id
    product.latest_score = scan.compliance_score
    product.latest_ai_verdict = scan.ai_verdict
    product.latest_review_status = scan.review_status
    product.latest_official_decision = scan.official_decision
    product.pending_finalization_count = sum(
        1 for s in history if s.review_status != STATUS_FINALIZED
    )
    for value, column in ((identity.get("net_quantity"), "known_net_quantities"), (identity.get("mrp"), "mrp_values")):
        if value:
            setattr(product, column, _append_unique(getattr(product, column), value))

    db.commit()
    db.refresh(scan)
    return scan


def finalize_scan(
    db: Session,
    scan: ProductScan,
    *,
    decision: str,
    remarks: str,
    reviewer: str,
) -> ProductScan:
    """Record the OFFICIAL final decision on a scan (authorized humans only — enforced by the API)."""
    from backend.models.enums import FinalDecision

    value = FinalDecision(decision).value
    inspection = db.query(Inspection).filter(Inspection.id == scan.inspection_id).first()
    previous = scan.official_decision or "(pending)"
    if not (remarks or "").strip():
        raise ValueError("A remark is required to finalize a compliance result")

    scan.official_decision = value
    scan.review_status = STATUS_FINALIZED
    scan.remarks = remarks.strip()
    scan.finalized_by = reviewer
    scan.finalized_at = utcnow()
    scan.updated_at = utcnow()
    if inspection is not None:
        inspection.official_decision = value
        inspection.finalized_by = reviewer
        inspection.finalized_at = utcnow()
        inspection.finalization_remarks = remarks.strip()
    db.commit()
    db.refresh(scan)
    audit.log_action(
        reviewer,
        "compliance_finalized",
        scan.scan_number,
        before=f"{previous} (score {scan.compliance_score}%)",
        after=f"{value} (remarks: {remarks.strip()[:160]})",
        reason="official final decision on the automated compliance review",
    )
    _refresh_product_rollup(db, scan.product_id)
    db.refresh(scan)
    return scan


def _refresh_product_rollup(db: Session, product_id: int | None) -> None:
    if not product_id:
        return
    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        return
    scans = db.query(ProductScan).filter(ProductScan.product_id == product_id).all()
    latest = max(scans, key=lambda s: (s.scanned_at or utcnow(), s.id))
    product.scan_count = len(scans)
    product.latest_scan_id = latest.id
    product.latest_score = latest.compliance_score
    product.latest_ai_verdict = latest.ai_verdict
    product.latest_review_status = latest.review_status
    product.latest_official_decision = latest.official_decision
    product.pending_finalization_count = sum(1 for s in scans if s.review_status != STATUS_FINALIZED)
    db.commit()


def add_remarks(db: Session, scan: ProductScan, actor: str, remarks: str) -> ProductScan:
    """Attach working remarks to a scan without deciding anything."""
    scan.remarks = (remarks or "").strip()
    scan.updated_at = utcnow()
    db.commit()
    db.refresh(scan)
    audit.log_action(actor, "compliance_remarks", scan.scan_number, after=scan.remarks[:200])
    return scan


def is_reviewer(user) -> bool:
    return has_permission(user, Permission.COMPLIANCE_FINALIZE)


def scan_view(db: Session, scan: ProductScan, *, user, include_history: bool = False) -> dict:
    """Serialize a scan. Reviewer-only fields are withheld from users who cannot finalize."""
    reviewer = is_reviewer(user)
    checks = _load(scan.checks_json, [])
    violations = _load(scan.violations_json, [])
    inspection = db.query(Inspection).filter(Inspection.id == scan.inspection_id).first()
    derived_status = report_status(scan, inspection)
    payload = {
        "id": scan.id,
        "product_id": scan.product_id,
        "inspection_id": scan.inspection_id,
        "inspection_number": scan.scan_number,
        "scanned_at": str(scan.scanned_at),
        "scanned_by": scan.scanned_by,
        "product_name": scan.product_name,
        "brand": scan.brand,
        "category": scan.category,
        "category_state": scan.category_state,
        "manufacturer": scan.manufacturer,
        "net_quantity": scan.net_quantity,
        "mrp": scan.mrp,
        "batch_lot": scan.batch_lot,
        "image_filename": scan.image_filename,
        "image_count": scan.image_count,
        "extracted_text": scan.extracted_text,
        "structured_fields": _load(scan.structured_fields, {}),
        "compliance_score": scan.compliance_score,
        "coverage_score": scan.coverage_score,
        "coverage_floor": scan.coverage_floor,
        "ai_verdict": scan.ai_verdict,
        "ai_confidence": scan.ai_confidence,
        "ai_summary": scan.ai_summary,
        "recommended_action": scan.recommended_action,
        "checks": checks,
        "violations": violations,
        "legal_references": _load(scan.legal_refs_json, []),
        "rules_snapshot": _load(scan.rules_snapshot, []),
        "threshold": scan.threshold,
        # Two distinct statuses, never merged.
        "ai_verdict_is_preliminary": True,
        "review_status": scan.review_status,
        "report_status": derived_status,
        "report_status_label": REPORT_STATUS_LABELS.get(derived_status, derived_status),
        "official_decision": scan.official_decision,
        "is_finalized": scan.is_finalized,
        "finalized_by": scan.finalized_by,
        "finalized_at": str(scan.finalized_at) if scan.finalized_at else None,
        "status_message": (
            f"Final decision recorded by {scan.finalized_by}: {scan.official_decision}"
            if scan.is_finalized
            else PENDING_MESSAGE
        ),
        "can_finalize": reviewer,
    }
    if reviewer:
        payload["remarks"] = scan.remarks
        payload["scoring"] = _load(scan.ai_scoring, {})
    else:
        # A non-reviewer may not read review working notes, and sees no final decision until it exists.
        payload["official_decision"] = scan.official_decision if scan.is_finalized else None
        payload["remarks"] = ""
    if include_history and scan.product_id:
        siblings = (
            db.query(ProductScan)
            .filter(ProductScan.product_id == scan.product_id, ProductScan.id != scan.id)
            .order_by(ProductScan.scanned_at.desc())
            .all()
        )
        payload["history"] = [
            {
                "id": s.id,
                "inspection_number": s.scan_number,
                "scanned_at": str(s.scanned_at),
                "compliance_score": s.compliance_score,
                "coverage_score": s.coverage_score,
                "ai_verdict": s.ai_verdict,
                "review_status": s.review_status,
                "official_decision": s.official_decision if (reviewer or s.is_finalized) else None,
                "status_message": s.public_status,
            }
            for s in siblings
        ]
    return payload


def card_view(scan: ProductScan, *, user) -> dict:
    """Compact repository card (list view) — progressive disclosure keeps the list clean."""
    reviewer = is_reviewer(user)
    return {
        "id": scan.id,
        "inspection_id": scan.inspection_id,
        "inspection_number": scan.scan_number,
        "product_id": scan.product_id,
        "product_name": scan.product_name or "(name not read)",
        "brand": scan.brand,
        "category": scan.category or "OTHER",
        "scanned_at": str(scan.scanned_at),
        "scanned_by": scan.scanned_by,
        "image_filename": scan.image_filename,
        "compliance_score": scan.compliance_score,
        "coverage_score": scan.coverage_score,
        "ai_verdict": scan.ai_verdict,
        "review_status": scan.review_status,
        "report_status": report_status(scan),
        "report_status_label": REPORT_STATUS_LABELS.get(report_status(scan), report_status(scan)),
        "official_decision": scan.official_decision if (reviewer or scan.is_finalized) else None,
        "status_message": scan.public_status,
        "status_text": _status_text(scan),
    }


#: Report-status model. DERIVED, never stored: a second status column could disagree with the two
#: states that actually produce it (the inspection's pipeline state and the review state), so the
#: label is computed from them every time it is read.
#:
#:   DRAFT                   inspection created, pipeline has not completed
#:   PROCESSING              pipeline running
#:   ERROR                   pipeline failed
#:   AI_ANALYSIS_COMPLETE    automated review finished and returned a pass-oriented preliminary
#:                           verdict; it is still PRELIMINARY until an official finalizes it
#:   NEEDS_MANUAL_REVIEW     automated review finished and flagged something for a human
#:   PENDING_FINALIZATION    automated review finished but the application threshold was not met
#:   FINALIZED               an authorized official recorded the final decision
#:
#: APPROVED / REJECTED are the FINALIZED outcomes (COMPLIANT / NON_COMPLIANT respectively) — they
#: are never used for an AI verdict, so a preliminary result cannot be read as an official one.
REPORT_STATUS_LABELS = {
    "DRAFT": "Draft — pipeline not run",
    "PROCESSING": "Processing",
    "ERROR": "Pipeline error",
    "AI_ANALYSIS_COMPLETE": "AI analysis complete (preliminary)",
    "NEEDS_MANUAL_REVIEW": "AI analysis complete — manual review flagged (preliminary)",
    "PENDING_FINALIZATION": "AI analysis complete — awaiting official finalization",
    "FINALIZED": "Final decision recorded by an authorized official",
}


def report_status(scan: ProductScan, inspection=None) -> str:
    """Derive the report status from the pipeline state and the review state (see the table above).

    The pipeline states (DRAFT / PROCESSING / ERROR) are only decidable when the linked inspection
    is at hand. A scan without it is derived from the review state alone — a scan row exists only
    once the pipeline has produced an automated review, so the list view can never report DRAFT for
    a result the review produced.
    """
    if scan.is_finalized:
        return "FINALIZED"
    if inspection is not None:
        pipeline_state = (getattr(inspection, "status", "") or "").upper()
        if pipeline_state in ("", "CREATED"):
            return "DRAFT"
        if pipeline_state == "PROCESSING":
            return "PROCESSING"
        if pipeline_state == "FAILED":
            return "ERROR"
    if scan.review_status == STATUS_AI_PRELIMINARY:
        return "AI_ANALYSIS_COMPLETE"
    if scan.ai_verdict == compliance_service.VERDICT_NEEDS_REVIEW or _load(scan.violations_json, []):
        return "NEEDS_MANUAL_REVIEW"
    return "PENDING_FINALIZATION"


def _status_text(scan: ProductScan) -> str:
    if scan.is_finalized:
        return scan.official_decision or ""
    if scan.review_status == STATUS_AI_PRELIMINARY:
        return "AI preliminary: pass-oriented (awaiting official finalization)"
    return "Awaiting official finalization"


def _load(raw: str, fallback):
    try:
        value = json.loads(raw or "")
        return value if isinstance(value, type(fallback)) else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback


def list_scans(
    db: Session,
    *,
    user,
    query: str = "",
    review_status: str = "",
    verdict: str = "",
    category: str = "",
    min_score: float | None = None,
    max_score: float | None = None,
    below_coverage_floor: bool = False,
    sort: str = "recent",
    page: int = 1,
    page_size: int = 24,
) -> dict:
    """Search / filter / sort the scan repository, always inside the caller's data scope."""
    q = db.query(ProductScan)
    scoped = scope_org_id(user)
    if scoped is not None:
        q = q.filter(ProductScan.organization_id == scoped)
    if review_status:
        q = q.filter(ProductScan.review_status == review_status.upper())
    if verdict:
        q = q.filter(ProductScan.ai_verdict == verdict.upper())
    if category:
        q = q.filter(ProductScan.category == category.upper())
    if min_score is not None:
        q = q.filter(ProductScan.compliance_score >= float(min_score))
    if max_score is not None:
        q = q.filter(ProductScan.compliance_score <= float(max_score))
    if below_coverage_floor:
        # "We could not verify enough of the label" — the reason a high compliance score can still
        # need an official to look at it. Filterable because it is a real review worklist.
        q = q.filter(ProductScan.coverage_score < compliance_service.COVERAGE_FLOOR)
    if (query or "").strip():
        needle = f"%{query.strip().lower()}%"
        q = q.filter(
            ProductScan.product_name.ilike(needle)
            | ProductScan.brand.ilike(needle)
            | ProductScan.manufacturer.ilike(needle)
            | ProductScan.scan_number.ilike(needle)
            | ProductScan.batch_lot.ilike(needle)
            | ProductScan.extracted_text.ilike(needle)
        )
    order = {
        "recent": ProductScan.scanned_at.desc(),
        "oldest": ProductScan.scanned_at.asc(),
        "score_desc": ProductScan.compliance_score.desc(),
        "score_asc": ProductScan.compliance_score.asc(),
        "name": ProductScan.product_name.asc(),
    }.get(sort, ProductScan.scanned_at.desc())
    total = q.count()
    rows = q.order_by(order, ProductScan.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # Facets let the UI show meaningful, data-derived filters (never a hard-coded list).
    all_rows = db.query(ProductScan)
    if scoped is not None:
        all_rows = all_rows.filter(ProductScan.organization_id == scoped)
    status_counts: dict[str, int] = {}
    verdict_counts: dict[str, int] = {}
    categories: dict[str, int] = {}
    below_floor = 0
    for row in all_rows.all():
        status_counts[row.review_status] = status_counts.get(row.review_status, 0) + 1
        verdict_counts[row.ai_verdict] = verdict_counts.get(row.ai_verdict, 0) + 1
        if row.category:
            categories[row.category] = categories.get(row.category, 0) + 1
        if (row.coverage_score or 0) < compliance_service.COVERAGE_FLOOR:
            below_floor += 1
    return {
        "items": [card_view(r, user=user) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "facets": {
            "review_status": status_counts,
            "ai_verdict": verdict_counts,
            "category": categories,
            "below_coverage_floor": below_floor,
        },
        "coverage_floor": compliance_service.COVERAGE_FLOOR,
        "threshold": compliance_service.AI_COMPLIANT_THRESHOLD,
        "pending_finalization": status_counts.get(STATUS_PENDING_FINALIZATION, 0),
        "finalized": status_counts.get(STATUS_FINALIZED, 0),
    }


def product_history(db: Session, product_id: int, *, user) -> dict | None:
    """A product with its complete scan/compliance history, oldest first."""
    scoped = scope_org_id(user)
    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        return None
    q = db.query(ProductScan).filter(ProductScan.product_id == product_id)
    if scoped is not None:
        q = q.filter(ProductScan.organization_id == scoped)
    scans = q.order_by(ProductScan.scanned_at.asc(), ProductScan.id.asc()).all()
    if scoped is not None and not scans:
        # An organisation-scoped user must not learn that another organisation's product exists.
        return None
    reviewer = is_reviewer(user)
    return {
        "product": {
            "id": product.id,
            "name": product.name,
            "brand": product.brand,
            "category": product.category,
            "manufacturer": product.manufacturer,
            "known_net_quantities": product.known_net_quantities,
            "mrp_values": product.mrp_values,
            "first_seen": str(product.first_seen),
            "last_seen": str(product.last_seen),
            "scan_count": product.scan_count,
            "latest_score": product.latest_score,
            "latest_ai_verdict": product.latest_ai_verdict,
            "latest_review_status": product.latest_review_status,
            "latest_official_decision": product.latest_official_decision,
            "pending_finalization_count": product.pending_finalization_count,
        },
        "scans": [
            {
                "id": s.id,
                "inspection_id": s.inspection_id,
                "inspection_number": s.scan_number,
                "scanned_at": str(s.scanned_at),
                "scanned_by": s.scanned_by,
                "compliance_score": s.compliance_score,
                "ai_verdict": s.ai_verdict,
                "review_status": s.review_status,
                "official_decision": s.official_decision if (reviewer or s.is_finalized) else None,
                "status_message": s.public_status,
                "violations": len(_load(s.violations_json, [])),
                "checks_failed": len([c for c in _load(s.checks_json, []) if c.get("status") == "FAIL"]),
                "checks_passed": len([c for c in _load(s.checks_json, []) if c.get("status") == "PASS"]),
                "remarks": s.remarks if reviewer else "",
                "finalized_by": s.finalized_by,
                "finalized_at": str(s.finalized_at) if s.finalized_at else None,
            }
            for s in scans
        ],
        "trend": [
            {"scanned_at": str(s.scanned_at), "score": s.compliance_score}
            for s in scans
            if s.compliance_score is not None
        ],
    }


def analyses_for_scan(db: Session, scan: ProductScan) -> list[dict]:
    """Image analyses created by the same author (the scan's enhancement/OCR evidence trail)."""
    rows = (
        db.query(ImageAnalysis)
        .filter(ImageAnalysis.organization_id == scan.organization_id)
        .order_by(ImageAnalysis.id.desc())
        .limit(5)
        .all()
    )
    return [
        {
            "id": r.id,
            "state": r.state,
            "message": r.message,
            "created_at": str(r.created_at),
            "original_url": f"/files/originals/{r.original_stored}",
            "enhanced_text_url": f"/files/processed/{r.enhanced_text_stored}",
        }
        for r in rows
    ]


def pending_finalization_count(db: Session, user) -> int:
    q = db.query(ProductScan).filter(ProductScan.review_status != STATUS_FINALIZED)
    scoped = scope_org_id(user)
    if scoped is not None:
        q = q.filter(ProductScan.organization_id == scoped)
    return q.count()
