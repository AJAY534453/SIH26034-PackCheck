"""Authentication, session-security and role-separation tests.

Covers: cookie sessions, brute-force lockout, revoked sessions, password change/reset,
MFA (TOTP + one-time recovery codes), refresh-token rotation with reuse detection,
role portals, and organization-level data isolation.
"""
from __future__ import annotations

import uuid

from backend.security import totp_now


def _login(client, username, password, **extra):
    """Log in. These tests use bearer tokens, so they opt in the same way an API client would —
    the browser app receives no token in the body (asserted separately below)."""
    extra.setdefault("include_token", True)
    return client.post("/auth/login", json={"username": username, "password": password, **extra})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_user(username: str, password: str, role: str = "VIEWER", **kwargs) -> int:
    from backend.database import SessionLocal
    from backend.models import User
    from backend.security import hash_password

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        if u is None:
            u = User(username=username, full_name=username, role=role,
                     password_hash=hash_password(password), **kwargs)
            db.add(u)
            db.commit()
            db.refresh(u)
        return u.id
    finally:
        db.close()


def _make_org(name: str, kind: str = "BUSINESS") -> int:
    from backend.database import SessionLocal
    from backend.models import Organization

    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.name == name).first()
        if o is None:
            o = Organization(name=name, kind=kind)
            db.add(o)
            db.commit()
            db.refresh(o)
        return o.id
    finally:
        db.close()


# ------------------------------------------------------------------ sessions & cookies

def test_login_sets_httponly_session_cookie_and_me_works_without_header(client):
    r = _login(client, "inspector", "inspector123")
    assert r.status_code == 200
    set_cookie = "; ".join(r.headers.get_list("set-cookie"))
    assert "pck_access=" in set_cookie
    assert "pck_refresh=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite" in set_cookie

    # The cookie alone authenticates (no Authorization header).
    me = client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["role"] == "INSPECTOR"
    assert "inspections.view" in body["permissions"]
    # No secret ever appears in the user payload.
    for secret in ("password_hash", "mfa_secret", "mfa_recovery_codes", "access_token", "refresh_token"):
        assert secret not in me.text


def test_login_response_carries_role_home_and_permissions(client):
    body = _login(client, "officer", "officer123").json()
    assert body["role"] == "ENFORCEMENT_OFFICER"
    assert body["home"] == "/enforcement"
    assert "enforcement.manage" in body["permissions"]


def test_login_body_carries_no_token_unless_explicitly_requested(client):
    """The browser app authenticates with the HttpOnly cookie — never with a body credential."""
    default = _login(client, "viewer", "viewer123", include_token=False).json()
    assert default["access_token"] == ""
    assert "refresh_token" not in default
    assert client.get("/auth/me").status_code == 200  # the cookie alone is sufficient

    explicit = _login(client, "viewer", "viewer123", include_token=True).json()
    assert explicit["access_token"]
    assert client.get("/auth/me", headers=_bearer(explicit["access_token"])).status_code == 200


def test_credentials_are_never_accepted_in_the_query_string(client):
    """A token in a URL leaks through logs, history and Referer — the server refuses it."""
    token = _login(client, "viewer", "viewer123").json()["access_token"]
    client.cookies.clear()
    assert client.get("/auth/me", params={"token": token}).status_code == 401
    assert client.get("/inspections", params={"token": token}).status_code == 401


def test_logout_revokes_the_session_server_side(client):
    token = _login(client, "viewer", "viewer123").json()["access_token"]
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 200
    assert client.post("/auth/logout", headers=_bearer(token)).status_code == 200
    # The token is signed and unexpired, but its session is gone -> rejected.
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 401


def test_role_hint_never_grants_a_role(client):
    """Selecting a role on the login screen is a hint, not authorization."""
    r = _login(client, "viewer", "viewer123", role_hint="ADMIN")
    assert r.status_code == 200
    assert r.json()["role"] == "VIEWER"
    assert client.post("/inspections", headers=_bearer(r.json()["access_token"])).status_code == 403


# ------------------------------------------------------------------ brute force

def test_repeated_failures_lock_the_account(client):
    username = f"lock_{uuid.uuid4().hex[:8]}"
    _make_user(username, "CorrectHorse1", role="VIEWER")
    for _ in range(4):
        assert _login(client, username, "wrong-password").status_code == 401
    # the 5th failure trips the lock
    r = _login(client, username, "wrong-password")
    assert r.status_code == 423
    # even the CORRECT password is refused while locked
    assert _login(client, username, "CorrectHorse1").status_code == 423


def test_unknown_account_and_wrong_password_share_a_message(client):
    a = _login(client, "no-such-user-xyz", "whatever")
    b = _login(client, "viewer", "definitely-wrong")
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_disabled_account_is_refused(client):
    username = f"disabled_{uuid.uuid4().hex[:8]}"
    _make_user(username, "CorrectHorse1", role="VIEWER", is_active=False)
    assert _login(client, username, "CorrectHorse1").status_code == 403


# ------------------------------------------------------------------ password flows

def test_password_change_requires_current_password_and_revokes_sessions(client):
    username = f"pw_{uuid.uuid4().hex[:8]}"
    _make_user(username, "OldPassword1", role="VIEWER")
    token = _login(client, username, "OldPassword1").json()["access_token"]

    bad = client.post("/auth/password/change", headers=_bearer(token),
                      json={"current_password": "nope", "new_password": "NewPassword1"})
    assert bad.status_code == 400

    weak = client.post("/auth/password/change", headers=_bearer(token),
                       json={"current_password": "OldPassword1", "new_password": "short"})
    assert weak.status_code == 422

    ok = client.post("/auth/password/change", headers=_bearer(token),
                     json={"current_password": "OldPassword1", "new_password": "NewPassword1"})
    assert ok.status_code == 200
    # changing the password revokes every session, including the one used to change it
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 401
    assert _login(client, username, "OldPassword1").status_code == 401
    assert _login(client, username, "NewPassword1").status_code == 200


def test_password_reset_does_not_reveal_account_existence(client):
    known = client.post("/auth/password/reset/request", json={"identifier": "viewer"}).json()
    unknown = client.post("/auth/password/reset/request", json={"identifier": "ghost-42"}).json()
    assert known["message"] == unknown["message"]
    assert "reset_token" not in unknown  # nothing to leak for a non-account


def test_password_reset_flow_and_single_use_token(client):
    username = f"reset_{uuid.uuid4().hex[:8]}"
    _make_user(username, "Original123", role="VIEWER")
    token = client.post("/auth/password/reset/request", json={"identifier": username}).json()["reset_token"]

    bad = client.post("/auth/password/reset/confirm", json={"token": token, "new_password": "short"})
    assert bad.status_code == 422

    ok = client.post("/auth/password/reset/confirm", json={"token": token, "new_password": "ResetPassword1"})
    assert ok.status_code == 200
    assert _login(client, username, "ResetPassword1").status_code == 200
    # the token cannot be replayed
    again = client.post("/auth/password/reset/confirm", json={"token": token, "new_password": "Another123"})
    assert again.status_code == 400


# ------------------------------------------------------------------ MFA

def test_totp_mfa_enroll_challenge_and_recovery_code(client):
    username = f"mfa_{uuid.uuid4().hex[:8]}"
    _make_user(username, "Password123", role="INSPECTOR")
    token = _login(client, username, "Password123").json()["access_token"]

    setup = client.post("/auth/mfa/setup", headers=_bearer(token)).json()
    assert setup["secret"] and setup["otpauth_uri"].startswith("otpauth://totp/")

    # A wrong code does not enable MFA.
    assert client.post("/auth/mfa/enable", headers=_bearer(token), json={"code": "000000"}).status_code == 422

    enabled = client.post("/auth/mfa/enable", headers=_bearer(token), json={"code": totp_now(setup["secret"])})
    assert enabled.status_code == 200
    recovery = enabled.json()["recovery_codes"]
    assert len(recovery) >= 6

    # Login now stops at the MFA challenge — no session is issued yet.
    first = _login(client, username, "Password123")
    assert first.status_code == 200
    body = first.json()
    assert body["mfa_required"] is True
    assert body["access_token"] == ""

    bad = client.post("/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": "123456"})
    assert bad.status_code == 401

    good = client.post("/auth/mfa/verify", json={
        "mfa_token": body["mfa_token"], "code": totp_now(setup["secret"]), "include_token": True,
    })
    assert good.status_code == 200
    assert client.get("/auth/me", headers=_bearer(good.json()["access_token"])).status_code == 200

    # A recovery code works exactly once.
    second = _login(client, username, "Password123").json()
    used = client.post("/auth/mfa/verify", json={"mfa_token": second["mfa_token"], "recovery_code": recovery[0]})
    assert used.status_code == 200
    third = _login(client, username, "Password123").json()
    reused = client.post("/auth/mfa/verify", json={"mfa_token": third["mfa_token"], "recovery_code": recovery[0]})
    assert reused.status_code == 401


# ------------------------------------------------------------------ refresh rotation

def test_refresh_rotates_and_detects_reuse(client):
    _login(client, "viewer", "viewer123")
    old_refresh = client.cookies.get("pck_refresh")
    assert old_refresh

    rotated = client.post("/auth/refresh?include_token=true")
    assert rotated.status_code == 200
    new_token = rotated.json()["access_token"]
    assert new_token
    assert client.cookies.get("pck_refresh") != old_refresh
    assert client.get("/auth/me", headers=_bearer(new_token)).status_code == 200

    # Replaying the OLD refresh token is treated as theft: everything is revoked.
    client.cookies.set("pck_refresh", old_refresh)
    replay = client.post("/auth/refresh")
    assert replay.status_code == 401
    assert client.get("/auth/me", headers=_bearer(new_token)).status_code == 401


def test_sessions_listing_and_revoking_own_sessions(client):
    token = _login(client, "viewer", "viewer123").json()["access_token"]
    listing = client.get("/auth/sessions", headers=_bearer(token))
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert any(i["current"] for i in items)

    other = next(i for i in items if not i["current"]) if len(items) > 1 else None
    # revoking someone else's session id is refused
    assert client.delete("/auth/sessions/does-not-exist", headers=_bearer(token)).status_code == 404
    if other:
        assert client.delete(f"/auth/sessions/{other['session_id']}", headers=_bearer(token)).status_code == 200


# ------------------------------------------------------------------ roles & isolation

def test_role_portals_are_server_enforced(client):
    def token_for(u, p):
        return _login(client, u, p).json()["access_token"]

    officer = token_for("officer", "officer123")
    entity = token_for("entity", "entity123")
    compliance = token_for("compliance", "compliance123")

    # ROLE 1
    assert client.get("/enforcement/dashboard", headers=_bearer(officer)).status_code == 200
    assert client.get("/entity/dashboard", headers=_bearer(officer)).status_code == 403
    assert client.get("/internal/dashboard", headers=_bearer(officer)).status_code == 403
    # ROLE 2
    assert client.get("/entity/dashboard", headers=_bearer(entity)).status_code == 200
    assert client.get("/enforcement/dashboard", headers=_bearer(entity)).status_code == 403
    assert client.get("/internal/dashboard", headers=_bearer(entity)).status_code == 403
    # ROLE 3
    assert client.get("/internal/dashboard", headers=_bearer(compliance)).status_code == 200
    assert client.get("/enforcement/dashboard", headers=_bearer(compliance)).status_code == 403
    assert client.get("/entity/dashboard", headers=_bearer(compliance)).status_code == 403
    # a regulated entity cannot create or review inspections in the regulator pipeline
    assert client.post("/inspections", headers=_bearer(entity)).status_code == 403
    assert client.post("/reports/generate/1", headers=_bearer(entity)).status_code == 403
    # the audit trail is not reachable from the entity/enforcement portals
    assert client.get("/audit", headers=_bearer(officer)).status_code == 403
    assert client.get("/audit", headers=_bearer(entity)).status_code == 403


def test_organization_data_isolation(client):
    org_a = _make_org(f"Entity A {uuid.uuid4().hex[:6]}")
    org_b = _make_org(f"Entity B {uuid.uuid4().hex[:6]}")
    user_a = f"ent_a_{uuid.uuid4().hex[:6]}"
    user_b = f"ent_b_{uuid.uuid4().hex[:6]}"
    _make_user(user_a, "Password123", role="REGULATED_ENTITY", organization_id=org_a)
    _make_user(user_b, "Password123", role="REGULATED_ENTITY", organization_id=org_b)

    token_a = _login(client, user_a, "Password123").json()["access_token"]
    token_b = _login(client, user_b, "Password123").json()["access_token"]

    # Entity A files a submission — it is owned by A's organization.
    filed = client.post("/entity/submissions", headers=_bearer(token_a), json={"notes": "initial"})
    assert filed.status_code == 200
    submission_id = filed.json()["submission"]["id"]

    records_a = client.get("/entity/records", headers=_bearer(token_a)).json()["items"]
    records_b = client.get("/entity/records", headers=_bearer(token_b)).json()["items"]
    assert any(r["id"] == submission_id for r in records_a)
    assert all(r["id"] != submission_id for r in records_b), "entity B must not see entity A's record"

    # A direct fetch of another organization's record is not found (no existence leak).
    # The inspecting side (global) can still read it.
    admin = _login(client, "admin", "admin123").json()["access_token"]
    assert client.get(f"/inspections/{submission_id}", headers=_bearer(admin)).status_code == 200
    assert client.get(f"/inspections/{submission_id}", headers=_bearer(token_b)).status_code == 404


def test_organization_isolation_applies_to_inspection_list(client):
    org = _make_org(f"List Org {uuid.uuid4().hex[:6]}")
    user = f"ent_list_{uuid.uuid4().hex[:6]}"
    _make_user(user, "Password123", role="REGULATED_ENTITY", organization_id=org)
    token = _login(client, user, "Password123").json()["access_token"]

    inspector = _login(client, "inspector", "inspector123").json()["access_token"]
    other = client.post("/inspections", headers=_bearer(inspector)).json()["id"]

    items = client.get("/inspections", headers=_bearer(token)).json()["items"]
    assert all(i["id"] != other for i in items)
    assert client.get(f"/inspections/{other}", headers=_bearer(token)).status_code == 404


# ------------------------------------------------------------------ audit logging

def test_security_events_are_audited_without_secrets(client):
    username = f"audit_{uuid.uuid4().hex[:8]}"
    _make_user(username, "Password123", role="VIEWER")
    _login(client, username, "wrong")           # failed login
    _login(client, username, "Password123")     # successful login

    token = _login(client, "admin", "admin123").json()["access_token"]
    actions = client.get("/audit?limit=200", headers=_bearer(token)).json()["actions"]
    for expected in ("login", "login_failed"):
        assert expected in actions
    assert "password_hash" not in client.get("/audit?limit=5", headers=_bearer(token)).text
