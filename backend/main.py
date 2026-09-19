"""POCKET FastAPI application — modular monolith.

Legacy foundation preserved: GET /, GET /status, POST /upload (back-compat upload that
creates an inspection with one image). All new functionality is modular under /auth,
/inspections, /dashboard, /products, /violations, /reports, /rules, /files.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.config import settings
from backend.database import Base, SessionLocal, engine
from backend.models import Inspection, InspectionImage, User  # ensure metadata is complete
from backend.migrations import ensure_schema
from backend.ocr import ocr_available
from backend.services.inspection_service import add_image, create_inspection, process_inspection
from backend.api import (
    analysis,
    assistant,
    auth,
    inspections as inspections_api,
    listings,
    meta,
    reprocess,
    repository,
    roles,
    tools,
)
from backend.ai import ai_status
from backend.api.deps import get_current_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)
    # create_all creates missing tables but never alters an existing one; apply additive
    # column migrations so a database created before the account-security columns keeps working.
    ensure_schema(engine)
    _seed_baseline()
    yield


def _seed_baseline() -> None:
    """Create default users + organizations + seed rules on first run (idempotent)."""
    from backend import audit
    from backend.models import Organization, User
    from backend.models.enums import OrganizationKind, UserRole
    from backend.rules.registry import seed_rules
    from backend.security import hash_password

    db = SessionLocal()
    try:
        # Organizations are the isolation boundary. They are created before users so every
        # role that is scoped to an organization has one to be scoped to.
        def org(name: str, kind: OrganizationKind) -> Organization:
            row = db.query(Organization).filter(Organization.name == name).first()
            if row is None:
                row = Organization(name=name, kind=kind.value)
                db.add(row)
                db.commit()
                db.refresh(row)
            return row

        # Demo accounts are seeded PER ACCOUNT so an existing database also gains the new role
        # demos — without ever touching an account that already exists (no password resets).
        if settings.DEMO_MODE:
            regulator = org("Legal Metrology Department", OrganizationKind.REGULATOR)
            business = org("Demo Foods & Beverages Pvt Ltd", OrganizationKind.BUSINESS)
            corporate = org("Demo Corporate Compliance Office", OrganizationKind.CORPORATE)

            accounts = [
                # username, password, full name, role, organization, verified
                ("admin", "admin123", "System Administrator", UserRole.ADMIN, regulator, True),
                ("inspector", "inspector123", "Inspector", UserRole.INSPECTOR, None, True),
                ("viewer", "viewer123", "Viewer", UserRole.VIEWER, None, True),
                ("officer", "officer123", "Enforcement Officer",
                 UserRole.ENFORCEMENT_OFFICER, regulator, True),
                ("entity", "entity123", "Regulated Entity User",
                 UserRole.REGULATED_ENTITY, business, True),
                ("compliance", "compliance123", "Internal Compliance User",
                 UserRole.INTERNAL_COMPLIANCE, corporate, True),
            ]
            created = []
            for username, password, full_name, role, organization, verified in accounts:
                if db.query(User).filter(User.username == username).first() is not None:
                    continue
                db.add(User(
                    username=username,
                    full_name=full_name,
                    role=role.value,
                    email=f"{username}@packcheck.local",
                    organization_id=organization.id if organization is not None else None,
                    email_verified=verified,
                    password_hash=hash_password(password),
                ))
                created.append(username)
            if created:
                db.commit()
                audit.log_action("system", "seed_users", after="created " + ", ".join(created))
        stats = seed_rules(db)
        if stats["created"]:
            audit.log_action("system", "rules_seeded", after=str(stats))
    finally:
        db.close()


app = FastAPI(
    title="PACKCHECK AI — AI-Assisted Legal Metrology Inspection System",
    description=(
        "AI-assisted inspection platform for packaged commodities under the Legal Metrology "
        "(Packaged Commodities) Rules, 2011. SIH26034. "
        "AI perceives → Python validates → Rules decide → Evidence explains. "
        "Extraction ≠ Validation ≠ Legal decision. Decision support — not a legal authority."
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN, "http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(analysis.router)
app.include_router(inspections_api.router)
app.include_router(meta.router)
app.include_router(repository.router)
app.include_router(roles.router)
app.include_router(tools.router)
app.include_router(assistant.router)
app.include_router(listings.router)
app.include_router(reprocess.router)


# ---------- legacy foundation (preserved) ----------

@app.get("/")
def root():
    return {
        "name": settings.APP_NAME,
        "full_name": f"{settings.APP_NAME} — {settings.APP_SUBTITLE}",
        "tagline": settings.APP_TAGLINE,
        "team": settings.APP_TEAM,
        "problem": "SIH26034 — Legal Metrology (Packaged Commodities) Rules, 2011 compliance inspection",
        "version": settings.APP_VERSION,
        "ocr_available": ocr_available(),
        "ai": ai_status(),
        "docs": "/docs",
    }


@app.get("/status")
def status():
    """Honest health check: only reports ok when every required subsystem is available."""
    db_ok = False
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        finally:
            db.close()
    except Exception:
        db_ok = False
    ocr = ocr_available()
    storage_ok = settings.ensure_dirs()
    ai = ai_status()
    return {
        "status": "ok" if (db_ok and storage_ok) else "degraded",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "components": {
            # A vision provider is OPTIONAL: it is reported truthfully but never gates health.
            "api": True,
            "database": db_ok,
            "ocr": ocr,
            "storage": storage_ok,
            "rule_engine": True,  # deterministic, in-process — unavailable only if the app is
            "vision_provider": ai["enabled"],
        },
        "ocr_available": ocr,
        "ai": ai,
        "demo_mode": settings.DEMO_MODE,
    }


@app.get("/health")
def health():
    """Lightweight liveness probe for launchers and operators (no auth, no heavy checks)."""
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION, "probe": "/health"}


@app.get("/health/vision")
def health_vision():
    """Vision perception health.

    Reports the ALWAYS-AVAILABLE on-device engine (checked live, no network) and the OPTIONAL
    provider's configuration together with what the last real run actually recorded. Provider
    reachability is deliberately NOT guessed here — `POST /vision/test` makes the live call and
    reports its latency and error verbatim.
    """
    from backend.services.vision_service import health as vision_health

    db = None
    try:
        db = SessionLocal()
        return vision_health(db)
    except Exception as exc:  # a health probe must never raise
        return {
            "status": "UNKNOWN",
            "error": f"{type(exc).__name__}: {exc}",
            "engine": "on-device-vision",
        }
    finally:
        if db is not None:
            db.close()


@app.post("/upload")
async def legacy_upload(file: UploadFile = File(...), role: str = Form("ADDITIONAL_EVIDENCE")):
    """Legacy endpoint: creates a new inspection with one image and processes it immediately."""
    db = SessionLocal()
    try:
        inspection = create_inspection(db, inspector="legacy-upload")
        data = await file.read()
        try:
            add_image(db, inspection, data, file.filename or "upload", role.upper())
        except Exception as e:
            raise HTTPException(422, str(e))
        process_inspection(db, inspection.id)
        return {
            "message": "Image accepted; inspection processed",
            "inspection_id": inspection.id,
            "inspection_number": inspection.inspection_number,
            "decision": inspection.final_decision,
        }
    finally:
        db.close()


# ---------- global error hygiene ----------

from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):  # pragma: no cover
    # Never leak stack traces to clients.
    return JSONResponse(status_code=500, content={"detail": "Internal server error. Check server logs."})
