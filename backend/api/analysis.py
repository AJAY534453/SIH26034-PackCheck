"""Image analysis endpoints — capture/upload, enhance, OCR and structured extraction.

Every analysis keeps the original image (immutable) plus both enhanced representations and the
recognised text, so the extracted information stays associated with the image it came from.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.api.deps import require_permission
from backend.authz import Permission, org_scope_filter, scope_org_id
from backend.database import get_db
from backend.models import ImageAnalysis, User
from backend.services import analysis_service as svc
from backend.services.image_service import UploadValidationError

router = APIRouter(prefix="/analysis", tags=["analysis"])

ALLOWED_MODES = {"auto", "full", "text"}


@router.post("")
async def create_analysis(
    file: UploadFile = File(...),
    mode: str = Form("auto"),
    user: User = Depends(require_permission(Permission.ANALYSIS_RUN)),
    db: Session = Depends(get_db),
):
    """Analyse one captured/uploaded image.

    ``mode`` is a hint (``auto`` | ``full`` | ``text``); both enhancement representations are
    always produced so the user can compare them.
    """
    chosen = (mode or "auto").lower()
    if chosen not in ALLOWED_MODES:
        raise HTTPException(422, f"mode must be one of {sorted(ALLOWED_MODES)}")
    data = await file.read()
    if not data:
        raise HTTPException(422, "The uploaded file is empty.")
    try:
        row = svc.analyze_and_persist(
            db,
            data=data,
            filename=file.filename or "capture.png",
            created_by=user.username,
            organization_id=user.organization_id,
            mode=chosen,
        )
    except UploadValidationError as exc:
        raise HTTPException(422, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"analysis": svc.analysis_view(row)}


@router.get("")
def list_analyses(user: User = Depends(require_permission(Permission.ANALYSIS_RUN)),
                  db: Session = Depends(get_db)):
    rows = (
        org_scope_filter(db.query(ImageAnalysis), ImageAnalysis, user)
        .order_by(ImageAnalysis.id.desc())
        .limit(50)
        .all()
    )
    return {"items": [svc.analysis_view(r) for r in rows], "total": len(rows)}


@router.get("/{analysis_id}")
def get_analysis(analysis_id: int, user: User = Depends(require_permission(Permission.ANALYSIS_RUN)),
                 db: Session = Depends(get_db)):
    scoped = scope_org_id(user)
    row = db.query(ImageAnalysis).filter(ImageAnalysis.id == analysis_id).first()
    if row is None or (scoped is not None and row.organization_id != scoped):
        raise HTTPException(404, "Analysis not found")
    return svc.analysis_view(row)
