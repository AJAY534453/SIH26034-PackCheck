"""Product scan repository, compliance history, Rule 7 font-size compliance and calibration.

Read endpoints require ``compliance.view``; finalization requires ``compliance.finalize`` (the
"higher official" capability). Organization scope is applied in the query, so a regulated entity or
internal-compliance user only ever sees their own organization's scans.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import audit
from backend.api.deps import require_any_permission, require_permission
from backend.authz import Permission, scope_org_id
from backend.database import get_db
from backend.models import Inspection, ProductScan, RuleEvaluation, User
from backend.rules.font_size import all_tables
from backend.rules.registry import get_active_versions
from backend.services import repository_service as repo
from backend.services.font_service import build_font_measurement

router = APIRouter(prefix="/repository", tags=["repository"])


class RemarksIn(BaseModel):
    remarks: str = ""


class FinalizeIn(BaseModel):
    decision: str  # COMPLIANT | NON_COMPLIANT | NEEDS_MANUAL_REVIEW
    remarks: str


class CalibrationIn(BaseModel):
    """A physical scale for this inspection.

    Supply EITHER ``px_per_mm`` directly, OR ``reference_mm`` together with ``reference_px`` (the
    pixel span of a known printed length), OR ``panel_width_mm``/``panel_height_mm`` (the measured
    principal display panel). Everything is optional so an operator can add what they measured.
    """

    px_per_mm: float | None = None
    reference_mm: float | None = None
    reference_px: float | None = None
    panel_width_mm: float | None = None
    panel_height_mm: float | None = None
    pdp_area_cm2: float | None = None
    packaging_form: str = "normal"  # normal | moulded
    note: str = ""


def _scoped_inspection(db: Session, inspection_id: int, user: User) -> Inspection:
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    scoped = scope_org_id(user)
    if inspection is None or (scoped is not None and inspection.organization_id != scoped):
        raise HTTPException(404, "Inspection not found")
    return inspection


def _scoped_scan(db: Session, scan_id: int, user: User) -> ProductScan:
    scan = db.query(ProductScan).filter(ProductScan.id == scan_id).first()
    scoped = scope_org_id(user)
    if scan is None or (scoped is not None and scan.organization_id != scoped):
        raise HTTPException(404, "Scan not found")
    return scan


@router.get("/scans")
def list_scans(
    q: str = "",
    review_status: str = "",
    verdict: str = "",
    category: str = "",
    min_score: float | None = None,
    max_score: float | None = None,
    below_coverage_floor: bool = False,
    sort: str = "recent",
    page: int = 1,
    page_size: int = 24,
    user: User = Depends(require_permission(Permission.COMPLIANCE_VIEW)),
    db: Session = Depends(get_db),
):
    """Search / filter / sort the scanned-product repository."""
    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    page_size = max(1, min(page_size, 100))
    if sort not in ("recent", "oldest", "score_desc", "score_asc", "name"):
        raise HTTPException(422, "sort must be one of recent, oldest, score_desc, score_asc, name")
    return repo.list_scans(
        db,
        user=user,
        query=q,
        review_status=review_status,
        verdict=verdict,
        category=category,
        min_score=min_score,
        max_score=max_score,
        below_coverage_floor=below_coverage_floor,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get("/summary")
def summary(
    user: User = Depends(require_permission(Permission.COMPLIANCE_VIEW)),
    db: Session = Depends(get_db),
):
    """Headline repository counters, scoped to the caller."""
    data = repo.list_scans(db, user=user, page=1, page_size=1)
    return {
        "total_scans": data["total"],
        "facets": data["facets"],
        "pending_finalization": data["pending_finalization"],
        "finalized": data["finalized"],
    }


@router.get("/scans/{scan_id}")
def scan_detail(
    scan_id: int,
    user: User = Depends(require_permission(Permission.COMPLIANCE_VIEW)),
    db: Session = Depends(get_db),
):
    scan = _scoped_scan(db, scan_id, user)
    payload = repo.scan_view(db, scan, user=user, include_history=True)
    payload["image_url"] = f"/files/originals/{scan.image_filename}" if scan.image_filename else ""
    payload["analyses"] = repo.analyses_for_scan(db, scan)
    return payload


@router.post("/scans/{scan_id}/remarks")
def set_remarks(
    scan_id: int,
    body: RemarksIn,
    user: User = Depends(require_permission(Permission.COMPLIANCE_FINALIZE)),
    db: Session = Depends(get_db),
):
    scan = _scoped_scan(db, scan_id, user)
    updated = repo.add_remarks(db, scan, user.username, body.remarks)
    return {"message": "Remarks saved", "scan": repo.scan_view(db, updated, user=user)}


@router.post("/scans/{scan_id}/finalize")
def finalize(
    scan_id: int,
    body: FinalizeIn,
    user: User = Depends(require_permission(Permission.COMPLIANCE_FINALIZE)),
    db: Session = Depends(get_db),
):
    """Record the OFFICIAL final decision on an automated compliance review."""
    scan = _scoped_scan(db, scan_id, user)
    if not (body.remarks or "").strip():
        raise HTTPException(422, "A remark is required to finalize a compliance result")
    try:
        updated = repo.finalize_scan(
            db, scan, decision=body.decision, remarks=body.remarks, reviewer=user.username
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"message": "Official decision recorded", "scan": repo.scan_view(db, updated, user=user)}


@router.post("/backfill")
def backfill(
    user: User = Depends(require_permission(Permission.USERS_MANAGE)),
    db: Session = Depends(get_db),
):
    """Build repository rows for already-processed inspections that predate the repository.

    Idempotent (a scan row is created once per inspection) and evidence-bound: the score and verdict
    are computed from each inspection's stored rule evaluations, exactly as a new scan would be.
    """
    candidates = (
        db.query(Inspection)
        .filter(Inspection.status.in_(["AWAITING_REVIEW", "COMPLETED"]))
        .order_by(Inspection.id)
        .all()
    )
    created: list[str] = []
    for inspection in candidates:
        if db.query(ProductScan).filter(ProductScan.inspection_id == inspection.id).first() is not None:
            continue
        if not db.query(RuleEvaluation).filter(RuleEvaluation.inspection_id == inspection.id).count():
            continue
        scan = repo.refresh_scan(db, inspection)
        created.append(f"{scan.scan_number}:{scan.compliance_score}%")
    audit.log_action(user.username, "repository_backfill", after=f"{len(created)} scan(s)")
    return {"created": len(created), "details": created}


@router.get("/products/{product_id}")
def product_history(
    product_id: int,
    user: User = Depends(require_permission(Permission.COMPLIANCE_VIEW)),
    db: Session = Depends(get_db),
):
    """A product with its complete scan / compliance history (oldest first)."""
    data = repo.product_history(db, product_id, user=user)
    if data is None:
        raise HTTPException(404, "Product not found")
    return data


@router.get("/font-size/{inspection_id}")
def font_size_compliance(
    inspection_id: int,
    user: User = Depends(require_permission(Permission.COMPLIANCE_VIEW)),
    db: Session = Depends(get_db),
):
    """Rule 7 font-size compliance detail: required / detected / satisfied / legal reference."""
    inspection = _scoped_inspection(db, inspection_id, user)
    versions = get_active_versions(db)
    params = next((rv.params or {} for rv in versions.values() if rv.check_type == "font_size"), {})
    evaluation = (
        db.query(RuleEvaluation)
        .filter(RuleEvaluation.inspection_id == inspection.id, RuleEvaluation.check_type == "font_size")
        .first()
    )
    detail: dict = {}
    if evaluation is not None:
        try:
            detail = json.loads(evaluation.detail or "{}")
        except (json.JSONDecodeError, TypeError):
            detail = {}
    return {
        "inspection_id": inspection.id,
        "inspection_number": inspection.inspection_number,
        "status": evaluation.status if evaluation else "NOT_EVALUATED",
        "reason": evaluation.reason if evaluation else "This inspection has not been processed yet.",
        "observed": evaluation.observed if evaluation else "",
        "confidence": evaluation.confidence if evaluation else 0.0,
        "required_mm": detail.get("required_mm"),
        "required_reference": detail.get("required_reference"),
        "detected_mm": detail.get("detected_mm"),
        "detected_field": detail.get("detected_field"),
        "detected_text": detail.get("detected_text"),
        "satisfied": detail.get("satisfied"),
        "measurements": detail.get("measurements", []),
        "smallest_line": detail.get("smallest_line"),
        "method": detail.get("method"),
        "px_per_mm": detail.get("px_per_mm"),
        "px_per_mm_source": detail.get("px_per_mm_source"),
        "px_per_mm_note": detail.get("px_per_mm_note"),
        "panel_area_cm2": detail.get("panel_area_cm2"),
        "panel_area_source": detail.get("panel_area_source"),
        "panel_area_note": detail.get("panel_area_note"),
        "packaging_form": detail.get("packaging_form"),
        "quantity_family": detail.get("quantity_family"),
        "applicable_table": detail.get("applicable_table"),
        "required_table": detail.get("required_table"),
        "width_ratio_ok": detail.get("width_ratio_ok"),
        "min_width_ratio": detail.get("min_width_ratio"),
        "cap_height_ratio": detail.get("cap_height_ratio"),
        "exempt_note": detail.get("exempt_note") or params.get("exempt_rule7_5", ""),
        "tables": detail.get("tables") or all_tables(params),
        "calibration": {
            "px_per_mm": inspection.calibration_px_per_mm,
            "source": inspection.calibration_source,
            "note": inspection.calibration_note,
            "panel_width_mm": inspection.panel_width_mm,
            "panel_height_mm": inspection.panel_height_mm,
            "pdp_area_cm2": inspection.pdp_area_cm2,
            "packaging_form": inspection.packaging_form,
        },
        "source_reference": "LM (PC) Rules, 2011 — Rule 7(2)-(3), Table-I / Table-II "
                            "(as substituted by G.S.R. 629(E) dated 23.06.2017)",
    }


@router.post("/inspections/{inspection_id}/calibration")
def set_calibration(
    inspection_id: int,
    body: CalibrationIn,
    user: User = Depends(require_any_permission(Permission.INSPECTION_REVIEW, Permission.COMPLIANCE_FINALIZE)),
    db: Session = Depends(get_db),
):
    """Record the physical scale for an inspection and re-evaluate the Rule 7 check immediately."""
    inspection = _scoped_inspection(db, inspection_id, user)

    px_per_mm = body.px_per_mm
    source = "manual_px_per_mm"
    if px_per_mm is None and body.reference_mm and body.reference_px:
        if body.reference_mm <= 0 or body.reference_px <= 0:
            raise HTTPException(422, "reference_mm and reference_px must both be greater than zero")
        px_per_mm = float(body.reference_px) / float(body.reference_mm)
        source = f"reference_length({body.reference_mm:g} mm ≙ {body.reference_px:g} px)"
    if px_per_mm is not None and px_per_mm <= 0:
        raise HTTPException(422, "px_per_mm must be greater than zero")
    if (
        px_per_mm is None
        and not (body.panel_width_mm and body.panel_height_mm)
        and not body.pdp_area_cm2
    ):
        raise HTTPException(
            422,
            "Provide a scale (px_per_mm or reference_mm + reference_px), or the measured panel "
            "width × height in mm, or the panel area in cm².",
        )
    form = (body.packaging_form or "normal").lower()
    if form not in ("normal", "moulded"):
        raise HTTPException(422, "packaging_form must be 'normal' or 'moulded'")

    inspection.calibration_px_per_mm = px_per_mm
    if px_per_mm is not None:
        inspection.calibration_source = source
    inspection.calibration_note = body.note or ""
    inspection.panel_width_mm = body.panel_width_mm
    inspection.panel_height_mm = body.panel_height_mm
    inspection.pdp_area_cm2 = body.pdp_area_cm2
    inspection.packaging_form = form
    db.commit()
    audit.log_action(
        user.username,
        "measurement_calibration",
        inspection.inspection_number,
        after=f"px_per_mm={px_per_mm}; panel={body.panel_width_mm}x{body.panel_height_mm} mm; "
              f"form={form}",
        reason=body.note[:200],
    )

    # Re-evaluate deterministically: a new measurement can change the Rule 7 result and therefore
    # the score, the verdict and the repository row.
    from backend.services.review_service import _reevaluate_after_manual_change

    _reevaluate_after_manual_change(db, inspection)
    db.refresh(inspection)
    scan = db.query(ProductScan).filter(ProductScan.inspection_id == inspection.id).first()
    return {
        "message": "Calibration recorded and the rules re-evaluated",
        "calibration": {
            "px_per_mm": inspection.calibration_px_per_mm,
            "source": inspection.calibration_source,
            "panel_width_mm": inspection.panel_width_mm,
            "panel_height_mm": inspection.panel_height_mm,
            "pdp_area_cm2": inspection.pdp_area_cm2,
            "packaging_form": inspection.packaging_form,
        },
        "scan_id": scan.id if scan else None,
    }


@router.get("/inspections/{inspection_id}/measurement")
def measurement_preview(
    inspection_id: int,
    user: User = Depends(require_permission(Permission.COMPLIANCE_VIEW)),
    db: Session = Depends(get_db),
):
    """What the measurement layer can currently see — calibration, panel area and per-field sizes."""
    from backend.models import ExtractedField, InspectionImage

    inspection = _scoped_inspection(db, inspection_id, user)
    rows = db.query(ExtractedField).filter(ExtractedField.inspection_id == inspection.id).all()
    images = (
        db.query(InspectionImage)
        .filter(InspectionImage.inspection_id == inspection.id)
        .order_by(InspectionImage.id)
        .all()
    )
    versions = get_active_versions(db)
    params = next((rv.params or {} for rv in versions.values() if rv.check_type == "font_size"), {})
    measurement = build_font_measurement(
        inspection,
        {r.field_name: r for r in rows},
        images,
        declaration_fields=params.get("declaration_fields"),
    )
    return measurement
