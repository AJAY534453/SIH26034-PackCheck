"""Inspection endpoints: full lifecycle from creation to review and export."""
from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.deps import require_permission
from backend.authz import Permission, org_scope_filter, scope_org_id
from backend.database import get_db
from backend.models import (
    Classification,
    Evidence,
    ExtractedField,
    Inspection,
    InspectionImage,
    ProductScan,
    Report,
    ReviewAction,
    RuleEvaluation,
    RuleVersion,
    User,
    Violation,
    VisionRegion,
)
from backend.schemas.inspection import (
    FieldOut,
    ImageOut,
    InspectionDetail,
    InspectionSummary,
    RuleEvalOut,
    ViolationOut,
)
from backend.config import settings
from backend.services.image_service import assess_image_quality
from backend.services.inspection_service import STAGES, add_image, create_inspection, process_inspection
from backend.services.review_service import (
    add_review_note,
    complete_review,
    review_field,
    review_violation,
)

router = APIRouter(prefix="/inspections", tags=["inspections"])


class ReviewFieldIn(BaseModel):
    action: str  # ACCEPT | EDIT_FIELD | ADD_FIELD | REJECT | CONFIRM_ABSENT
    field_name: str
    corrected_value: str | None = None
    reason: str = ""


class ReviewNoteIn(BaseModel):
    note: str


class FinalDecisionIn(BaseModel):
    decision: str  # COMPLIANT | NON_COMPLIANT | NEEDS_MANUAL_REVIEW
    reason: str = ""


class ViolationReviewIn(BaseModel):
    action: str  # CONFIRM | DISMISS | RESOLVE
    reason: str = ""


@router.post("", response_model=InspectionSummary)
def create(inspector_user: User = Depends(require_permission(Permission.INSPECTIONS_MANAGE)), db: Session = Depends(get_db)):
    # organization_id comes from the trusted account, never the request.
    inspection = create_inspection(
        db, inspector=inspector_user.username, organization_id=inspector_user.organization_id
    )
    return inspection


@router.get("")
def list_inspections(
    status: str | None = None,
    decision: str | None = None,
    category: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_permission(Permission.INSPECTIONS_VIEW)),
    db: Session = Depends(get_db),
):
    q = db.query(Inspection)
    # Organization isolation is applied in the QUERY: a regulated-entity / internal-compliance
    # user can never receive another organization's inspections, regardless of the UI.
    q = org_scope_filter(q, Inspection, user)
    if status:
        q = q.filter(Inspection.status == status.upper())
    if decision:
        q = q.filter(Inspection.final_decision == decision.upper())
    if category:
        q = q.filter(Inspection.category == category.upper())
    items = q.order_by(Inspection.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    total = q.count()
    # optional text search over inspection number + extracted product names
    results = []
    for i in items:
        d = InspectionSummary.model_validate(i).model_dump(mode="json")
        if search:
            pn = (
                db.query(ExtractedField)
                .filter(ExtractedField.inspection_id == i.id, ExtractedField.field_name == "product_name")
                .first()
            )
            hay = " ".join([i.inspection_number, (pn.display_value if pn else "")]).lower()
            if search.lower() not in hay:
                continue
        results.append(d)
    return {"items": results, "total": total, "page": page, "page_size": page_size}


@router.get("/stages")
def get_stages(user: User = Depends(require_permission(Permission.INSPECTIONS_VIEW))):
    return {"stages": STAGES}


@router.get("/{inspection_id}", response_model=InspectionDetail)
def get_inspection(inspection_id: int, user: User = Depends(require_permission(Permission.INSPECTIONS_VIEW)), db: Session = Depends(get_db)):
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    # A record outside the caller's organization is reported as not found (no existence leak).
    scoped = scope_org_id(user)
    if inspection is None or (scoped is not None and inspection.organization_id != scoped):
        raise HTTPException(404, "Inspection not found")
    images = db.query(InspectionImage).filter(InspectionImage.inspection_id == inspection_id).all()
    fields = db.query(ExtractedField).filter(ExtractedField.inspection_id == inspection_id).all()
    rules = db.query(RuleEvaluation).filter(RuleEvaluation.inspection_id == inspection_id).all()
    violations = db.query(Violation).filter(Violation.inspection_id == inspection_id).all()
    reviews = db.query(ReviewAction).filter(ReviewAction.inspection_id == inspection_id).order_by(ReviewAction.id).all()
    reports = db.query(Report).filter(Report.inspection_id == inspection_id).all()

    detail = InspectionDetail(
        **InspectionSummary.model_validate(inspection).model_dump(mode="json"),
        stage_status=inspection.stage_status or {},
        summary=inspection.summary or "",
        notes=inspection.notes or "",
        category_confidence=inspection.category_confidence or 0.0,
        overall_quality_score=inspection.overall_quality_score or 0.0,
        images=[],
        fields=[],
        rules=[],
        violations=[],
        review_actions=[],
        reports=[],
    ).model_dump(mode="json")
    detail["images"] = [ImageOut.model_validate(i).model_dump(mode="json") for i in images]
    detail["fields"] = [FieldOut.model_validate(f).model_dump(mode="json") for f in fields]
    detail["rules"] = [RuleEvalOut.model_validate(r).model_dump(mode="json") for r in rules]
    detail["violations"] = [ViolationOut.model_validate(v).model_dump(mode="json") for v in violations]
    detail["review_actions"] = [
        {
            "id": r.id, "action": r.action, "reviewer": r.reviewer,
            "original_value": r.original_value, "corrected_value": r.corrected_value,
            "reason": r.reason, "created_at": str(r.created_at),
        }
        for r in reviews
    ]
    detail["reports"] = [
        {"id": rp.id, "format": rp.format, "created_at": str(rp.created_at), "generated_by": rp.generated_by,
         "stored_filename": rp.stored_filename}
        for rp in reports
    ]
    # evidence summary per field
    ev = db.query(Evidence).filter(Evidence.inspection_id == inspection_id).all()
    detail["evidence"] = [
        {
            "id": e.id, "field_name": e.field_name, "kind": e.kind, "bbox": e.bbox,
            "stored_filename": e.stored_filename, "image_id": e.image_id,
            "raw_text": e.raw_text, "confidence": e.confidence,
            "extraction_method": e.extraction_method, "note": e.note or "",
            # "field" | "rule" | "vision" | "image" — lets the UI separate a visual observation
            # from a declaration crop instead of mixing them in one list.
            "related_type": e.related_type,
        }
        for e in ev
    ]
    # ---------- on-device vision observations ----------
    # Every region the vision engine retained for this scan, with its bbox in ORIGINAL pixels so
    # the evidence viewer can zoom straight to it. Statuses are read from the record, not inferred.
    regions = (
        db.query(VisionRegion)
        .filter(VisionRegion.inspection_id == inspection_id)
        .order_by(VisionRegion.image_id, VisionRegion.id)
        .all()
    )
    detail["vision"] = {
        "status": inspection.vision_status or "",
        "engine": inspection.vision_engine or "",
        "note": inspection.vision_note or "",
        "provider_status": inspection.provider_status or "",
        "provider_note": inspection.provider_note or "",
        "regions": [
            {
                "id": r.id, "image_id": r.image_id, "kind": r.kind, "label": r.label,
                "bbox": r.bbox, "text": r.text, "confidence": r.confidence,
                "prominence": r.prominence, "contrast": r.contrast, "sharpness": r.sharpness,
                "text_density": r.text_density, "engine": r.engine, "note": r.note or "",
            }
            for r in regions
        ],
    }
    cls = db.query(Classification).filter(Classification.inspection_id == inspection_id).first()
    detail["classification_signals"] = cls.signals if cls else ""
    # Rule -> evidence linking: the retained crop(s) each rule conclusion rests on, resolved from
    # what the evaluation recorded and from the rule version's own declared field list. This is what
    # turns a PASS/FAIL row into a traceable conclusion (rule → detected value → image region).
    detail["rule_evidence"] = _rule_evidence_index(db, ev, rules)
    # Analysis provenance: which engine produced the ACTIVE result, whether it is still the current
    # one, and how many times the record has been regenerated. The UI shows this rather than
    # assuming a record is up to date.
    from backend.version import ENGINE_VERSION, engine_stamp

    stamp = engine_stamp(db)
    detail["provenance"] = {
        "engine_version": inspection.pipeline_version or "",
        "current_engine": ENGINE_VERSION,
        "up_to_date": (inspection.pipeline_version or "") == ENGINE_VERSION,
        "rule_fingerprint": stamp["rule_fingerprint"],
        "rule_count": stamp["rule_count"],
        "reprocess_count": int(inspection.reprocess_count or 0),
        "last_reprocessed_at": str(inspection.last_reprocessed_at or ""),
    }

    # ---------- automated compliance review + Rule 7 measurement ----------
    # The reviewer sees the AI preliminary verdict and the human final decision as two separate
    # things; a non-reviewer is told plainly that an unfinalized result is still pending.
    from backend.services import compliance_service, repository_service

    scan = db.query(ProductScan).filter(ProductScan.inspection_id == inspection_id).first()
    if scan is not None:
        detail["compliance_review"] = repository_service.scan_view(
            db, scan, user=user, include_history=True
        )
    else:
        review = compliance_service.build_ai_review(db, inspection)
        detail["compliance_review"] = {
            **review,
            "review_status": "NOT_EVALUATED",
            "official_decision": inspection.official_decision,
            "status_message": "This inspection has not been through the automated compliance review yet.",
            "can_finalize": False,
        }
    font_eval = next((r for r in rules if (r.check_type or "") == "font_size"), None)
    detail["font_size"] = {
        "status": font_eval.status if font_eval else "NOT_EVALUATED",
        "reason": font_eval.reason if font_eval else "",
        "detail": _load_json(font_eval.detail) if font_eval else {},
    }
    detail["calibration"] = {
        "px_per_mm": inspection.calibration_px_per_mm,
        "source": inspection.calibration_source,
        "note": inspection.calibration_note,
        "panel_width_mm": inspection.panel_width_mm,
        "panel_height_mm": inspection.panel_height_mm,
        "pdp_area_cm2": inspection.pdp_area_cm2,
        "packaging_form": inspection.packaging_form,
    }
    return detail


def _rule_field_names(detail: dict, version: RuleVersion | None) -> list[str]:
    """Declaration fields a rule result depends on, named by the evaluation or the rule definition."""
    names: list[str] = []

    def add(value: object) -> None:
        text = str(value or "").strip()
        if not text:
            return
        # Placement detail entries read "mrp (FRONT_PACKAGE)" — the field name is the part before it.
        name = text.split(" (")[0].strip()
        if name and name not in names:
            names.append(name)

    if isinstance(detail.get("detected_field"), str):
        add(detail.get("detected_field"))
    for key in ("on_panel", "off_panel", "not_detected", "fields", "field_names"):
        value = detail.get(key)
        if isinstance(value, list):
            for item in value:
                add(item)
        elif isinstance(value, str):
            add(value)
    params = (version.params if version is not None else {}) or {}
    for key in ("fields", "declaration_fields", "required_fields"):
        value = params.get(key)
        if isinstance(value, list):
            for item in value:
                add(item)
    return names


def _rule_evidence_index(db: Session, evidence_rows: list, evaluations: list) -> dict:
    """Map every rule evaluation to the evidence crops its conclusion rests on.

    Deliberately data-driven: the field list comes from the evaluation's own trace and the rule
    version's declared fields, so an unrelated image is never attached to a conclusion.
    """
    by_field: dict[str, list] = {}
    for e in evidence_rows:
        by_field.setdefault(e.field_name, []).append(e)
    version_ids = {r.rule_version_id for r in evaluations if r.rule_version_id}
    versions = {
        rv.id: rv for rv in db.query(RuleVersion).filter(RuleVersion.id.in_(version_ids or [0])).all()
    }
    out: dict[str, list] = {}
    for r in evaluations:
        picks: list[dict] = []
        seen: set[int] = set()
        for name in _rule_field_names(_load_json(r.detail), versions.get(r.rule_version_id)):
            for e in by_field.get(name, []):
                if e.id in seen:
                    continue
                seen.add(e.id)
                picks.append(
                    {
                        "id": e.id,
                        "field_name": e.field_name,
                        "image_id": e.image_id,
                        "stored_filename": e.stored_filename,
                        "bbox": e.bbox,
                        "raw_text": e.raw_text or "",
                        "confidence": e.confidence or 0.0,
                        "extraction_method": e.extraction_method or "",
                        "kind": e.kind,
                    }
                )
        out[str(r.id)] = picks[:6]
    return out


def _load_json(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


@router.post("/{inspection_id}/images", response_model=ImageOut)
async def upload_image(
    inspection_id: int,
    role: str = Form("ADDITIONAL_EVIDENCE"),
    file: UploadFile = File(...),
    user: User = Depends(require_permission(Permission.INSPECTIONS_MANAGE)),
    db: Session = Depends(get_db),
):
    # Resource ownership: an inspection outside the caller's organization is reported as not found.
    scoped = scope_org_id(user)
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None or (scoped is not None and inspection.organization_id != scoped):
        raise HTTPException(404, "Inspection not found")
    if inspection.status not in ("CREATED", "FAILED", "AWAITING_REVIEW"):
        raise HTTPException(409, f"Cannot add images while inspection is {inspection.status}")
    data = await file.read()
    try:
        image = add_image(db, inspection, data, file.filename or "upload", role.upper())
    except Exception as e:
        raise HTTPException(422, str(e))
    # Assess quality immediately so the UI can warn before the scan runs (never a legal signal).
    q = assess_image_quality(settings.STORAGE_DIR / "originals" / image.stored_filename)
    image.quality_score = q["score"]
    image.quality_status = q["status"]
    image.quality_metrics = {"metrics": q.get("metrics", {}), "values": q.get("values", {})}
    db.commit()
    db.refresh(image)
    return image


@router.post("/{inspection_id}/process")
def process(
    inspection_id: int,
    refresh_ocr: bool = False,
    user: User = Depends(require_permission(Permission.INSPECTIONS_MANAGE)),
    db: Session = Depends(get_db),
):
    """Run the pipeline. Stored OCR evidence is reused by default (deterministic reprocess);
    `?refresh_ocr=true` recognises the images again."""
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None:
        raise HTTPException(404, "Inspection not found")
    if not db.query(InspectionImage).filter(InspectionImage.inspection_id == inspection_id).count():
        raise HTTPException(422, "Upload at least one package image before processing")
    updated = process_inspection(db, inspection_id, refresh_ocr=refresh_ocr)
    return {"status": updated.status, "decision": updated.final_decision, "summary": updated.summary}


@router.get("/{inspection_id}/progress")
def progress(inspection_id: int, user: User = Depends(require_permission(Permission.INSPECTIONS_VIEW)), db: Session = Depends(get_db)):
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None:
        raise HTTPException(404, "Inspection not found")
    # Progress carries the real elapsed time per stage and the perception status, so the scanning
    # UI can show what is actually happening instead of an indeterminate spinner.
    return {
        "status": inspection.status,
        "stage_status": inspection.stage_status or {},
        "stage_timings": inspection.stage_timings or {},
        "duration_ms": inspection.duration_ms or 0,
        "vision_status": inspection.vision_status or "",
        "vision_engine": inspection.vision_engine or "",
        "provider_status": inspection.provider_status or "",
        "decision": inspection.final_decision,
        "official_decision": inspection.official_decision,
    }


@router.post("/{inspection_id}/review/field")
def review_field_endpoint(inspection_id: int, body: ReviewFieldIn,
                          user: User = Depends(require_permission(Permission.INSPECTION_REVIEW)),
                          db: Session = Depends(get_db)):
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None:
        raise HTTPException(404, "Inspection not found")
    try:
        return review_field(db, inspection, body.field_name, body.action.upper(), body.corrected_value,
                            reviewer=user.username, reason=body.reason)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/{inspection_id}/review/note")
def review_note_endpoint(inspection_id: int, body: ReviewNoteIn,
                         user: User = Depends(require_permission(Permission.INSPECTION_REVIEW)),
                         db: Session = Depends(get_db)):
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None:
        raise HTTPException(404, "Inspection not found")
    add_review_note(db, inspection, user.username, body.note)
    return {"message": "Note recorded"}


@router.post("/{inspection_id}/review/final")
def final_decision_endpoint(inspection_id: int, body: FinalDecisionIn,
                            user: User = Depends(require_permission(Permission.INSPECTION_REVIEW)),
                            db: Session = Depends(get_db)):
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None:
        raise HTTPException(404, "Inspection not found")
    try:
        complete_review(db, inspection, user.username, body.decision.upper(), body.reason)
    except ValueError:
        raise HTTPException(422, "Decision must be COMPLIANT, NON_COMPLIANT or NEEDS_MANUAL_REVIEW")
    return {"decision": inspection.final_decision, "status": inspection.status}


@router.post("/{inspection_id}/review/violation/{violation_id}")
def review_violation_endpoint(inspection_id: int, violation_id: int, body: ViolationReviewIn,
                              user: User = Depends(require_permission(Permission.INSPECTION_REVIEW)),
                              db: Session = Depends(get_db)):
    """Human decision on one violation: CONFIRM (agrees), DISMISS (not a violation, needs a
    reason) or RESOLVE (rectified). The deterministic engine is re-run so the final decision
    reflects the human call instead of silently overriding it."""
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None:
        raise HTTPException(404, "Inspection not found")
    try:
        return review_violation(db, inspection, violation_id, body.action, user.username, body.reason)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/{inspection_id}/export")
def export_inspection(inspection_id: int, format: str = "json",
                      user: User = Depends(require_permission(Permission.INSPECTIONS_VIEW)), db: Session = Depends(get_db)):
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None:
        raise HTTPException(404, "Inspection not found")
    detail = get_inspection(inspection_id, user=user, db=db)  # reuse assembled payload
    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["field_name", "value", "confidence", "state", "source_engine", "note"])
        for f in detail["fields"]:
            has_value = bool((f.get("display_value") or "").strip())
            conf = f.get("confidence") if has_value else None  # no fake confidence on empty values
            writer.writerow([
                f["field_name"], f.get("display_value") or "", conf, f.get("state"),
                f.get("source_engine"), f.get("uncertainty_reason"),
            ])
        writer.writerow([])
        writer.writerow(["rule_number", "title", "status", "reason"])
        for r in detail["rules"]:
            writer.writerow([r["rule_number"], r["title"], r["status"], r["reason"]])
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={inspection.inspection_number}.csv"},
        )
    return StreamingResponse(
        json.dumps(detail, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={inspection.inspection_number}.json"},
    )
