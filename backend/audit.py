"""Append-only audit trail. Every mutating action in POCKET is recorded here."""
from __future__ import annotations

from backend.database import SessionLocal
from backend.models import AuditLog


def log_action(
    actor: str | None,
    action: str,
    inspection_id: str | None = None,
    before: str | None = None,
    after: str | None = None,
    reason: str | None = None,
) -> None:
    """Record an audit event in its own short-lived session so it survives caller rollbacks."""
    db = SessionLocal()
    try:
        db.add(
            AuditLog(
                actor=actor,
                action=action,
                inspection_id=inspection_id,
                before=before,
                after=after,
                reason=reason,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
