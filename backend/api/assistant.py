"""PRO endpoints — concise, grounded, record-aware help inside the application.

The caller may supply the record it currently has open (``context``); PRO resolves it inside the
caller's own scope before answering anything about it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import audit
from backend.api.deps import require_permission
from backend.authz import Permission
from backend.database import get_db
from backend.models import User
from backend.services import assistant_service

router = APIRouter(prefix="/assistant", tags=["assistant"])

MAX_MESSAGE_CHARS = 500


class ContextIn(BaseModel):
    """Which record the caller has open. A hint only — never an authorization."""

    path: str = ""
    inspection_id: int | None = None
    scan_id: int | None = None


class ChatIn(BaseModel):
    message: str
    context: ContextIn | None = None


@router.get("/welcome")
def welcome(
    path: str = "",
    inspection_id: int | None = None,
    scan_id: int | None = None,
    user: User = Depends(require_permission(Permission.ASSISTANT_USE)),
    db: Session = Depends(get_db),
):
    """Opening state: PRO's greeting, record-aware suggestions and what the assistant will not do."""
    context = {"path": path, "inspection_id": inspection_id, "scan_id": scan_id}
    return assistant_service.welcome(db, user, context)


@router.post("/chat")
def chat(
    body: ChatIn,
    user: User = Depends(require_permission(Permission.ASSISTANT_USE)),
    db: Session = Depends(get_db),
):
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(422, "Ask a question.")
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(422, f"Please keep the question under {MAX_MESSAGE_CHARS} characters.")
    context = body.context.model_dump() if body.context else None
    result = assistant_service.respond(db, user, message, context)
    audit.log_action(
        user.username,
        "assistant_query",
        None,
        after=f"{result.get('intent')}: {message[:160]}",
        reason="PRO in-application assistant",
    )
    return result
