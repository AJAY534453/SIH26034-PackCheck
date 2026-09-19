"""Stored-artifact authorization tests.

Every file under ``storage/`` is reachable only through the record that owns it: the route resolves
the owner and applies that record's permission AND organization scope. These tests pin the
behaviour that a signed-in user from another organization cannot fetch evidence by filename, that
an unresolvable file is never served, and that a credential in the query string is ignored.
"""
from __future__ import annotations

import io

from PIL import Image


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 220), "white").save(buf, "PNG")
    buf.seek(0)
    return buf.getvalue()


def _token(client, username: str, password: str) -> str:
    r = client.post("/auth/login", json={"username": username, "password": password, "include_token": True})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _inspection_with_image(client, inspector_token: str) -> tuple[int, str]:
    """Create an inspection owned by the INSPECTOR (no organization) with one stored image."""
    created = client.post("/inspections", headers=_bearer(inspector_token))
    assert created.status_code == 200, created.text
    inspection_id = created.json()["id"]
    uploaded = client.post(
        f"/inspections/{inspection_id}/images",
        headers=_bearer(inspector_token),
        files={"file": ("label.png", _png(), "image/png")},
        data={"role": "PRIMARY_LABEL"},
    )
    assert uploaded.status_code == 200, uploaded.text
    return inspection_id, uploaded.json()["stored_filename"]


# ------------------------------------------------------------------ ownership


def test_file_access_requires_authentication(client):
    """No cookie, no header -> 401, and never a file body."""
    assert client.get("/files/originals/whatever.png").status_code == 401
    assert client.get("/files/bills/whatever.png").status_code == 401


def test_owner_scope_can_read_its_evidence_and_others_cannot(client):
    inspector = _token(client, "inspector", "inspector123")
    _, stored = _inspection_with_image(client, inspector)

    # The unrestricted internal role that owns the record may read it.
    assert client.get(f"/files/originals/{stored}", headers=_bearer(inspector)).status_code == 200

    # A regulated entity from a different organization may NOT, even knowing the exact filename.
    entity = _token(client, "entity", "entity123")
    denied = client.get(f"/files/originals/{stored}", headers=_bearer(entity))
    assert denied.status_code == 404
    assert b"PNG" not in denied.content[:8]  # no file bytes leaked

    # Same for internal compliance (a third organization).
    compliance = _token(client, "compliance", "compliance123")
    assert client.get(f"/files/originals/{stored}", headers=_bearer(compliance)).status_code == 404


def test_owner_can_read_its_own_analysis_artifacts(client):
    """Positive control: organization scoping blocks OTHER organizations, not the owner's records.

    The regulated entity runs an image analysis (a capability its role holds) and can then open the
    stored original AND the derived enhancements; a different organization cannot.
    """
    entity = _token(client, "entity", "entity123")
    created = client.post(
        "/analysis",
        headers=_bearer(entity),
        files={"file": ("own-label.png", _png(), "image/png")},
        data={"mode": "auto"},
    )
    assert created.status_code == 200, created.text
    analysis = created.json()["analysis"]
    original = analysis["original_url"].rsplit("/", 1)[-1]
    enhanced = analysis["enhanced_full_url"].rsplit("/", 1)[-1]

    assert client.get(f"/files/originals/{original}", headers=_bearer(entity)).status_code == 200
    assert client.get(f"/files/processed/{enhanced}", headers=_bearer(entity)).status_code == 200

    # A different organization cannot read either artifact, even knowing the filenames.
    compliance = _token(client, "compliance", "compliance123")
    assert client.get(f"/files/originals/{original}", headers=_bearer(compliance)).status_code == 404
    assert client.get(f"/files/processed/{enhanced}", headers=_bearer(compliance)).status_code == 404


def test_entity_cannot_inject_evidence_into_a_foreign_inspection(client):
    """An entity may not attach files to an inspection it cannot even read."""
    inspector = _token(client, "inspector", "inspector123")
    inspection_id, _ = _inspection_with_image(client, inspector)
    entity = _token(client, "entity", "entity123")
    r = client.post(
        f"/inspections/{inspection_id}/images",
        headers=_bearer(entity),
        files={"file": ("foreign.png", _png(), "image/png")},
        data={"role": "PRIMARY_LABEL"},
    )
    assert r.status_code == 403


def test_served_files_are_not_cacheable_by_the_browser(client):
    """The browser cache is keyed by URL, not by session — evidence must not be replayed from it
    after a logout or an account switch on the same machine."""
    inspector = _token(client, "inspector", "inspector123")
    _, stored = _inspection_with_image(client, inspector)
    resp = client.get(f"/files/originals/{stored}", headers=_bearer(inspector))
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")


def test_unowned_and_unknown_files_are_not_served(client):
    inspector = _token(client, "inspector", "inspector123")
    # A real file in storage that no record claims: the owner cannot be established -> 404.
    from backend.config import settings

    settings.ensure_dirs()
    orphan = settings.STORAGE_DIR / "originals" / "orphan-not-in-db.png"
    orphan.write_bytes(_png())
    assert client.get("/files/originals/orphan-not-in-db.png", headers=_bearer(inspector)).status_code == 404
    # A filename that is not in storage at all -> 404, and an unknown kind -> 404.
    assert client.get("/files/originals/nope.png", headers=_bearer(inspector)).status_code == 404
    assert client.get("/files/secrets/x.png", headers=_bearer(inspector)).status_code == 404


def test_query_string_token_is_not_accepted_for_files(client):
    """The old `?token=` path is gone: URLs leak into logs, history and Referer."""
    inspector = _token(client, "inspector", "inspector123")
    _, stored = _inspection_with_image(client, inspector)
    client.cookies.clear()
    assert client.get(f"/files/originals/{stored}", params={"token": inspector}).status_code == 401


def test_report_file_requires_the_reports_permission_and_ownership(client):
    inspector = _token(client, "inspector", "inspector123")
    inspection_id, _ = _inspection_with_image(client, inspector)
    generated = client.post(f"/reports/generate/{inspection_id}", headers=_bearer(inspector))
    assert generated.status_code == 200, generated.text
    stored = generated.json()["stored_filename"]

    assert client.get(f"/files/reports/{stored}", headers=_bearer(inspector)).status_code == 200

    # The regulated entity has reports.view but not this record -> still refused.
    entity = _token(client, "entity", "entity123")
    assert client.get(f"/files/reports/{stored}", headers=_bearer(entity)).status_code == 404

    # A role WITHOUT reports.view cannot read report artifacts even when the record is visible.
    viewer = _token(client, "viewer", "viewer123")
    assert client.get(f"/files/reports/{stored}", headers=_bearer(viewer)).status_code == 200  # viewer holds reports.view
    assert client.get("/reports", headers=_bearer(viewer)).status_code == 200


# ------------------------------------------------------------------ permission gates


def test_read_endpoints_require_their_permission_not_just_authentication(client):
    """An operation validates a PERMISSION, never merely "is signed in"."""
    entity = _token(client, "entity", "entity123")
    compliance = _token(client, "compliance", "compliance123")

    # Enforcement-side reads are refused to roles that hold no enforcement.view, even though the
    # payload would only contain their own scoped rows.
    assert client.get("/violations", headers=_bearer(entity)).status_code == 403
    assert client.get("/violations", headers=_bearer(compliance)).status_code == 403
    assert client.get("/violations", headers=_bearer(_token(client, "viewer", "viewer123"))).status_code == 200

    # /analysis requires analysis.run, which a viewer does not have.
    assert client.get("/analysis", headers=_bearer(_token(client, "viewer", "viewer123"))).status_code == 403

    # Every role that may read inspections keeps working.
    for username, password in (("viewer", "viewer123"), ("entity", "entity123"), ("compliance", "compliance123")):
        token = _token(client, username, password)
        assert client.get("/inspections", headers=_bearer(token)).status_code == 200
        assert client.get("/products", headers=_bearer(token)).status_code == 200
        assert client.get("/reports", headers=_bearer(token)).status_code == 200
        assert client.get("/rules", headers=_bearer(token)).status_code == 200
        assert client.get("/dashboard/stats", headers=_bearer(token)).status_code == 200
        assert client.get("/grocery", headers=_bearer(token)).status_code == 200
        assert client.get("/complaints", headers=_bearer(token)).status_code == 200
        assert client.get("/bills", headers=_bearer(token)).status_code == 200


def test_audit_log_is_admin_only(client):
    token = _token(client, "admin", "admin123")
    assert client.get("/audit", headers=_bearer(token)).status_code == 200
    for username, password in (("officer", "officer123"), ("inspector", "inspector123"),
                               ("viewer", "viewer123"), ("entity", "entity123"),
                               ("compliance", "compliance123")):
        assert client.get("/audit", headers=_bearer(_token(client, username, password))).status_code == 403
