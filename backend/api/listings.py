"""Online listing / e-commerce capture, listing declaration check, package-vs-listing consistency.

Requires ``listings.use`` — an inspector, enforcement officer, internal-compliance or regulated-entity
user can capture a listing and run the comparison. Organization scope is applied in the query, so an
entity-side user only ever sees their own organization's listings.

Nothing returned here is a legal determination: every check carries the ``legal_status`` of the
catalog entry that governs it, and a package/listing difference is reported as a potential
inconsistency for an officer to assess.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.deps import require_permission
from backend.authz import Permission, scope_org_id
from backend.database import get_db
from backend.models import ProductListing, User
from backend.services import listing_service as listings

router = APIRouter(prefix="/listings", tags=["listings"])


class ListingIn(BaseModel):
    text: str
    source: str = "MANUAL_TEXT"  # MANUAL_TEXT | SCREENSHOT | LISTING_URL
    source_url: str = ""
    platform: str = ""
    inspection_id: int | None = None


class ListingTextIn(BaseModel):
    text: str


class AttachIn(BaseModel):
    inspection_id: int


def _scoped_listing(db: Session, listing_id: int, user: User) -> ProductListing:
    listing = db.query(ProductListing).filter(ProductListing.id == listing_id).first()
    scoped = scope_org_id(user)
    if listing is None or (scoped is not None and listing.organization_id != scoped):
        raise HTTPException(404, "Listing not found")
    return listing


@router.get("")
def list_listings(
    q: str = "",
    page: int = 1,
    page_size: int = 20,
    user: User = Depends(require_permission(Permission.LISTINGS_USE)),
    db: Session = Depends(get_db),
):
    """Search captured listings."""
    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    return listings.list_listings(db, user=user, query=q, page=page, page_size=max(1, min(page_size, 100)))


@router.post("")
def create_listing(
    payload: ListingIn,
    user: User = Depends(require_permission(Permission.LISTINGS_USE)),
    db: Session = Depends(get_db),
):
    """Capture listing text (pasted, or captured from a page/screenshot) and extract its declarations."""
    try:
        listing = listings.create_listing(
            db,
            actor=user.username,
            organization_id=user.organization_id,
            text=payload.text,
            source=payload.source,
            source_url=payload.source_url,
            platform=payload.platform,
            inspection_id=payload.inspection_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return listings.listing_view(listing)


@router.get("/catalog")
def catalog(user: User = Depends(require_permission(Permission.LISTINGS_USE))):
    """The listing rule catalog with each entry's legal status, for display beside the results."""
    from backend.rules import listing_catalog

    return {
        "checks": [
            {
                **{k: v for k, v in check.items() if k != "watched_fields"},
                "legal_status_label": listing_catalog.status_label(check.get("legal_status", "")),
            }
            for check in listing_catalog.all_checks()
        ]
    }


@router.get("/by-inspection/{inspection_id}")
def by_inspection(
    inspection_id: int,
    user: User = Depends(require_permission(Permission.LISTINGS_USE)),
    db: Session = Depends(get_db),
):
    """Every listing captured against an inspection, each with its last consistency result."""
    rows = listings.listings_for_inspection(db, inspection_id)
    scoped = scope_org_id(user)
    if scoped is not None:
        rows = [r for r in rows if r.organization_id == scoped]
    return {"items": [listings.listing_view(r) for r in rows], "total": len(rows)}


@router.get("/{listing_id}")
def get_listing(
    listing_id: int,
    user: User = Depends(require_permission(Permission.LISTINGS_USE)),
    db: Session = Depends(get_db),
):
    return listings.listing_view(_scoped_listing(db, listing_id, user))


@router.put("/{listing_id}")
def update_listing(
    listing_id: int,
    payload: ListingTextIn,
    user: User = Depends(require_permission(Permission.LISTINGS_USE)),
    db: Session = Depends(get_db),
):
    """Replace the captured text and re-extract (the stored comparison is invalidated)."""
    listing = _scoped_listing(db, listing_id, user)
    try:
        listings.update_listing(db, listing, actor=user.username, text=payload.text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return listings.listing_view(listing)


@router.post("/{listing_id}/attach")
def attach(
    listing_id: int,
    payload: AttachIn,
    user: User = Depends(require_permission(Permission.LISTINGS_USE)),
    db: Session = Depends(get_db),
):
    """Link the listing to the package inspection it should be compared against."""
    listing = _scoped_listing(db, listing_id, user)
    try:
        listings.attach_to_inspection(db, listing, payload.inspection_id, actor=user.username)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return listings.listing_view(listing)


@router.get("/{listing_id}/declarations")
def declarations(
    listing_id: int,
    user: User = Depends(require_permission(Permission.LISTINGS_USE)),
    db: Session = Depends(get_db),
):
    """The Rule 6(10) listing-declaration check over the captured text."""
    listing = _scoped_listing(db, listing_id, user)
    return listings.declaration_check(listing)


@router.post("/{listing_id}/consistency")
def consistency(
    listing_id: int,
    user: User = Depends(require_permission(Permission.LISTINGS_USE)),
    db: Session = Depends(get_db),
):
    """Run (and store) the package-versus-listing consistency comparison."""
    listing = _scoped_listing(db, listing_id, user)
    return listings.consistency(db, listing)


@router.delete("/{listing_id}")
def delete_listing(
    listing_id: int,
    user: User = Depends(require_permission(Permission.LISTINGS_USE)),
    db: Session = Depends(get_db),
):
    listing = _scoped_listing(db, listing_id, user)
    listings.delete_listing(db, listing, actor=user.username)
    return {"deleted": True, "id": listing_id}
