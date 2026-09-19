"""Bill Scanner, Grocery tracker and Complaint Center APIs (+ vision provider status).

Design rules carried over from the inspection side of the product:

- READING ≠ DECIDING. A vision reading is stored with its provenance and confidence; the
  price comparison, the expiry countdown and the complaint status are computed in Python.
- Nothing enters the grocery tracker implicitly. Adding an item is an explicit user action and
  only grocery items raise expiry alerts.
- Sample/demo values are labelled as such wherever they appear — never blended with real ones.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend import audit
from backend.ai import ai_status, extract_bill_fields
from backend.api.deps import require_permission
from backend.authz import Permission, has_permission, org_scope_filter, scope_org_id
from backend.config import settings
from backend.database import get_db
from backend.models import Bill, Complaint, ExtractedField, GroceryItem, Inspection, InspectionImage, User
from backend.models.user import utcnow
from backend.services import tools_service as svc
from backend.services.image_service import UploadValidationError, validate_and_store

router = APIRouter(tags=["tools"])

ALLOWED_SOURCES = {"vision", "manual", "demo"}


@router.get("/ai/status")
def vision_status(user: User = Depends(require_permission(Permission.DASHBOARD_VIEW))):
    """Honest provider status. The UI uses this to label what produced a reading."""
    return ai_status()


@router.post("/vision/test")
async def vision_test(
    file: UploadFile = File(...),
    user: User = Depends(require_permission(Permission.TOOLS_USE)),
):
    """Controlled self-test of BOTH perception sources over one image.

    Runs the on-device engine always (no key, no network) and the configured provider when there
    is one, reporting latency, regions and any error exactly as they occurred. This is how a
    deployment is verified independently of a full inspection — and the response never claims a
    source ran when it did not.
    """
    from backend.services.vision_service import self_test

    data = await file.read()
    if not data:
        raise HTTPException(422, "No image data was received.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"Image exceeds the {settings.MAX_UPLOAD_MB} MB upload limit.")
    result = self_test(data, file.filename or "upload")
    audit.log_action(user.username, "vision_self_test", file.filename or "upload",
                     after=f"on-device={result.get('on_device', {}).get('status')} provider={result.get('provider_call', {}).get('status')}")
    return result


# ---------------------------------------------------------------- bills


def _apply_bill_values(bill: Bill, values: dict) -> None:
    """Assign only the keys actually supplied, then recompute the comparison."""
    for key in ("store_name", "bill_number", "bill_date", "product_name", "brand", "quantity", "line_items"):
        if key in values and values[key] is not None:
            setattr(bill, key, str(values[key]).strip()[: 2000 if key == "line_items" else 200])
    if "billed_price" in values:
        bill.billed_price = svc.parse_money(values["billed_price"])
    if "mrp" in values:
        bill.mrp = svc.parse_money(values["mrp"])
    if "inspection_id" in values and values["inspection_id"] is not None:
        bill.inspection_id = int(values["inspection_id"])
    if "product_id" in values and values["product_id"] is not None:
        bill.product_id = int(values["product_id"])
    if "notes" in values and values["notes"] is not None:
        bill.notes = str(values["notes"])[:2000]

    comparison = svc.compare_bill_to_mrp(bill.billed_price, bill.mrp)
    bill.price_difference = comparison["difference"]
    bill.price_difference_pct = comparison["difference_pct"]
    bill.comparison_status = comparison["status"]


@router.post("/bills/scan")
async def scan_bill(
    file: UploadFile = File(...),
    store_name: str = Form(""),
    user: User = Depends(require_permission(Permission.TOOLS_USE)),
    db: Session = Depends(get_db),
):
    """Store a bill image as immutable evidence and read it with the configured provider.

    When no provider is configured the image is still stored and the endpoint says exactly why
    no values were read — it never substitutes invented values.
    """
    data = await file.read()
    if not data:
        raise HTTPException(422, "The uploaded bill file is empty.")
    try:
        info = validate_and_store(data, file.filename or "bill", [], kind="bills")
    except UploadValidationError as e:
        raise HTTPException(422, str(e))

    bill = Bill(
        created_by=user.username,
        organization_id=user.organization_id,
        store_name=store_name.strip()[:200],
        stored_filename=info["stored_filename"],
        extraction_source="manual",
    )
    db.add(bill)
    db.commit()
    db.refresh(bill)

    readings: dict[str, dict] = {}
    status = ai_status()
    if status["enabled"]:
        result = extract_bill_fields([(data, info["mime_type"])])
        if result.used:
            values: dict[str, object] = {}
            for obs in result.observations:
                readings[obs.field] = {
                    "value": obs.value,
                    "confidence": round(obs.confidence, 3),
                    "evidence_text": obs.evidence_text,
                }
                if obs.field in {"store_name", "bill_number", "bill_date", "product_name", "brand", "quantity", "line_items"}:
                    values[obs.field] = obs.value
                elif obs.field == "billed_price":
                    values["billed_price"] = obs.value
                elif obs.field == "mrp_on_bill":
                    values["mrp"] = obs.value
            _apply_bill_values(bill, values)
            bill.extraction_source = "vision"
            bill.extraction_confidence = round(
                sum(r["confidence"] for r in readings.values()) / len(readings), 3
            ) if readings else 0.0
            bill.extraction_detail = f"{result.provider}:{result.model} read {len(readings)} field(s)."
            db.commit()
        else:
            bill.extraction_detail = f"Vision provider not used: {result.error}"
            db.commit()
    else:
        bill.extraction_detail = status["reason"]
        db.commit()

    audit.log_action(user.username, "bill_scanned", reason=bill.extraction_detail or "stored")
    return {
        "bill": svc.bill_view(bill),
        "readings": readings,
        "ai": status,
        "message": (
            "Bill image stored and read by the configured vision provider. Every value below is "
            "labelled with how it was obtained and must be verified."
            if readings
            else "Bill image stored. No automated reading was available — enter the values below "
            "from the bill, or configure a vision provider to enable reading."
        ),
    }


@router.post("/bills")
def create_bill(payload: dict, user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    """Manual entry (or an explicitly requested, clearly labelled sample)."""
    source = str(payload.get("source") or "manual").lower()
    if source not in ALLOWED_SOURCES:
        raise HTTPException(422, f"source must be one of {sorted(ALLOWED_SOURCES)}")
    bill = Bill(
        created_by=user.username,
        organization_id=user.organization_id,
        extraction_source=source,
        extraction_detail=(
            "Manually entered by the user." if source == "manual"
            else "Sample values for demonstration — NOT a reading from the uploaded evidence."
        ),
    )
    _apply_bill_values(bill, payload)
    db.add(bill)
    db.commit()
    db.refresh(bill)
    audit.log_action(user.username, "bill_created", reason=f"source={source}, status={bill.comparison_status}")
    return {"bill": svc.bill_view(bill), "message": "Bill recorded."}


@router.patch("/bills/{bill_id}")
def update_bill(bill_id: int, payload: dict, user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if bill is None:
        raise HTTPException(404, "Bill not found")
    if bill.created_by != user.username and user.role != "ADMIN":
        raise HTTPException(403, "You can only edit bills you created.")
    before = f"billed={bill.billed_price} mrp={bill.mrp}"
    _apply_bill_values(bill, payload)
    # A human correction is what the record now says; the automated reading stays in the audit trail.
    if bill.extraction_source == "vision":
        bill.extraction_source = "vision+manual"
    db.commit()
    audit.log_action(user.username, "bill_updated", reason=f"{before} → billed={bill.billed_price} mrp={bill.mrp}")
    return {"bill": svc.bill_view(bill), "message": "Bill updated and re-compared."}


@router.get("/bills")
def list_bills(user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    rows = org_scope_filter(db.query(Bill), Bill, user).order_by(Bill.id.desc()).all()
    return {"items": [svc.bill_view(b) for b in rows], "total": len(rows)}


@router.get("/bills/{bill_id}")
def get_bill(bill_id: int, user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    q = org_scope_filter(db.query(Bill), Bill, user).filter(Bill.id == bill_id)
    bill = q.first()
    if bill is None:
        raise HTTPException(404, "Bill not found")
    return svc.bill_view(bill)


# ---------------------------------------------------------------- grocery


@router.post("/grocery")
def add_grocery(payload: dict, user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    """EXPLICIT add to the grocery tracker — the only way an item starts being expiry-tracked."""
    name = str(payload.get("product_name") or "").strip()
    if not name:
        raise HTTPException(422, "product_name is required")

    inspection_id = payload.get("inspection_id")
    # Defaults are read from the inspection's OWN evidence when the user adds a scanned product,
    # so nothing has to be re-typed — but the user still chose to add it.
    mfg_date = str(payload.get("mfg_date") or "").strip()
    best_before = str(payload.get("best_before_text") or "").strip()
    if inspection_id:
        insp = db.query(Inspection).filter(Inspection.id == int(inspection_id)).first()
        if insp is None:
            raise HTTPException(404, "Inspection not found")

    resolved = svc.resolve_expiry(
        expiry_date=str(payload.get("expiry_date") or ""),
        best_before_text=best_before,
        mfg_date=mfg_date,
        purchase_date=str(payload.get("purchase_date") or ""),
    )
    category = str(payload.get("category") or "")[:60]
    if not category and inspection_id:
        # Read the product's own category from its inspection (never invented).
        insp_cat = db.query(Inspection).filter(Inspection.id == int(inspection_id)).first()
        if insp_cat is not None:
            category = (insp_cat.category or "")[:60]

    item = GroceryItem(
        owner=user.username,
        organization_id=user.organization_id,
        product_id=int(payload["product_id"]) if payload.get("product_id") else None,
        inspection_id=int(inspection_id) if inspection_id else None,
        product_name=name[:200],
        brand=str(payload.get("brand") or "")[:120],
        quantity=str(payload.get("quantity") or "")[:80],
        mrp=str(payload.get("mrp") or "")[:40],
        batch_lot=str(payload.get("batch_lot") or "")[:80],
        category=category,
        purchase_date=str(payload.get("purchase_date") or utcnow().date().isoformat())[:30],
        expiry_date=resolved["expiry"],
        best_before_text=best_before[:120],
        expiry_basis=resolved["basis"],
        notes=str(payload.get("notes") or "")[:2000],
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    audit.log_action(user.username, "grocery_item_added", after=f"{item.product_name} (basis={item.expiry_basis})")
    return {
        "item": svc.grocery_view(item),
        "expiry_note": resolved.get("note", ""),
        "message": "Added to your grocery tracker. Expiry alerts apply only to items you add here.",
    }


def _grocery_card(db: Session, item: GroceryItem) -> dict:
    """Compact card data: the tracked item plus its product image and scan reference."""
    view = svc.grocery_view(item)
    view.setdefault("inspection_number", "")
    view.setdefault("image_url", "")
    if item.inspection_id:
        insp = db.query(Inspection).filter(Inspection.id == item.inspection_id).first()
        if insp is not None:
            view["inspection_number"] = insp.inspection_number
            img = (
                db.query(InspectionImage)
                .filter(InspectionImage.inspection_id == insp.id)
                .order_by(InspectionImage.id)
                .first()
            )
            if img is not None and img.stored_filename:
                view["image_url"] = f"/files/originals/{img.stored_filename}"
    return view


@router.get("/grocery")
def list_grocery(user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    rows = org_scope_filter(db.query(GroceryItem), GroceryItem, user).order_by(GroceryItem.id.desc()).all()
    items = [_grocery_card(db, i) for i in rows]
    return {
        "items": items,
        "total": len(items),
        "alerts": [i for i in items if i["alert"] in {"EXPIRED", "EXPIRING_SOON"}],
    }


@router.get("/grocery/{item_id}")
def get_grocery(item_id: int, user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    """Product detail for one tracked item: the item plus its underlying scan's declarations.

    Progressive disclosure lives here — the list shows only a card; this endpoint returns the
    full product information (declarations, detected text, scan provenance) on demand.
    """
    item = (
        org_scope_filter(db.query(GroceryItem), GroceryItem, user)
        .filter(GroceryItem.id == item_id)
        .first()
    )
    if item is None:
        raise HTTPException(404, "Grocery item not found")
    # Cross-owner viewing inside the caller's own scope is itself a PERMISSION, never a role list.
    if item.owner != user.username and not has_permission(user, Permission.TOOLS_ORG_VIEW):
        raise HTTPException(403, "You can only view your own grocery items.")

    card = _grocery_card(db, item)
    product: dict = {"fields": [], "detected_text": "", "scan": None}
    if item.inspection_id:
        insp = db.query(Inspection).filter(Inspection.id == item.inspection_id).first()
        if insp is not None:
            fields = (
                db.query(ExtractedField)
                .filter(ExtractedField.inspection_id == insp.id)
                .order_by(ExtractedField.field_name)
                .all()
            )
            product["fields"] = [
                {
                    "field_name": f.field_name,
                    "display_value": f.display_value,
                    "state": f.state,
                    "confidence": f.confidence,
                    "source": f.source_engine or f.source,
                    "note": f.uncertainty_reason,
                }
                for f in fields
            ]
            images = (
                db.query(InspectionImage)
                .filter(InspectionImage.inspection_id == insp.id)
                .order_by(InspectionImage.id)
                .all()
            )
            product["detected_text"] = "\n".join(im.ocr_text for im in images if im.ocr_text)
            product["scan"] = {
                "inspection_id": insp.id,
                "inspection_number": insp.inspection_number,
                "created_at": str(insp.created_at),
                "inspector": insp.inspector,
                "status": insp.status,
                "decision": insp.final_decision,
                "category": insp.category,
                "category_state": insp.category_state,
                "overall_quality": insp.overall_quality,
                "overall_quality_score": insp.overall_quality_score,
                "image_count": len(images),
            }
    return {"item": card, "product": product}


@router.patch("/grocery/{item_id}")
def update_grocery(item_id: int, payload: dict, user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    # Organization scope is applied in the QUERY: an item belonging to another organization is
    # simply not found, so it can be neither read nor mutated by guessing an id.
    item = (
        org_scope_filter(db.query(GroceryItem), GroceryItem, user)
        .filter(GroceryItem.id == item_id)
        .first()
    )
    if item is None:
        raise HTTPException(404, "Grocery item not found")
    if item.owner != user.username and not has_permission(user, Permission.TOOLS_ORG_VIEW):
        raise HTTPException(403, "You can only update your own grocery items.")
    if "expiry_date" in payload:
        item.expiry_date = str(payload.get("expiry_date") or "")[:30]
        item.expiry_basis = "PRINTED_ON_PACKAGE" if item.expiry_date else "NOT_PROVIDED"
    if "purchase_date" in payload:
        item.purchase_date = str(payload.get("purchase_date") or "")[:30]
    if "notes" in payload:
        item.notes = str(payload.get("notes") or "")[:2000]
    db.commit()
    db.refresh(item)
    audit.log_action(user.username, "grocery_item_updated", after=f"item {item.id}")
    return {"item": svc.grocery_view(item), "message": "Grocery item updated."}


@router.delete("/grocery/{item_id}")
def delete_grocery(item_id: int, user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    item = (
        org_scope_filter(db.query(GroceryItem), GroceryItem, user)
        .filter(GroceryItem.id == item_id)
        .first()
    )
    if item is None:
        raise HTTPException(404, "Grocery item not found")
    if item.owner != user.username and not has_permission(user, Permission.TOOLS_ORG_VIEW):
        raise HTTPException(403, "You can only remove your own grocery items.")
    db.delete(item)
    db.commit()
    audit.log_action(user.username, "grocery_item_removed", after=f"item {item_id}")
    return {"message": "Removed from grocery tracker."}


# ---------------------------------------------------------------- complaints


@router.post("/complaints")
def create_complaint(payload: dict, user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    issue = str(payload.get("issue") or "").strip()
    if len(issue) < 10:
        raise HTTPException(422, "Please describe the issue (at least 10 characters).")
    complaint = Complaint(
        complaint_number=svc.next_complaint_number(db),
        created_by=user.username,
        organization_id=user.organization_id,
        product_id=int(payload["product_id"]) if payload.get("product_id") else None,
        inspection_id=int(payload["inspection_id"]) if payload.get("inspection_id") else None,
        bill_id=int(payload["bill_id"]) if payload.get("bill_id") else None,
        product_name=str(payload.get("product_name") or "")[:200],
        store_name=str(payload.get("store_name") or "")[:200],
        category=str(payload.get("category") or "OTHER")[:60],
        severity=str(payload.get("severity") or "MEDIUM").upper()[:20],
        issue=issue[:4000],
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    svc.append_timeline(db, complaint, "SUBMITTED", user.username, "Complaint filed.")
    audit.log_action(user.username, "complaint_created", after=complaint.complaint_number)
    return {"complaint": svc.complaint_view(complaint), "message": "Complaint submitted."}


@router.get("/complaints")
def list_complaints(user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    rows = org_scope_filter(db.query(Complaint), Complaint, user).order_by(Complaint.id.desc()).all()
    items = [svc.complaint_view(c) for c in rows]
    counts: dict[str, int] = {}
    for c in items:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {"items": items, "total": len(items), "by_status": counts, "stages": svc.COMPLAINT_STAGES}


@router.get("/complaints/{complaint_id}")
def get_complaint(complaint_id: int, user: User = Depends(require_permission(Permission.TOOLS_USE)), db: Session = Depends(get_db)):
    q = org_scope_filter(db.query(Complaint), Complaint, user).filter(Complaint.id == complaint_id)
    complaint = q.first()
    if complaint is None:
        raise HTTPException(404, "Complaint not found")
    return svc.complaint_view(complaint)


@router.post("/complaints/{complaint_id}/advance")
def advance_complaint(
    complaint_id: int,
    payload: dict,
    user: User = Depends(require_permission(Permission.COMPLAINTS_MANAGE)),
    db: Session = Depends(get_db),
):
    """Move a complaint along its lifecycle. Status changes are append-only history."""
    complaint = (
        org_scope_filter(db.query(Complaint), Complaint, user)
        .filter(Complaint.id == complaint_id)
        .first()
    )
    if complaint is None:
        raise HTTPException(404, "Complaint not found")
    target = str(payload.get("status") or "").upper()
    if target not in svc.COMPLAINT_STAGES:
        raise HTTPException(422, f"status must be one of {svc.COMPLAINT_STAGES}")
    note = str(payload.get("note") or "")[:1000]
    if target == "RESOLVED":
        complaint.resolution = note or complaint.resolution
    svc.append_timeline(db, complaint, target, user.username, note)
    audit.log_action(user.username, "complaint_status_changed", after=f"{complaint.complaint_number} → {target}", reason=note)
    return {"complaint": svc.complaint_view(complaint), "message": f"Complaint moved to {target}."}


# ---------------------------------------------------------------- evidence file for bills

@router.get("/bills/{bill_id}/evidence")
def bill_evidence(bill_id: int, db: Session = Depends(get_db), user: User = Depends(require_permission(Permission.TOOLS_USE))):
    """Where the stored bill image lives, for the UI's evidence viewer.

    Organization-scoped: another organization's bill is not revealed, and answering with the
    filename is what lets the caller then fetch the file through the ownership-checked route.
    """
    bill = (
        org_scope_filter(db.query(Bill), Bill, user)
        .filter(Bill.id == bill_id)
        .first()
    )
    if bill is None or not bill.stored_filename:
        raise HTTPException(404, "No bill image stored for this record")
    return {"kind": "bills", "stored_filename": bill.stored_filename}
