"""Protected file serving + dashboard + products + violations + reports + rules endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend import audit
from backend.api.deps import get_current_user, require_permission
from backend.authz import Permission, has_permission, org_scope_filter, scope_org_id
from backend.config import settings
from backend.database import get_db
from backend.ai import ai_status
from backend.models import (
    Bill,
    Complaint,
    Evidence,
    ExtractedField,
    GroceryItem,
    ImageAnalysis,
    Inspection,
    InspectionImage,
    Product,
    Report,
    Rule,
    RuleVersion,
    User,
    Violation,
)
from backend.models.enums import FieldState, ViolationStatus
from backend.rules.font_size import all_tables
from backend.services.report_service import generate_report, render_report_html

router = APIRouter(tags=["meta"])


def _scoped_inspection_ids(db: Session, user: User) -> list[int] | None:
    """Inspection ids visible to this user, or None when the user is unrestricted.

    Used to confine violation / report queries (which reference an inspection rather than
    carrying their own organization) to the caller's organization.
    """
    scoped = scope_org_id(user)
    if scoped is None:
        return None
    rows = db.query(Inspection.id).filter(Inspection.organization_id == scoped).all()
    return [r[0] for r in rows]


# ---------- protected files ----------

# Every stored artifact is reachable ONLY through the record that owns it. The route resolves the
# owning row and then applies that record's permission and organization scope, so "authenticated"
# is never enough: a regulated entity cannot read another organization's evidence by filename.
_FILE_KINDS = {
    "originals",
    "processed",
    "crops",
    "reports",
    "bills",
}

# Permission required per artifact family (the record's own permission, not a generic one).
_FILE_PERMISSION = {
    "originals": Permission.INSPECTIONS_VIEW,
    "processed": Permission.INSPECTIONS_VIEW,
    "crops": Permission.INSPECTIONS_VIEW,
    "reports": Permission.REPORTS_VIEW,
    "bills": Permission.TOOLS_USE,
}


def _inspection_for_stored_file(db: Session, kind: str, filename: str) -> int | None:
    """The inspection this artifact belongs to, or None when nothing claims it."""
    if kind == "reports":
        row = db.query(Report).filter(Report.stored_filename == filename).first()
        return row.inspection_id if row is not None else None
    image = db.query(InspectionImage).filter(InspectionImage.stored_filename == filename).first()
    if image is not None:
        return image.inspection_id
    evidence = db.query(Evidence).filter(Evidence.stored_filename == filename).first()
    return evidence.inspection_id if evidence is not None else None


def _analysis_for_stored_file(db: Session, filename: str) -> ImageAnalysis | None:
    """The image-analysis row that produced this artifact (original or either enhancement)."""
    return (
        db.query(ImageAnalysis)
        .filter(
            or_(
                ImageAnalysis.original_stored == filename,
                ImageAnalysis.enhanced_full_stored == filename,
                ImageAnalysis.enhanced_text_stored == filename,
            )
        )
        .first()
    )


def _authorize_stored_file(db: Session, kind: str, filename: str, user: User) -> None:
    """Raise 404 unless the caller may read the record that owns this artifact.

    404 (not 403) is deliberate: an unauthorized caller must not learn that a file exists.
    """
    scoped = scope_org_id(user)
    permission = _FILE_PERMISSION[kind]

    # --- scans of a bill: owned by its organization / creator ---------------------------------
    if kind == "bills":
        bill = db.query(Bill).filter(Bill.stored_filename == filename).first()
        if bill is None or not has_permission(user, permission):
            raise HTTPException(404, "Not found")
        if scoped is not None and bill.organization_id != scoped:
            raise HTTPException(404, "Not found")
        return

    # --- artifacts owned by an INSPECTION (evidence, crops, rendered reports) -----------------
    inspection_id = _inspection_for_stored_file(db, kind, filename)
    if inspection_id is None:
        # --- artifacts owned by an image ANALYSIS (original + enhanced representations) --------
        analysis = _analysis_for_stored_file(db, filename)
        if analysis is None:
            # Nothing claims this file: it cannot be attributed to an owner, so it is not served.
            audit.log_action(user.username, "file_access_denied", reason=f"unowned {kind}/{filename}")
            raise HTTPException(404, "Not found")
        if not has_permission(user, Permission.ANALYSIS_RUN):
            audit.log_action(user.username, "file_access_denied", reason=f"{kind}/{filename}")
            raise HTTPException(404, "Not found")
        if scoped is not None and analysis.organization_id != scoped:
            audit.log_action(user.username, "file_access_denied", reason=f"foreign {kind}/{filename}")
            raise HTTPException(404, "Not found")
        return

    if not has_permission(user, permission):
        audit.log_action(user.username, "file_access_denied", reason=f"{kind}/{filename}")
        raise HTTPException(404, "Not found")
    if scoped is not None:
        owner = db.query(Inspection.organization_id).filter(Inspection.id == inspection_id).first()
        if owner is None or owner[0] != scoped:
            audit.log_action(user.username, "file_access_denied", reason=f"foreign {kind}/{filename}")
            raise HTTPException(404, "Not found")


@router.get("/files/{kind}/{filename}")
def get_file(
    kind: str,
    filename: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Path-safe, auth-protected, OWNERSHIP-checked evidence file access.

    Auth is via the Authorization header or the HttpOnly session cookie. Credentials are never
    accepted in the query string, and a file whose owning record is outside the caller's permission
    or organization scope is reported as not found (no existence leak).
    """
    if kind not in _FILE_KINDS:
        raise HTTPException(404, "Not found")
    safe = Path(filename).name  # strips any traversal attempt
    path = settings_storage() / kind / safe
    if not path.is_file():
        raise HTTPException(404, "Not found")
    _authorize_stored_file(db, kind, safe, user)
    media = "application/pdf" if path.suffix == ".pdf" else "image/png" if path.suffix == ".png" else "image/jpeg"
    # `no-store` is deliberate: the browser cache is keyed by URL, not by session, so a cached
    # evidence image would stay readable after a logout or an account switch on the same machine.
    return FileResponse(
        str(path),
        media_type=media,
        headers={"Cache-Control": "private, no-store"},
    )


def _scoped_scan_rows(db: Session, user: User):
    """Product-scan rows visible to the caller (organization scope applied in the query)."""
    from backend.models import ProductScan

    query = db.query(ProductScan)
    scoped = scope_org_id(user)
    if scoped is not None:
        query = query.filter(ProductScan.organization_id == scoped)
    return query.all()


def settings_storage() -> Path:
    from backend.config import settings

    return settings.STORAGE_DIR


# ---------- dashboard ----------

@router.get("/dashboard/stats")
def dashboard_stats(user: User = Depends(require_permission(Permission.DASHBOARD_VIEW)), db: Session = Depends(get_db)):
    # Organization isolation applies to every count on this page for scoped roles.
    inspections = org_scope_filter(db.query(Inspection), Inspection, user).all()
    visible_ids = _scoped_inspection_ids(db, user)
    total = len(inspections)
    compliant = sum(1 for i in inspections if i.final_decision == "COMPLIANT")
    non_compliant = sum(1 for i in inspections if i.final_decision == "NON_COMPLIANT")
    needs_review = sum(1 for i in inspections if i.final_decision == "NEEDS_MANUAL_REVIEW")
    # Product count is scoped: a regulated entity must not learn how many products exist elsewhere.
    if visible_ids is None:
        products = db.query(Product).count()
    else:
        scoped_product_ids = {
            row.product_id
            for row in db.query(Inspection.product_id).filter(Inspection.id.in_(visible_ids)).all()
            if row.product_id
        }
        products = len(scoped_product_ids)

    if visible_ids is None:
        violations = db.query(Violation).count()
        reports = db.query(Report).count()
        violation_rows = db.query(Violation).all()
    else:
        violations = db.query(Violation).filter(Violation.inspection_id.in_(visible_ids)).count()
        reports = db.query(Report).filter(Report.inspection_id.in_(visible_ids)).count()
        violation_rows = db.query(Violation).filter(Violation.inspection_id.in_(visible_ids)).all()

    # common violation types
    by_rule: dict[str, int] = {}
    for v in violation_rows:
        key = v.title or v.rule_number or "Other"
        by_rule[key] = by_rule.get(key, 0) + 1
    common = sorted(by_rule.items(), key=lambda kv: kv[1], reverse=True)[:6]

    # trend: inspections per day (last 14 days with data)
    trend: dict[str, int] = {}
    for i in inspections:
        day = str(i.created_at)[:10]
        trend[day] = trend.get(day, 0) + 1
    trend_list = [{"date": d, "count": c} for d, c in sorted(trend.items())[-14:]]

    recent = (
        db.query(Inspection)
        .order_by(Inspection.id.desc())
        .limit(8)
        .all()
    )
    recent_items = []
    for i in recent:
        pn = (
            db.query(ExtractedField)
            .filter(ExtractedField.inspection_id == i.id, ExtractedField.field_name == "product_name")
            .first()
        )
        recent_items.append(
            {
                "id": i.id,
                "inspection_number": i.inspection_number,
                "product": pn.display_value if pn else "—",
                "date": str(i.created_at)[:19],
                "inspector": i.inspector,
                "decision": i.final_decision,
                "status": i.status,
            }
        )
    review_queue = [
        {"id": i.id, "inspection_number": i.inspection_number, "decision": i.final_decision}
        for i in inspections
        if i.status == "AWAITING_REVIEW"
    ][:8]

    # Conflicts and open findings are the operator's highest-priority work, so surface them
    # explicitly rather than hiding them inside the totals. All counts are live DB queries.
    conflicting_q = db.query(ExtractedField).filter(ExtractedField.state == FieldState.CONFLICTING.value)
    if visible_ids is not None:
        conflicting_q = conflicting_q.filter(ExtractedField.inspection_id.in_(visible_ids))
    conflicting_rows = conflicting_q.all()
    conflict_fields: dict[int, list[str]] = {}
    for f in conflicting_rows:
        conflict_fields.setdefault(f.inspection_id, []).append(f.field_name)
    conflict_queue = [
        {
            "id": i.id,
            "inspection_number": i.inspection_number,
            "fields": sorted(conflict_fields[i.id]),
        }
        for i in inspections
        if i.id in conflict_fields
    ][:8]
    open_q = db.query(Violation).filter(Violation.status == ViolationStatus.OPEN.value)
    if visible_ids is not None:
        open_q = open_q.filter(Violation.inspection_id.in_(visible_ids))
    open_violations = open_q.count()

    # The SCAN → EXTRACT → VERIFY → ALERT → REPORT chain, each stage backed by a live count so
    # the dashboard shows what the product actually is rather than a decorative diagram.
    pipeline_items = {
        "images_scanned": db.query(InspectionImage).count(),
        "fields_detected": db.query(ExtractedField).filter(
            ExtractedField.state == FieldState.DETECTED.value
        ).count(),
        "fields_uncertain": db.query(ExtractedField).filter(
            ExtractedField.state == FieldState.UNCERTAIN.value
        ).count(),
        "awaiting_review": len(review_queue),
    }
    confirmed_violations = (
        db.query(Violation).filter(Violation.status == ViolationStatus.CONFIRMED.value).count()
    )

    # ---------- product scan repository + automated compliance review counters ----------
    # These come from the repository service so the numbers obey the same data scope as the
    # repository page itself (a scoped role sees only its own organization's scans).
    from backend.services import repository_service

    scan_summary = repository_service.list_scans(db, user=user, page=1, page_size=1)
    scoped_rows = _scoped_scan_rows(db, user)
    scores = [row.compliance_score for row in scoped_rows if row.compliance_score is not None]
    coverages = [row.coverage_score for row in scoped_rows if row.coverage_score is not None]
    average_score = round(sum(scores) / len(scores), 1) if scores else None
    average_coverage = round(sum(coverages) / len(coverages), 1) if coverages else None
    below_threshold = sum(1 for s in scores if s < settings.AI_COMPLIANCE_THRESHOLD)
    below_coverage_floor = sum(1 for c in coverages if c < settings.AI_COVERAGE_FLOOR)

    # Consumer-tool counters. Every number is a live query — the dashboard never shows a
    # decorative statistic. Grocery alerts count only EXPIRED / EXPIRING_SOON rows, which exist
    # only for products the user explicitly added to the tracker.
    from backend.services import tools_service as tools_svc

    bills = org_scope_filter(db.query(Bill), Bill, user).order_by(Bill.id.desc()).limit(200).all()
    grocery_rows = org_scope_filter(db.query(GroceryItem), GroceryItem, user).all()
    grocery_views = [tools_svc.grocery_view(g) for g in grocery_rows]
    grocery_alerts = [g for g in grocery_views if g["alert"] in {"EXPIRED", "EXPIRING_SOON"}]
    complaint_query = org_scope_filter(db.query(Complaint), Complaint, user)
    complaint_rows = complaint_query.all()
    open_complaints = [c for c in complaint_rows if c.status != "RESOLVED"]

    return {
        "pipeline": pipeline_items,
        "ai": ai_status(),
        "bills": org_scope_filter(db.query(Bill), Bill, user).count(),
        "bills_total": org_scope_filter(db.query(Bill), Bill, user).count(),
        "potential_price_differences": [
            {
                "id": b.id,
                "product": b.product_name or "—",
                "store": b.store_name or "—",
                "billed": b.billed_price,
                "mrp": b.mrp,
                "difference": b.price_difference,
                "status": b.comparison_status,
                "source": b.extraction_source,
            }
            for b in bills
            if b.comparison_status == "POTENTIAL_PRICE_DIFFERENCE"
        ][:6],
        "grocery_items": len(grocery_views),
        "grocery_alerts": grocery_alerts[:6],
        "grocery_alert_count": len(grocery_alerts),
        "complaints": len(complaint_rows),
        "open_complaints": len(open_complaints),
        "recent_complaints": [tools_svc.complaint_view(c) for c in complaint_query.order_by(Complaint.id.desc()).limit(5).all()],
        "total_inspections": total,
        "compliant": compliant,
        "non_compliant": non_compliant,
        "needs_review": needs_review,
        "products": products,
        "violations": violations,
        "reports": reports,
        "conflicts": len(conflicting_rows),
        "open_violations": open_violations,
        "confirmed_violations": confirmed_violations,
        "product_scans": scan_summary["total"],
        "pending_finalization": scan_summary["pending_finalization"],
        "finalized_scans": scan_summary["finalized"],
        "scan_facets": scan_summary["facets"],
        "average_compliance_score": average_score,
        "average_coverage_score": average_coverage,
        "scans_below_threshold": below_threshold,
        "scans_below_coverage_floor": below_coverage_floor,
        "compliance_threshold": settings.AI_COMPLIANCE_THRESHOLD,
        "coverage_floor": settings.AI_COVERAGE_FLOOR,
        "pending_finalization_queue": [
            {
                "id": card["id"],
                "inspection_id": card["inspection_id"],
                "inspection_number": card["inspection_number"],
                "product": card["product_name"],
                "score": card["compliance_score"],
                "status_text": card["status_text"],
            }
            for card in repository_service.list_scans(
                db, user=user, review_status="PENDING_FINALIZATION", sort="score_asc", page=1, page_size=6
            )["items"]
        ],
        "common_violations": [{"type": k, "count": v} for k, v in common],
        "trend": trend_list,
        "recent_inspections": recent_items,
        "review_queue": review_queue,
        "conflict_queue": conflict_queue,
    }


# ---------- products ----------

@router.get("/products")
def list_products(user: User = Depends(require_permission(Permission.INSPECTIONS_VIEW)), db: Session = Depends(get_db)):
    products = db.query(Product).order_by(Product.last_seen.desc()).all()
    # A scoped role only sees products that its own organization's inspections reference.
    visible_ids = _scoped_inspection_ids(db, user)
    if visible_ids is not None:
        owned = {
            i.product_id
            for i in org_scope_filter(db.query(Inspection), Inspection, user).all()
            if i.product_id
        }
        products = [p for p in products if p.id in owned]
    out = []
    for p in products:
        # aggregate history from inspections attributed to this product
        insp = db.query(Inspection).filter(Inspection.product_id == p.id).all()
        out.append({
            "id": p.id,
            "name": p.name,
            "brand": p.brand,
            "category": p.category,
            "manufacturer": p.manufacturer,
            "known_net_quantities": p.known_net_quantities,
            "mrp_values": p.mrp_values,
            "first_seen": str(p.first_seen),
            "last_seen": str(p.last_seen),
            "inspections": len(insp),
        })
    # products may also exist implicitly via inspections; those get created at review time
    return {"items": out, "total": len(out)}


@router.get("/products/{product_id}")
def product_detail(product_id: int, user: User = Depends(require_permission(Permission.INSPECTIONS_VIEW)), db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    scoped = scope_org_id(user)
    if p is None:
        raise HTTPException(404, "Product not found")
    insp_q = db.query(Inspection).filter(Inspection.product_id == p.id)
    if scoped is not None:
        insp_q = insp_q.filter(Inspection.organization_id == scoped)
    insp = insp_q.order_by(Inspection.id.desc()).all()
    if scoped is not None and not insp:
        raise HTTPException(404, "Product not found")
    history = []
    for i in insp:
        violations = db.query(Violation).filter(Violation.inspection_id == i.id).count()
        history.append({
            "id": i.id,
            "inspection_number": i.inspection_number,
            "date": str(i.created_at)[:19],
            "decision": i.final_decision,
            "status": i.status,
            "violations": violations,
        })
    return {
        "id": p.id, "name": p.name, "brand": p.brand, "category": p.category,
        "manufacturer": p.manufacturer, "packer": p.packer, "importer": p.importer,
        "known_net_quantities": p.known_net_quantities, "mrp_values": p.mrp_values,
        "first_seen": str(p.first_seen), "last_seen": str(p.last_seen),
        "history": history,
    }


# ---------- violations ----------

@router.get("/violations")
def list_violations(user: User = Depends(require_permission(Permission.ENFORCEMENT_VIEW)), db: Session = Depends(get_db)):
    """Confirmed/raised findings — an enforcement-side read, not a general one."""
    violation_query = db.query(Violation)
    visible_ids = _scoped_inspection_ids(db, user)
    if visible_ids is not None:
        violation_query = violation_query.filter(Violation.inspection_id.in_(visible_ids))
    vs = violation_query.order_by(Violation.id.desc()).all()
    items = []
    for v in vs:
        insp = db.query(Inspection).filter(Inspection.id == v.inspection_id).first()
        pf = (
            db.query(ExtractedField)
            .filter(ExtractedField.inspection_id == v.inspection_id, ExtractedField.field_name == "product_name")
            .first()
        )
        items.append({
            "id": v.id,
            "inspection_id": v.inspection_id,
            "inspection_number": insp.inspection_number if insp else "",
            "product": pf.display_value if pf else "—",
            "rule_number": v.rule_number,
            "title": v.title,
            "description": v.description,
            "observed": v.observed,
            "expected": v.expected,
            "severity": v.severity,
            "status": v.status,
            "confidence": v.confidence,
            "created_at": str(v.created_at),
        })
    return {"items": items, "total": len(items)}


# ---------- reports ----------

@router.get("/reports")
def list_reports(user: User = Depends(require_permission(Permission.REPORTS_VIEW)), db: Session = Depends(get_db)):
    report_query = db.query(Report)
    visible_ids = _scoped_inspection_ids(db, user)
    if visible_ids is not None:
        report_query = report_query.filter(Report.inspection_id.in_(visible_ids))
    rs = report_query.order_by(Report.id.desc()).all()
    items = []
    for r in rs:
        insp = db.query(Inspection).filter(Inspection.id == r.inspection_id).first()
        items.append({
            "id": r.id,
            "inspection_id": r.inspection_id,
            "inspection_number": insp.inspection_number if insp else "",
            "format": r.format,
            "generated_by": r.generated_by,
            "created_at": str(r.created_at),
            "stored_filename": r.stored_filename,
        })
    return {"items": items, "total": len(items)}


@router.post("/reports/generate/{inspection_id}")
def generate(inspection_id: int, user: User = Depends(require_permission(Permission.REPORTS_GENERATE)), db: Session = Depends(get_db)):
    try:
        report = generate_report(db, inspection_id, generated_by=user.username)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"id": report.id, "stored_filename": report.stored_filename}


@router.get("/reports/html/{inspection_id}", response_class=HTMLResponse)
def report_html(inspection_id: int, user: User = Depends(require_permission(Permission.REPORTS_VIEW)), db: Session = Depends(get_db)):
    """Print-ready HTML report built from the SAME stored rows as the PDF.

    The table CSS is the wrapping contract (auto row height, ``overflow-wrap: anywhere``, repeated
    ``thead``) so long JSON/CSV-derived content expands the row instead of overlapping when the
    browser prints/saves it to PDF.
    """
    scoped = scope_org_id(user)
    insp = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if insp is None or (scoped is not None and insp.organization_id != scoped):
        raise HTTPException(404, "Inspection not found")
    try:
        return HTMLResponse(content=render_report_html(db, inspection_id))
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---------- rules ----------

@router.get("/rules")
def list_rules(user: User = Depends(require_permission(Permission.RULES_VIEW)), db: Session = Depends(get_db)):
    rules = db.query(Rule).order_by(Rule.rule_id).all()
    items = []
    for r in rules:
        rv = (
            db.query(RuleVersion)
            .filter(RuleVersion.rule_id_fk == r.id, RuleVersion.version == r.current_version)
            .first()
        )
        items.append({
            "id": r.id,
            "rule_id": r.rule_id,
            "rule_number": r.rule_number,
            "title": r.title,
            "description": r.description,
            "source_reference": r.source_reference,
            "current_version": r.current_version,
            "applicability": r.applicability,
            "status": r.status,
            "requirement": rv.requirement if rv else "",
            "effective_from": str(rv.effective_from) if rv and rv.effective_from else None,
            # How the rule is verified, plus the tabulated legal data when the check is measurable
            # (Rule 7 font size) — so the Rule Library shows the actual table in force instead of a
            # hand-typed summary.
            "check_type": rv.check_type if rv else "",
            "amendment": rv.amendment if rv else "",
            "font_size_tables": (
                all_tables(rv.params or {}) if rv and rv.check_type == "font_size" else []
            ),
            "method": (rv.params or {}).get("method", "") if rv else "",
            "exempt_note": (rv.params or {}).get("exempt_rule7_5", "") if rv else "",
        })
    return {"items": items, "total": len(items)}


@router.get("/audit")
def audit_log(action: str = "", actor: str = "", inspection: str = "", limit: int = 100, offset: int = 0,
              user: User = Depends(require_permission(Permission.AUDIT_VIEW)), db: Session = Depends(get_db)):
    """Append-only audit trail, newest first. Admin-only: it names actors and records reasons.

    The trail is written by every mutating action (login, upload, processing, field review,
    violation decision, report generation). It is read-only by design — never editable.
    """
    from backend.models import AuditLog

    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if actor:
        q = q.filter(AuditLog.actor == actor)
    if inspection:
        q = q.filter(AuditLog.inspection_id == inspection)
    total = q.count()
    rows = q.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all()
    actions = sorted({a for (a,) in db.query(AuditLog.action).distinct().all() if a})
    return {
        "items": [
            {
                "id": r.id,
                "actor": r.actor,
                "action": r.action,
                "inspection_id": r.inspection_id,
                "before": r.before,
                "after": r.after,
                "reason": r.reason,
                "created_at": str(r.created_at),
            }
            for r in rows
        ],
        "total": total,
        "actions": actions,
    }


@router.get("/rules/{rule_id_str}")
def rule_detail(rule_id_str: str, user: User = Depends(require_permission(Permission.RULES_VIEW)), db: Session = Depends(get_db)):
    rule = db.query(Rule).filter(Rule.rule_id == rule_id_str).first()
    if rule is None:
        raise HTTPException(404, "Rule not found")
    versions = db.query(RuleVersion).filter(RuleVersion.rule_id_fk == rule.id).order_by(RuleVersion.version).all()
    return {
        "id": rule.id,
        "rule_id": rule.rule_id,
        "rule_number": rule.rule_number,
        "title": rule.title,
        "description": rule.description,
        "source_reference": rule.source_reference,
        "status": rule.status,
        "versions": [
            {
                "version": v.version,
                "requirement": v.requirement,
                "amendment": v.amendment,
                "effective_from": str(v.effective_from) if v.effective_from else None,
                "check_type": v.check_type,
            }
            for v in versions
        ],
    }
