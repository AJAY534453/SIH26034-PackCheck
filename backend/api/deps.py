"""Shared API dependencies for authentication and authorization.

A protected operation validates, in order:

1. **authenticated user** — a signed access token from the ``Authorization`` header or the
   ``HttpOnly`` session cookie. A token in a URL query string is NOT accepted: query strings are
   recorded by proxies, server logs and ``Referer`` headers, which turns a credential into a leak.
2. **a live server-side session** — the token's ``sid`` must map to a non-revoked, unexpired
   session row, so logout / password change / reuse detection actually take effect;
3. **role** — for the legacy role checks; and
4. **permission** — the centralized RBAC table (`backend.authz`).

Every protected endpoint depends on one of these; none trusts the frontend.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.authz import has_permission
from backend.database import get_db
from backend.models import AuthSession, User
from backend.models.enums import UserStatus
from backend.security import decode_token

ACCESS_COOKIE = "pck_access"
REFRESH_COOKIE = "pck_refresh"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def _extract_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return request.cookies.get(ACCESS_COOKIE, "")


def resolve_session(request: Request, db: Session, *, touch: bool = True) -> tuple[User, AuthSession]:
    """Validate the access token + its server-side session. Raises 401 on any problem."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token, expected_type="access")
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    sid = payload.get("sid") or ""
    if not sid:
        # A token with no session id cannot be revoked, so it is not accepted.
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    session = db.query(AuthSession).filter(AuthSession.session_id == sid).first()
    if session is None or session.revoked:
        raise HTTPException(status_code=401, detail="Session ended. Please log in again.")
    if _as_naive(session.expires_at) is not None and _as_naive(session.expires_at) <= _now():
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    user = db.query(User).filter(User.username == payload.get("sub"), User.is_active).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    if user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status_code=403, detail="This account is not active. Contact an administrator.")
    if touch:
        last = _as_naive(session.last_used_at)
        now = _now()
        if last is None or (now - last).total_seconds() > 300:
            session.last_used_at = now
            db.commit()
    return user, session


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user, _ = resolve_session(request, db)
    return user


def get_current_session(request: Request, db: Session = Depends(get_db)) -> tuple[User, AuthSession]:
    return resolve_session(request, db)


def require_role(*roles: str):
    """Dependency factory: allow only the listed roles (legacy role checks)."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail=f"Requires role in {sorted(roles)}")
        return user

    return checker


def require_permission(*permissions: str):
    """Dependency factory: allow only users whose role grants EVERY listed permission."""

    def checker(user: User = Depends(get_current_user)) -> User:
        missing = [p for p in permissions if not has_permission(user, p)]
        if missing:
            raise HTTPException(
                status_code=403,
                detail="Your role is not authorized for this operation.",
            )
        return user

    return checker


def require_any_permission(*permissions: str):
    """Dependency factory: allow users whose role grants AT LEAST ONE listed permission.

    Used where two different capabilities can legitimately reach the same operation (e.g. recording
    a measurement calibration, which is both a laboratory action and an official-reviewer action).
    The grant is still decided by the central permission table, never by a role literal.
    """

    def checker(user: User = Depends(get_current_user)) -> User:
        if not any(has_permission(user, p) for p in permissions):
            raise HTTPException(
                status_code=403,
                detail="Your role is not authorized for this operation.",
            )
        return user

    return checker
