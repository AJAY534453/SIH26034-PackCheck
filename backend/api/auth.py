"""Authentication endpoints — one centralized implementation, used by every role.

The server authenticates with an HttpOnly session cookie: the browser app receives NO credential
in the response body (nothing sensitive is ever exposed to JavaScript or kept in browser storage).
A non-browser API client can still ask for the access token explicitly with ``include_token: true``
and then use it as a bearer header — an opt-in, so tokens are absent from normal responses.

The user's ROLE comes exclusively from the trusted account record; a role selected on the login
screen is only a routing hint and never grants access.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import audit
from backend.api.deps import ACCESS_COOKIE, REFRESH_COOKIE, get_current_session, get_current_user
from backend.config import settings
from backend.database import get_db
from backend.models import User
from backend.schemas.common import Token, UserOut
from backend.services import auth_service as svc
from backend.services.auth_service import AuthError

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str
    # Optional routing hint from the login screen. NEVER used for authorization.
    role_hint: str | None = None
    # API clients (not the browser app) opt in to receiving the bearer token in the body.
    include_token: bool = False


class MfaVerifyIn(BaseModel):
    mfa_token: str
    code: str | None = None
    recovery_code: str | None = None
    include_token: bool = False


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


class PasswordResetRequestIn(BaseModel):
    identifier: str


class PasswordResetConfirmIn(BaseModel):
    token: str
    new_password: str


class MfaCodeIn(BaseModel):
    code: str


# ---------------------------------------------------------------- cookies

def _cookie_kwargs() -> dict:
    kwargs: dict = {
        "httponly": True,
        "secure": settings.SECURE_COOKIES,
        "samesite": settings.COOKIE_SAMESITE,
        "path": "/",
    }
    if settings.COOKIE_DOMAIN:
        kwargs["domain"] = settings.COOKIE_DOMAIN
    return kwargs


def _set_session_cookies(response: Response, access: str, refresh: str) -> None:
    kwargs = _cookie_kwargs()
    response.set_cookie(ACCESS_COOKIE, access, max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, **kwargs)
    response.set_cookie(REFRESH_COOKIE, refresh, max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400, **kwargs)


def _clear_session_cookies(response: Response) -> None:
    kwargs = {k: v for k, v in _cookie_kwargs().items() if k != "max_age"}
    response.delete_cookie(ACCESS_COOKIE, **kwargs)
    response.delete_cookie(REFRESH_COOKIE, **kwargs)


def _client_ip(request: Request) -> str:
    # Behind the dev proxy the forwarded header is the real client; fall back to the socket peer.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def _raise(err: AuthError) -> None:
    raise HTTPException(status_code=err.status_code, detail=err.message)


# ---------------------------------------------------------------- login / MFA

@router.post("/login", response_model=Token)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip, ua = _client_ip(request), request.headers.get("user-agent", "")
    try:
        result = svc.authenticate(db, body.username, body.password, ip=ip, user_agent=ua)
    except AuthError as err:
        _raise(err)

    if result.get("mfa_required"):
        return Token(
            username=body.username.strip(),
            mfa_required=True,
            mfa_token=result["mfa_token"],
            message="Enter the code from your authenticator app to finish signing in.",
        )

    user: User = result["user"]
    # A role hint that disagrees with the trusted account role is recorded, never honoured.
    if body.role_hint and body.role_hint != user.role:
        audit.log_action(
            user.username, "login_role_hint_mismatch",
            reason=f"hint={body.role_hint} actual={user.role}",
        )
    session = svc.start_session(db, user, ip=ip, user_agent=ua, mfa_verified=False)
    _set_session_cookies(response, session["access_token"], session["refresh_token"])
    payload = svc.token_response(user)
    # The session is in the HttpOnly cookie; the body carries the token only on explicit request.
    return Token(access_token=session["access_token"] if body.include_token else "", **payload)


@router.post("/mfa/verify", response_model=Token)
def mfa_verify(body: MfaVerifyIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip, ua = _client_ip(request), request.headers.get("user-agent", "")
    try:
        session = svc.verify_mfa(
            db, body.mfa_token, code=body.code or "", recovery_code=body.recovery_code or "",
            ip=ip, user_agent=ua,
        )
    except AuthError as err:
        _raise(err)
    user: User = session["user"]
    _set_session_cookies(response, session["access_token"], session["refresh_token"])
    return Token(
        access_token=session["access_token"] if body.include_token else "",
        **svc.token_response(user),
    )


@router.post("/refresh", response_model=Token)
def refresh(
    request: Request,
    response: Response,
    include_token: bool = False,
    db: Session = Depends(get_db),
):
    """Rotate the session from the refresh cookie. ``include_token`` mirrors the login opt-in."""
    token = request.cookies.get(REFRESH_COOKIE, "")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token present.")
    try:
        session = svc.refresh_session(db, token, ip=_client_ip(request), user_agent=request.headers.get("user-agent", ""))
    except AuthError as err:
        _clear_session_cookies(response)
        _raise(err)
    user: User = session["user"]
    _set_session_cookies(response, session["access_token"], session["refresh_token"])
    return Token(
        access_token=session["access_token"] if include_token else "",
        **svc.token_response(user),
    )


@router.post("/logout")
def logout(response: Response, db: Session = Depends(get_db), principal=Depends(get_current_session)):
    """Revoke THIS session and clear the cookies. Logging out is a real server-side action."""
    user, session = principal
    svc.revoke_session(db, session.session_id, reason="logout")
    audit.log_action(user.username, "logout")
    _clear_session_cookies(response)
    return {"message": "Logged out"}


@router.post("/logout-all")
def logout_all(request: Request, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    count = svc.logout_everywhere(db, user)
    _clear_session_cookies(response)
    return {"message": f"Signed out of {count} session(s)."}


@router.get("/me", response_model=UserOut)
def me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return svc.user_public(db, user)


# ---------------------------------------------------------------- sessions

@router.get("/sessions")
def sessions(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sid = ""
    token = request.cookies.get(ACCESS_COOKIE) or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    from backend.security import decode_token

    payload = decode_token(token, expected_type="access") if token else None
    if payload:
        sid = payload.get("sid") or ""
    return {"items": svc.list_sessions(db, user, current_sid=sid)}


@router.delete("/sessions/{session_id}")
def revoke_one(session_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Revoke one of the CALLER's own sessions (ownership is checked server-side)."""
    from backend.models import AuthSession

    row = db.query(AuthSession).filter(AuthSession.session_id == session_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.username != user.username:
        raise HTTPException(status_code=403, detail="You can only end your own sessions.")
    svc.revoke_session(db, session_id, reason="revoked by user")
    return {"message": "Session ended."}


# ---------------------------------------------------------------- password

@router.post("/password/change")
def password_change(body: PasswordChangeIn, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        svc.change_password(db, user, body.current_password, body.new_password)
    except AuthError as err:
        _raise(err)
    _clear_session_cookies(response)
    return {"message": "Password changed. All sessions were signed out — please log in again."}


@router.post("/password/reset/request")
def password_reset_request(body: PasswordResetRequestIn, request: Request, db: Session = Depends(get_db)):
    try:
        return svc.request_password_reset(db, body.identifier, ip=_client_ip(request))
    except AuthError as err:
        _raise(err)


@router.post("/password/reset/confirm")
def password_reset_confirm(body: PasswordResetConfirmIn, db: Session = Depends(get_db)):
    try:
        svc.confirm_password_reset(db, body.token, body.new_password)
    except AuthError as err:
        _raise(err)
    return {"message": "Password reset. You can now log in with the new password."}


# ---------------------------------------------------------------- MFA management

@router.post("/mfa/setup")
def mfa_setup(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Returns the shared secret + otpauth URI ONCE for the user to add to their app."""
    return svc.begin_mfa_setup(db, user)


@router.post("/mfa/enable")
def mfa_enable(body: MfaCodeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return svc.enable_mfa(db, user, body.code)
    except AuthError as err:
        _raise(err)


@router.post("/mfa/disable")
def mfa_disable(body: MfaCodeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        svc.disable_mfa(db, user, body.code)
    except AuthError as err:
        _raise(err)
    return {"message": "MFA disabled."}


@router.post("/mfa/recovery-codes")
def mfa_recovery_codes(body: MfaCodeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return svc.regenerate_recovery_codes(db, user, body.code)
    except AuthError as err:
        _raise(err)
