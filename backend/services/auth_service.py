"""Centralized authentication service.

Every authentication decision lives here so no endpoint re-implements it:

* credential verification against a scrypt hash (never a plaintext comparison),
* progressive brute-force protection (per-IP sliding window + per-account lockout),
* MFA (TOTP) with one-time recovery codes,
* server-side SESSIONS that can be revoked (logout, logout-everywhere, password change),
* refresh-token ROTATION with reuse detection,
* password change / reset with a strength policy,
* an append-only audit trail for every security event (never a password or token).

Nothing here is frontend-only: the API and the query layer both depend on it.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import audit
from backend.authz import permissions_for, role_home
from backend.config import settings
from backend.models import AuthSession, AuthToken, User
from backend.models.enums import UserStatus
from backend.security import (
    create_access_token,
    create_mfa_challenge_token,
    create_refresh_token,
    decode_token,
    generate_recovery_codes,
    generate_totp_secret,
    hash_password,
    hash_recovery_code,
    new_session_id,
    token_fingerprint,
    totp_provisioning_uri,
    validate_password_strength,
    verify_password,
    verify_recovery_code,
    verify_totp,
)


class AuthError(Exception):
    """An authentication failure with a security-conscious, user-safe message."""

    def __init__(self, status_code: int, message: str, code: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


def _now() -> datetime:
    """Naive UTC — one comparison convention for every stored timestamp here."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


# ---------------------------------------------------------------- rate limiting

class SlidingWindowLimiter:
    """In-process sliding-window limiter (per key).

    Deliberately simple and dependency-free: it stops repeated failures from one IP/account
    without needing Redis. Multi-worker deployments would back it with shared storage — the
    interface would not change.
    """

    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        self._hits[key] = hits
        return hits

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.time())) >= self.max_attempts

    def hit(self, key: str) -> None:
        with self._lock:
            hits = self._prune(key, time.time())
            hits.append(time.time())
            self._hits[key] = hits

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


LOGIN_IP_LIMITER = SlidingWindowLimiter(settings.LOGIN_IP_MAX_ATTEMPTS, settings.LOGIN_WINDOW_SECONDS)
RESET_IP_LIMITER = SlidingWindowLimiter(settings.LOGIN_IP_MAX_ATTEMPTS, settings.LOGIN_WINDOW_SECONDS)


# ---------------------------------------------------------------- lookups

def find_user(db: Session, identifier: str) -> User | None:
    ident = (identifier or "").strip()
    if not ident:
        return None
    user = db.query(User).filter(func.lower(User.username) == ident.lower()).first()
    if user is not None:
        return user
    return db.query(User).filter(func.lower(User.email) == ident.lower()).first()


# ---------------------------------------------------------------- login

def authenticate(db: Session, identifier: str, password: str, *, ip: str = "", user_agent: str = "") -> dict:
    """Verify credentials. Returns either ``{"user": user}`` or an MFA challenge."""
    ip_key = ip or "unknown"
    if LOGIN_IP_LIMITER.blocked(ip_key):
        audit.log_action(identifier, "login_rate_limited", reason=f"ip={ip_key}")
        raise AuthError(429, "Too many sign-in attempts from this network. Please wait and try again.")

    user = find_user(db, identifier)
    generic = "Invalid credentials. Check your username/email and password."
    if user is None:
        LOGIN_IP_LIMITER.hit(ip_key)
        audit.log_action((identifier or "")[:64], "login_failed", reason="unknown account")
        raise AuthError(401, generic)

    now = _now()
    locked_until = _as_naive(user.locked_until)
    if locked_until and locked_until > now:
        audit.log_action(user.username, "login_blocked_locked", reason=str(locked_until))
        raise AuthError(
            423,
            "This account is temporarily locked after repeated failed sign-ins. "
            f"Try again after {locked_until:%Y-%m-%d %H:%M} UTC.",
            "ACCOUNT_LOCKED",
        )
    if locked_until and locked_until <= now:
        # the lockout window has elapsed — clear it so the user gets a clean try
        user.locked_until = None
        user.failed_login_count = 0
        if user.status == UserStatus.LOCKED.value:
            user.status = UserStatus.ACTIVE.value
        db.commit()

    if not user.is_active or user.status == UserStatus.DISABLED.value:
        audit.log_action(user.username, "login_denied_disabled")
        raise AuthError(403, "This account is disabled. Contact an administrator.", "ACCOUNT_DISABLED")
    if user.status == UserStatus.PENDING_VERIFICATION.value:
        audit.log_action(user.username, "login_denied_unverified")
        raise AuthError(403, "This account has not been verified yet.", "ACCOUNT_UNVERIFIED")

    if not verify_password(password, user.password_hash):
        LOGIN_IP_LIMITER.hit(ip_key)
        user.failed_login_count = (user.failed_login_count or 0) + 1
        remaining = settings.LOGIN_MAX_FAILED - user.failed_login_count
        if remaining <= 0:
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            user.status = UserStatus.LOCKED.value
            db.commit()
            audit.log_action(
                user.username, "account_locked",
                reason=f"{user.failed_login_count} consecutive failed attempts",
            )
            raise AuthError(
                423,
                "Too many failed attempts. This account is locked temporarily; "
                "use “Forgot password” or contact an administrator.",
                "ACCOUNT_LOCKED",
            )
        db.commit()
        audit.log_action(user.username, "login_failed", reason=f"bad password; {remaining} attempt(s) left")
        raise AuthError(401, generic)

    # credentials are correct
    user.failed_login_count = 0
    user.locked_until = None
    if user.status == UserStatus.LOCKED.value:
        user.status = UserStatus.ACTIVE.value
    db.commit()
    LOGIN_IP_LIMITER.reset(ip_key)
    audit.log_action(user.username, "login_credentials_ok", reason="awaiting MFA" if user.mfa_enabled else "session issued")

    if user.mfa_enabled:
        return {"mfa_required": True, "mfa_token": create_mfa_challenge_token(user.username)}

    return {"user": user}


def start_session(db: Session, user: User, *, ip: str = "", user_agent: str = "", mfa_verified: bool = False) -> dict:
    """Create a revocable session and issue an access + refresh token pair."""
    sid = new_session_id()
    session = AuthSession(
        session_id=sid,
        username=user.username,
        expires_at=_now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        mfa_verified=mfa_verified,
        ip_address=(ip or "")[:64],
        user_agent=(user_agent or "")[:300],
    )
    db.add(session)
    user.last_login_at = _now()
    user.last_login_ip = (ip or "")[:64]
    db.commit()
    db.refresh(session)

    access = create_access_token(
        user.username, user.role,
        {"name": user.full_name, "org": user.organization_id},
        sid=sid,
    )
    refresh = create_refresh_token(user.username, sid)
    audit.log_action(
        user.username, "login",
        after=f"role={user.role} mfa={'verified' if mfa_verified else 'not-enabled'}",
    )
    return {"user": user, "session": session, "access_token": access, "refresh_token": refresh}


# ---------------------------------------------------------------- MFA

def _consume_recovery_code(db: Session, user: User, code: str) -> bool:
    try:
        hashes = json.loads(user.mfa_recovery_codes or "[]")
    except json.JSONDecodeError:
        hashes = []
    if not isinstance(hashes, list):
        return False
    for i, stored in enumerate(hashes):
        if verify_recovery_code(code, stored):
            hashes.pop(i)
            user.mfa_recovery_codes = json.dumps(hashes)
            db.commit()
            audit.log_action(user.username, "mfa_recovery_code_used", reason=f"{len(hashes)} code(s) remaining")
            return True
    return False


def verify_mfa(db: Session, mfa_token: str, *, code: str = "", recovery_code: str = "", ip: str = "", user_agent: str = "") -> dict:
    payload = decode_token(mfa_token, expected_type="mfa")
    if payload is None:
        raise AuthError(401, "This sign-in challenge has expired. Please log in again.", "MFA_EXPIRED")
    user = db.query(User).filter(User.username == payload.get("sub"), User.is_active).first()
    if user is None or not user.mfa_enabled:
        raise AuthError(401, "Sign-in could not be completed. Please log in again.", "MFA_INVALID")

    ok = verify_totp(user.mfa_secret, code) if code else False
    if not ok and recovery_code:
        ok = _consume_recovery_code(db, user, recovery_code)
    if not ok:
        audit.log_action(user.username, "mfa_failed")
        raise AuthError(401, "That code is not valid. Use the current code from your authenticator app.", "MFA_FAILED")
    return start_session(db, user, ip=ip, user_agent=user_agent, mfa_verified=True)


def begin_mfa_setup(db: Session, user: User) -> dict:
    """Generate a fresh secret. MFA stays OFF until a code proves the device works."""
    secret = generate_totp_secret()
    user.mfa_secret = secret
    db.commit()
    audit.log_action(user.username, "mfa_setup_started")
    return {"secret": secret, "otpauth_uri": totp_provisioning_uri(secret, user.username)}


def enable_mfa(db: Session, user: User, code: str) -> dict:
    if not user.mfa_secret:
        raise AuthError(422, "Start MFA setup before confirming it.", "MFA_NOT_STARTED")
    if not verify_totp(user.mfa_secret, code):
        raise AuthError(422, "That code did not match. Check your device clock and try again.", "MFA_CODE_INVALID")
    codes = generate_recovery_codes()
    user.mfa_enabled = True
    user.mfa_recovery_codes = json.dumps([hash_recovery_code(c) for c in codes])
    db.commit()
    audit.log_action(user.username, "mfa_enabled", reason=f"{len(codes)} recovery codes issued")
    return {"recovery_codes": codes}


def disable_mfa(db: Session, user: User, code: str) -> None:
    if not user.mfa_enabled:
        raise AuthError(422, "MFA is not enabled on this account.", "MFA_NOT_ENABLED")
    if not verify_totp(user.mfa_secret, code):
        raise AuthError(422, "That code did not match.", "MFA_CODE_INVALID")
    user.mfa_enabled = False
    user.mfa_secret = ""
    user.mfa_recovery_codes = "[]"
    db.commit()
    audit.log_action(user.username, "mfa_disabled")


def regenerate_recovery_codes(db: Session, user: User, code: str) -> dict:
    if not user.mfa_enabled or not verify_totp(user.mfa_secret, code):
        raise AuthError(422, "Enter a valid authenticator code to reissue recovery codes.", "MFA_CODE_INVALID")
    codes = generate_recovery_codes()
    user.mfa_recovery_codes = json.dumps([hash_recovery_code(c) for c in codes])
    db.commit()
    audit.log_action(user.username, "mfa_recovery_codes_regenerated", reason=f"{len(codes)} issued")
    return {"recovery_codes": codes}


# ---------------------------------------------------------------- sessions

def _revoke_all_sessions(db: Session, username: str, reason: str) -> int:
    rows = (
        db.query(AuthSession)
        .filter(AuthSession.username == username, AuthSession.revoked.is_(False))
        .all()
    )
    for row in rows:
        row.revoked = True
        row.revoked_at = _now()
        row.revoke_reason = reason[:120]
    db.commit()
    return len(rows)


def revoke_session(db: Session, sid: str, reason: str = "logout") -> None:
    row = db.query(AuthSession).filter(AuthSession.session_id == sid).first()
    if row is not None and not row.revoked:
        row.revoked = True
        row.revoked_at = _now()
        row.revoke_reason = reason[:120]
        db.commit()


def logout_everywhere(db: Session, user: User) -> int:
    count = _revoke_all_sessions(db, user.username, "logout everywhere")
    audit.log_action(user.username, "logout_all", reason=f"{count} session(s) revoked")
    return count


def refresh_session(db: Session, refresh_token: str, *, ip: str = "", user_agent: str = "") -> dict:
    """Rotate a refresh token. Reuse of an already-rotated token revokes every session."""
    payload = decode_token(refresh_token, expected_type="refresh")
    if payload is None:
        raise AuthError(401, "Session expired. Please log in again.", "SESSION_EXPIRED")
    sid = payload.get("sid") or ""
    row = db.query(AuthSession).filter(AuthSession.session_id == sid).first()
    if row is None:
        raise AuthError(401, "Session expired. Please log in again.", "SESSION_EXPIRED")
    if row.revoked:
        # A revoked refresh token being presented is a strong theft signal: kill everything.
        _revoke_all_sessions(db, row.username, "refresh token reuse detected")
        audit.log_action(row.username, "session_reuse_detected", reason=f"sid={sid}")
        raise AuthError(401, "Session invalidated for security. Please log in again.", "SESSION_REUSE")
    if _as_naive(row.expires_at) <= _now():
        raise AuthError(401, "Session expired. Please log in again.", "SESSION_EXPIRED")

    user = db.query(User).filter(User.username == row.username).first()
    if user is None or not user.is_active or user.status != UserStatus.ACTIVE.value:
        revoke_session(db, sid, "account not active")
        raise AuthError(401, "Session ended. Please log in again.", "SESSION_INVALID")

    # rotate: revoke the presented session, issue a fresh one
    row.revoked = True
    row.revoked_at = _now()
    row.revoke_reason = "rotated"
    db.commit()
    return start_session(db, user, ip=ip, user_agent=user_agent, mfa_verified=row.mfa_verified)


def list_sessions(db: Session, user: User, current_sid: str = "") -> list[dict]:
    rows = (
        db.query(AuthSession)
        .filter(AuthSession.username == user.username)
        .order_by(AuthSession.id.desc())
        .all()
    )
    return [
        {
            "session_id": r.session_id,
            "current": r.session_id == current_sid,
            "created_at": str(r.created_at),
            "last_used_at": str(r.last_used_at) if r.last_used_at else "",
            "expires_at": str(r.expires_at),
            "revoked": r.revoked,
            "revoked_at": str(r.revoked_at) if r.revoked_at else "",
            "revoke_reason": r.revoke_reason,
            "ip_address": r.ip_address,
            "user_agent": r.user_agent,
            "mfa_verified": r.mfa_verified,
        }
        for r in rows
    ]


# ---------------------------------------------------------------- password

def _check_new_password(new_password: str) -> None:
    problems = validate_password_strength(new_password)
    if problems:
        raise AuthError(422, "Password " + "; ".join(problems) + ".", "WEAK_PASSWORD")


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        audit.log_action(user.username, "password_change_failed", reason="current password incorrect")
        raise AuthError(400, "The current password is incorrect.", "BAD_CURRENT_PASSWORD")
    _check_new_password(new_password)
    if verify_password(new_password, user.password_hash):
        raise AuthError(422, "The new password must differ from the current one.", "SAME_PASSWORD")
    user.password_hash = hash_password(new_password)
    user.password_changed_at = _now()
    user.must_change_password = False
    db.commit()
    # A password change invalidates every existing session, including this one.
    _revoke_all_sessions(db, user.username, "password changed")
    audit.log_action(user.username, "password_changed")


def request_password_reset(db: Session, identifier: str, *, ip: str = "") -> dict:
    """Begin a reset. The response NEVER reveals whether the account exists."""
    ip_key = ip or "unknown"
    if RESET_IP_LIMITER.blocked(ip_key):
        audit.log_action(identifier, "password_reset_rate_limited", reason=f"ip={ip_key}")
        raise AuthError(429, "Too many reset requests. Please wait before trying again.")
    RESET_IP_LIMITER.hit(ip_key)

    user = find_user(db, identifier)
    # Bound per-account reset abuse independently of the IP window.
    recent = 0
    token: str | None = None
    if user is not None and user.is_active:
        recent = (
            db.query(AuthToken)
            .filter(
                AuthToken.username == user.username,
                AuthToken.kind == "PASSWORD_RESET",
                AuthToken.created_at >= _now() - timedelta(minutes=15),
            )
            .count()
        )
    if user is not None and user.is_active and recent < 5:
        token = secrets.token_urlsafe(32)
        db.add(
            AuthToken(
                token_hash=token_fingerprint(token),
                username=user.username,
                kind="PASSWORD_RESET",
                expires_at=_now() + timedelta(minutes=settings.RESET_TOKEN_EXPIRE_MINUTES),
                requested_ip=(ip or "")[:64],
            )
        )
        db.commit()
        audit.log_action(user.username, "password_reset_requested", reason=f"ip={ip_key}")
    else:
        audit.log_action((identifier or "")[:64], "password_reset_requested", reason="no eligible account")

    response = {"message": "If an account matches, a reset has been generated."}
    # No email infra exists in this deployment. In demo mode the caller is handed the token so the
    # reset flow is usable end-to-end; with POCKET_DEMO_MODE=0 the token is NOT returned and would
    # be delivered out-of-band (email/SMS).
    if token and settings.DEMO_MODE:
        response["reset_token"] = token
        response["delivery"] = "demo"  # labelled so nobody mistakes it for a real email
    return response


def confirm_password_reset(db: Session, token: str, new_password: str) -> None:
    if not token:
        raise AuthError(422, "A reset token is required.", "BAD_RESET_TOKEN")
    row = (
        db.query(AuthToken)
        .filter(AuthToken.token_hash == token_fingerprint(token), AuthToken.kind == "PASSWORD_RESET")
        .first()
    )
    if row is None or row.used_at is not None or _as_naive(row.expires_at) <= _now():
        raise AuthError(400, "This reset link is invalid or has expired.", "BAD_RESET_TOKEN")
    user = db.query(User).filter(User.username == row.username).first()
    if user is None:
        raise AuthError(400, "This reset link is invalid or has expired.", "BAD_RESET_TOKEN")
    _check_new_password(new_password)
    user.password_hash = hash_password(new_password)
    user.password_changed_at = _now()
    user.failed_login_count = 0
    user.locked_until = None
    if user.status == UserStatus.LOCKED.value:
        user.status = UserStatus.ACTIVE.value
    row.used_at = _now()
    db.commit()
    _revoke_all_sessions(db, user.username, "password reset")
    audit.log_action(user.username, "password_reset_completed")


# ---------------------------------------------------------------- serialization

def user_public(db: Session, user: User) -> dict:
    """The ONLY user shape returned to a client. Never includes hashes, secrets or tokens."""
    org = None
    if user.organization_id:
        from backend.models import Organization

        o = db.query(Organization).filter(Organization.id == user.organization_id).first()
        if o is not None:
            org = {"id": o.id, "name": o.name, "kind": o.kind}
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email or "",
        "role": user.role,
        "status": user.status,
        "is_active": user.is_active,
        "organization_id": user.organization_id,
        "organization": org,
        "mfa_enabled": user.mfa_enabled,
        "must_change_password": user.must_change_password,
        "permissions": sorted(permissions_for(user.role)),
        "home": role_home(user.role),
    }


def token_response(user: User) -> dict:
    """Body returned by login/MFA/refresh (token also travels as an HttpOnly cookie)."""
    return {
        "username": user.username,
        "role": user.role,
        "full_name": user.full_name,
        "home": role_home(user.role),
        "permissions": sorted(permissions_for(user.role)),
    }
