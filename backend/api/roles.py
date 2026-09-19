"""Role-specific portals — one API surface per user category.

Authorization is enforced HERE, on the server, for every operation:

  ROLE 1  ENFORCEMENT_OFFICER  -> /enforcement/*   (regulator side)
  ROLE 2  REGULATED_ENTITY     -> /entity/*        (commercial side, own organization only)
  ROLE 3  INTERNAL_COMPLIANCE  -> /internal/*      (internal side, own organization only)

A user cannot reach another role's environment by editing a URL or calling its API: the permission
guard rejects it, and the query layer is scoped to the caller's organization. The dashboard the
frontend shows is therefore backed by data the server actually permits.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.authz import Permission, org_scope_filter, permissions_for, role_home, scope_org_id
from backend.api.deps import require_permission
from backend.database import get_db
from backend.models import (
    ExtractedField,
    Inspection,
    Organization,
    User,
    Violation,
)
from backend.models.enums import ViolationStatus
from backend.services.inspection_service import create_inspection
from backend.services.review_service import add_review_note

router = APIRouter(tags=["roles"])


class SubmissionIn(BaseModel):
    notes: str = ""


def _org(user: User, db: Session) -> dict | None:
    if not user.organization_id:
        return None
    o = db.query(Organization).filter(Organization.id == user.organization_id).first()
    if o is None:
        return None
    return {"id": o.id, "name": o.name, "kind": o.kind, "registration_no": o.registration_no}


def _identity(user: User, db: Session, portal: str) -> dict:
    return {
        "portal": portal,
        "role": user.role,
        "home": role_home(user.role),
        "permissions": sorted(permissions_for(user.role)),
        "organization": _org(user, db),
    }


def _inspection_brief(db: Session, insp: Inspection) -> dict:
    pn = (
        db.query(ExtractedField)
        .filter(ExtractedField.inspection_id == insp.id, ExtractedField.field_name == "product_name")
        .first()
    )
    return {
        "id": insp.id,
        "inspection_number": insp.inspection_number,
        "product": pn.display_value if pn else "—",
        "status": insp.status,
        "decision": insp.final_decision,
        "category": insp.category,
        "organization_id": insp.organization_id,
        "created_at": str(insp.created_at),
    }


# ------------------------------------------------------------------ ROLE 1: enforcement

@router.get("/enforcement/dashboard")
def enforcement_dashboard(user: User = Depends(require_permission(Permission.ENFORCEMENT_VIEW)),
                          db: Session = Depends(get_db)):
    inspections = db.query(Inspection).order_by(Inspection.id.desc()).all()
    open_violations = (
        db.query(Violation)
        .filter(Violation.status.in_([ViolationStatus.OPEN.value, ViolationStatus.CONFIRMED.value]))
        .all()
    )
    conflicting = (
        db.query(ExtractedField)
        .filter(ExtractedField.state == "CONFLICTING")
        .count()
    )
    return {
        "user": _identity(user, db, "enforcement"),
        "counts": {
            "inspections": len(inspections),
            "non_compliant": sum(1 for i in inspections if i.final_decision == "NON_COMPLIANT"),
            "needs_review": sum(1 for i in inspections if i.final_decision == "NEEDS_MANUAL_REVIEW"),
            "flagged_violations": len(open_violations),
            "confirmed_violations": sum(
                1 for v in open_violations if v.status == ViolationStatus.CONFIRMED.value
            ),
            "conflicting_declarations": conflicting,
        },
        "recent": [_inspection_brief(db, i) for i in inspections[:8]],
    }


@router.get("/enforcement/flagged")
def enforcement_flagged(user: User = Depends(require_permission(Permission.ENFORCEMENT_VIEW)),
                        db: Session = Depends(get_db)):
    rows = (
        db.query(Violation)
        .filter(Violation.status.in_([ViolationStatus.OPEN.value, ViolationStatus.CONFIRMED.value]))
        .order_by(Violation.id.desc())
        .limit(100)
        .all()
    )
    items = []
    for v in rows:
        insp = db.query(Inspection).filter(Inspection.id == v.inspection_id).first()
        pf = (
            db.query(ExtractedField)
            .filter(ExtractedField.inspection_id == v.inspection_id,
                    ExtractedField.field_name == "product_name")
            .first()
        )
        items.append({
            "id": v.id,
            "inspection_id": v.inspection_id,
            "inspection_number": insp.inspection_number if insp else "",
            "product": pf.display_value if pf else "—",
            "rule_number": v.rule_number,
            "title": v.title,
            "severity": v.severity,
            "status": v.status,
            "confidence": v.confidence,
            "description": v.description,
        })
    return {"items": items, "total": len(items)}


@router.get("/enforcement/records")
def enforcement_records(user: User = Depends(require_permission(Permission.ENFORCEMENT_VIEW)),
                        db: Session = Depends(get_db)):
    rows = db.query(Inspection).order_by(Inspection.id.desc()).limit(100).all()
    return {"items": [_inspection_brief(db, i) for i in rows], "total": len(rows)}


# ------------------------------------------------------------------ ROLE 2: regulated entity

@router.get("/entity/dashboard")
def entity_dashboard(user: User = Depends(require_permission(Permission.ENTITY_VIEW)),
                     db: Session = Depends(get_db)):
    scoped = org_scope_filter(db.query(Inspection), Inspection, user)
    inspections = scoped.order_by(Inspection.id.desc()).all()
    ids = [i.id for i in inspections]
    violations = (
        db.query(Violation).filter(Violation.inspection_id.in_(ids)).all() if ids else []
    )
    return {
        "user": _identity(user, db, "entity"),
        "counts": {
            "records": len(inspections),
            "compliant": sum(1 for i in inspections if i.final_decision == "COMPLIANT"),
            "non_compliant": sum(1 for i in inspections if i.final_decision == "NON_COMPLIANT"),
            "needs_review": sum(1 for i in inspections if i.final_decision == "NEEDS_MANUAL_REVIEW"),
            "open_findings": sum(1 for v in violations if v.status == ViolationStatus.OPEN.value),
        },
        "recent": [_inspection_brief(db, i) for i in inspections[:8]],
    }


@router.get("/entity/records")
def entity_records(user: User = Depends(require_permission(Permission.ENTITY_VIEW)),
                   db: Session = Depends(get_db)):
    rows = org_scope_filter(db.query(Inspection), Inspection, user).order_by(Inspection.id.desc()).all()
    return {"items": [_inspection_brief(db, i) for i in rows], "total": len(rows)}


@router.post("/entity/submissions")
def entity_submission(body: SubmissionIn,
                      user: User = Depends(require_permission(Permission.ENTITY_MANAGE)),
                      db: Session = Depends(get_db)):
    """A regulated entity files a compliance submission (a record owned by ITS organization).

    The submission is a record for review — it never decides its own compliance, and the
    organization is taken from the account, so one entity can never file against another.
    """
    if not user.organization_id:
        raise HTTPException(400, "Your account is not linked to an organization.")
    inspection = create_inspection(
        db,
        inspector=user.username,
        notes=f"Submitted by {user.username} for organization {user.organization_id}. {body.notes}".strip(),
        organization_id=user.organization_id,
    )
    return {"submission": _inspection_brief(db, inspection), "message": "Compliance submission created."}


# ------------------------------------------------------------------ ROLE 3: internal compliance

@router.get("/internal/dashboard")
def internal_dashboard(user: User = Depends(require_permission(Permission.INTERNAL_VIEW)),
                       db: Session = Depends(get_db)):
    inspections = org_scope_filter(db.query(Inspection), Inspection, user).order_by(Inspection.id.desc()).all()
    ids = [i.id for i in inspections]
    violations = db.query(Violation).filter(Violation.inspection_id.in_(ids)).all() if ids else []
    return {
        "user": _identity(user, db, "internal"),
        "counts": {
            "organization_records": len(inspections),
            "awaiting_review": sum(1 for i in inspections if i.status == "AWAITING_REVIEW"),
            "non_compliant": sum(1 for i in inspections if i.final_decision == "NON_COMPLIANT"),
            "confirmed_findings": sum(
                1 for v in violations if v.status == ViolationStatus.CONFIRMED.value
            ),
            "open_findings": sum(1 for v in violations if v.status == ViolationStatus.OPEN.value),
        },
        "recent": [_inspection_brief(db, i) for i in inspections[:8]],
    }


@router.get("/internal/reviews")
def internal_reviews(user: User = Depends(require_permission(Permission.INTERNAL_VIEW)),
                     db: Session = Depends(get_db)):
    rows = (
        org_scope_filter(db.query(Inspection), Inspection, user)
        .filter(Inspection.status == "AWAITING_REVIEW")
        .order_by(Inspection.id.desc())
        .all()
    )
    return {"items": [_inspection_brief(db, i) for i in rows], "total": len(rows)}


@router.post("/internal/reviews/{inspection_id}/note")
def internal_review_note(inspection_id: int, body: SubmissionIn,
                         user: User = Depends(require_permission(Permission.INTERNAL_MANAGE)),
                         db: Session = Depends(get_db)):
    """Record an internal review observation. The record must belong to the caller's org."""
    scoped = scope_org_id(user)
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if inspection is None or (scoped is not None and inspection.organization_id != scoped):
        raise HTTPException(404, "Inspection not found")
    if not body.notes.strip():
        raise HTTPException(422, "A note is required.")
    add_review_note(db, inspection, user.username, f"[internal] {body.notes.strip()}")
    return {"message": "Internal review note recorded."}
