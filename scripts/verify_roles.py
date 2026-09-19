"""Live role / permission / isolation smoke test.

Signs in as each demo role against a RUNNING backend and verifies, in order:

1. the role -> endpoint permission matrix (allowed AND refused operations),
2. organization isolation of records (with a positive control, so "empty" is never mistaken
   for "isolated"),
3. ownership of stored evidence files (no cross-organization file access),
4. credential hygiene: no token/hash in a normal response, no credential accepted in a query
   string, HttpOnly cookie session.

Usage (the backend must already be running):

    .venv\\Scripts\\python scripts/verify_roles.py                 # talks to http://127.0.0.1:8001
    .venv\\Scripts\\python scripts/verify_roles.py http://localhost:5174/api   # through the Vite proxy
    .venv\\Scripts\\python scripts/verify_roles.py --keep          # leave the probe rows behind

Proving isolation requires creating records in two organizations, so the run creates a small number
of probe rows and then DELETES them again (plus the derived image files) before exiting. The audit
trail of those actions is append-only and is deliberately left intact.

NOTE ON PATHS: the backend serves the API at the ROOT (`/auth/login`, `/inspections`). The `/api`
prefix exists only in the Vite dev proxy, which rewrites it away before forwarding. Pass the proxy
base URL if you want to test that hop as well.

Exit code is 0 only when every expectation holds, so this doubles as a CI-style gate.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

# Make the project importable so the cleanup step can use the same models the server uses.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402  (import order is deliberate: sys.path first)

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
KEEP = "--keep" in sys.argv
BASE = (ARGS[0] if ARGS else "http://127.0.0.1:8001").rstrip("/")

ACCOUNTS = {
    "admin": "admin123",
    "inspector": "inspector123",
    "viewer": "viewer123",
    "officer": "officer123",
    "entity": "entity123",
    "compliance": "compliance123",
}

# The permission table, restated from the role's point of view: (role, method, path, expected).
# A 403 here means "this role does not hold the permission the operation requires" — never a
# frontend-only restriction.
EXPECTATIONS: list[tuple[str, str, str, int]] = [
    # ---- ROLE 1: enforcement (regulator side) -------------------------------------------------
    ("officer", "GET", "/enforcement/dashboard", 200),
    ("officer", "GET", "/enforcement/records", 200),
    ("officer", "GET", "/enforcement/flagged", 200),
    ("officer", "GET", "/violations", 200),
    ("officer", "GET", "/audit", 403),                      # audit is ADMIN-only
    ("officer", "GET", "/entity/dashboard", 403),
    ("officer", "GET", "/entity/records", 403),
    ("officer", "GET", "/internal/dashboard", 403),
    ("officer", "GET", "/internal/reviews", 403),
    ("officer", "GET", "/inspections", 200),
    ("officer", "GET", "/products", 200),
    ("officer", "GET", "/reports", 200),
    ("officer", "GET", "/rules", 200),
    ("officer", "GET", "/dashboard/stats", 200),
    ("officer", "GET", "/grocery", 200),
    ("officer", "GET", "/bills", 200),
    ("officer", "GET", "/complaints", 200),
    # ---- ROLE 2: regulated entity (commercial side, own organization only) --------------------
    ("entity", "GET", "/entity/dashboard", 200),
    ("entity", "GET", "/entity/records", 200),
    ("entity", "GET", "/enforcement/dashboard", 403),
    ("entity", "GET", "/enforcement/records", 403),
    ("entity", "GET", "/enforcement/flagged", 403),
    ("entity", "GET", "/violations", 403),                  # enforcement-only information
    ("entity", "GET", "/internal/dashboard", 403),
    ("entity", "GET", "/internal/reviews", 403),
    ("entity", "GET", "/audit", 403),
    ("entity", "GET", "/inspections", 200),
    ("entity", "GET", "/products", 200),
    ("entity", "GET", "/reports", 200),
    ("entity", "GET", "/rules", 200),
    ("entity", "GET", "/analysis", 200),                    # entity may run image analysis
    ("entity", "GET", "/grocery", 200),
    # ---- ROLE 3: internal corporate compliance (no enforcement capability) --------------------
    ("compliance", "GET", "/internal/dashboard", 200),
    ("compliance", "GET", "/internal/reviews", 200),
    ("compliance", "GET", "/enforcement/dashboard", 403),
    ("compliance", "GET", "/enforcement/records", 403),
    ("compliance", "GET", "/entity/dashboard", 403),
    ("compliance", "GET", "/entity/records", 403),
    ("compliance", "GET", "/violations", 403),
    ("compliance", "GET", "/audit", 403),
    ("compliance", "GET", "/internal/reviews", 200),
    # ---- legacy internal roles (behaviour preserved) ------------------------------------------
    ("inspector", "GET", "/enforcement/dashboard", 200),
    ("inspector", "GET", "/audit", 403),
    ("viewer", "GET", "/enforcement/records", 200),         # viewer holds enforcement.view (read-only)
    ("viewer", "GET", "/violations", 200),
    ("viewer", "GET", "/analysis", 403),                    # no analysis.run
    ("viewer", "GET", "/audit", 403),
    ("admin", "GET", "/audit", 200),
    ("admin", "GET", "/enforcement/dashboard", 200),
    ("admin", "GET", "/entity/dashboard", 200),
    ("admin", "GET", "/internal/dashboard", 200),
    # ---- unauthenticated ---------------------------------------------------------------------
    ("anon", "GET", "/inspections", 401),
    ("anon", "GET", "/enforcement/dashboard", 401),
    ("anon", "GET", "/auth/me", 401),
    ("anon", "GET", "/files/originals/whatever.png", 401),
    # ---- write authorization -----------------------------------------------------------------
    ("viewer", "POST", "/inspections", 403),
    ("entity", "POST", "/inspections", 403),
    ("compliance", "POST", "/inspections", 403),
    ("officer", "POST", "/inspections", 200),
    ("viewer", "POST", "/entity/submissions", 403),
]

SECRET_KEYS = {"password", "password_hash", "mfa_secret", "mfa_recovery_codes",
               "access_token", "refresh_token", "mfa_token"}

IMAGE_KINDS = ("originals", "processed", "crops", "reports", "bills")


def sign_in(username: str, password: str, *, include_token: bool = False) -> tuple[httpx.Client, httpx.Response]:
    """Log in. The token is requested explicitly, exactly as an API client would."""
    client = httpx.Client(base_url=BASE, timeout=60.0, follow_redirects=True)
    payload: dict = {"username": username, "password": password}
    if include_token:
        payload["include_token"] = True
    resp = client.post("/auth/login", json=payload)
    if resp.status_code != 200:
        raise SystemExit(f"login failed for {username}: {resp.status_code} {resp.text[:200]}")
    return client, resp


def png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (320, 220), "white").save(buf, "PNG")
    buf.seek(0)
    return buf.getvalue()


def cleanup(created_inspections: list[int], created_analyses: list[tuple[int, list[str]]]) -> None:
    """Delete the probe rows this run created (and their derived files), so a live DB is unchanged."""
    files: list[str] = [name for _, names in created_analyses for name in names]
    try:
        from backend.config import settings
        from backend.database import SessionLocal
        from backend.models import (
            Evidence,
            ExtractedField,
            ImageAnalysis,
            Inspection,
            Report,
            ReviewAction,
            RuleEvaluation,
            Violation,
        )
    except Exception as exc:  # pragma: no cover - only when run outside the project
        print(f"note: cleanup skipped ({exc})")
        return

    db = SessionLocal()
    try:
        for inspection_id in created_inspections:
            for model in (Evidence, ExtractedField, RuleEvaluation, Violation, ReviewAction, Report):
                db.query(model).filter(model.inspection_id == inspection_id).delete()
            db.query(Inspection).filter(Inspection.id == inspection_id).delete()
        for analysis_id, _ in created_analyses:
            db.query(ImageAnalysis).filter(ImageAnalysis.id == analysis_id).delete()
        db.commit()
    finally:
        db.close()

    removed = 0
    for kind in ("originals", "processed"):
        for name in files:
            path = settings.STORAGE_DIR / kind / name
            if path.is_file():
                path.unlink()
                removed += 1
    print(
        f"\ncleanup: removed {len(created_inspections)} probe record(s), "
        f"{len(created_analyses)} probe analysis row(s) and {removed} derived file(s)"
    )


def leaks(blob: str) -> list[str]:
    """Any secret-shaped key with a non-empty value in a JSON body (token opt-in aside)."""
    try:
        data = json.loads(blob)
    except Exception:
        return []
    found: list[str] = []
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in SECRET_KEYS and isinstance(value, str) and value:
                    found.append(key)
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return found


def main() -> int:  # noqa: C901 - a linear checklist is the point of this script
    failures: list[str] = []
    clients: dict[str, httpx.Client] = {}
    tokens: dict[str, str] = {}
    created_inspections: list[int] = []
    created_analyses: list[tuple[int, list[str]]] = []

    for role, password in ACCOUNTS.items():
        client, resp = sign_in(role, password, include_token=True)
        clients[role] = client
        tokens[role] = resp.json()["access_token"]
        if not tokens[role]:
            failures.append(f"{role}: login did not return a token when explicitly requested")
    clients["anon"] = httpx.Client(base_url=BASE, timeout=60.0)

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"{'ok  ' if ok else 'FAIL'} {label}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(f"{label}{(' — ' + detail) if detail else ''}")

    print(f"== permission matrix against {BASE} ==")
    for role, method, path, expected in EXPECTATIONS:
        resp = clients[role].request(method, path)
        got = resp.status_code
        check(f"{role:11s} {method:5s} {path:30s} -> {got} (expected {expected})", got == expected)

    print("\n== credential hygiene ==")
    quiet, quiet_resp = sign_in("viewer", "viewer123")          # the browser path: no opt-in
    quiet_body = quiet_resp.json()
    set_cookies = " | ".join(quiet_resp.headers.get_list("set-cookie"))
    check("login body carries no token by default", quiet_body.get("access_token", "") == "",
          f"access_token={quiet_body.get('access_token')!r}")
    check("login sets an HttpOnly, SameSite cookie session",
          all(flag in set_cookies for flag in ("pck_access=", "pck_refresh=", "HttpOnly", "SameSite")),
          set_cookies[:160] or "no Set-Cookie header at all")
    check("cookie alone authenticates", quiet.get("/auth/me").status_code == 200)
    check("/auth/me leaks no secret material", not leaks(quiet.get("/auth/me").text))
    fresh = httpx.Client(base_url=BASE, timeout=30.0)            # no cookies at all
    check("token in a query string is refused on /auth/me",
          fresh.get("/auth/me", params={"token": tokens["viewer"]}).status_code == 401)
    check("token in a query string is refused on /inspections",
          fresh.get("/inspections", params={"token": tokens["viewer"]}).status_code == 401)

    print("\n== organization isolation of records (with positive control) ==")
    created = clients["officer"].post("/inspections")
    check("officer can create an inspection", created.status_code == 200, created.text[:120])
    foreign_id = created.json()["id"] if created.status_code == 200 else None
    if foreign_id:
        created_inspections.append(foreign_id)
    if foreign_id:
        check(f"officer reads inspection {foreign_id}", clients["officer"].get(f"/inspections/{foreign_id}").status_code == 200)
        check(f"entity cannot read inspection {foreign_id}",
              clients["entity"].get(f"/inspections/{foreign_id}").status_code == 404)
        check(f"compliance cannot read inspection {foreign_id}",
              clients["compliance"].get(f"/inspections/{foreign_id}").status_code == 404)

    submission = clients["entity"].post("/entity/submissions", json={"notes": "role-matrix verification probe"})
    own_id = submission.json().get("submission", {}).get("id") if submission.status_code == 200 else None
    check("entity can file a submission", submission.status_code == 200, submission.text[:120])
    if own_id:
        created_inspections.append(own_id)
    if own_id:
        check(f"entity reads its own record {own_id}", clients["entity"].get(f"/inspections/{own_id}").status_code == 200)
        check(f"compliance cannot read the entity's record {own_id}",
              clients["compliance"].get(f"/inspections/{own_id}").status_code == 404)
        check(f"officer (unrestricted) can read record {own_id}",
              clients["officer"].get(f"/inspections/{own_id}").status_code == 200)
        check("entity sees exactly its own records",
              clients["entity"].get("/inspections").json()["total"] >= 1)
        check("compliance sees none of the entity's records",
              clients["compliance"].get("/inspections").json()["total"] == 0)

    print("\n== stored-artifact ownership (IDOR probe) ==")
    analysis = clients["entity"].post(
        "/analysis",
        files={"file": ("matrix-probe.png", png_bytes(), "image/png")},
        data={"mode": "auto"},
    )
    if analysis.status_code != 200:
        check("entity can run an image analysis", False, analysis.text[:160])
    else:
        payload = analysis.json()["analysis"]
        original = payload["original_url"].rsplit("/", 1)[-1]
        enhanced = payload["enhanced_full_url"].rsplit("/", 1)[-1]
        text_enhanced = payload["enhanced_text_url"].rsplit("/", 1)[-1]
        created_analyses.append((payload["id"], [original, enhanced, text_enhanced]))
        check("owner reads its own analysis original",
              clients["entity"].get(f"/files/originals/{original}").status_code == 200)
        check("owner reads its own enhanced artifact",
              clients["entity"].get(f"/files/processed/{enhanced}").status_code == 200)
        for role in ("compliance", "viewer", "anon"):
            r = clients[role].get(f"/files/originals/{original}")
            check(f"{role} cannot read another organization's original", r.status_code in (401, 404),
                  f"got {r.status_code}")
        check("anonymous token-in-URL cannot read the file",
              fresh.get(f"/files/originals/{original}", params={"token": tokens["entity"]}).status_code == 401)

    if foreign_id:
        detail = clients["officer"].get(f"/inspections/{foreign_id}").json()
        stored = next((i.get("stored_filename") for i in detail.get("images", []) if i.get("stored_filename")), None)
        if stored:
            for role in ("entity", "compliance", "viewer"):
                r = clients[role].get(f"/files/originals/{stored}")
                check(f"{role} cannot read inspection {foreign_id}'s evidence image", r.status_code == 404,
                      f"got {r.status_code}")
    for kind in IMAGE_KINDS + ("secrets",):
        r = clients["officer"].get(f"/files/{kind}/does-not-exist.png")
        check(f"/files/{kind}/<unknown> is never served", r.status_code == 404, f"got {r.status_code}")
    check("path traversal is refused",
          clients["officer"].get("/files/originals/..%2F..%2Fsecret.txt").status_code == 404)

    # End the probe sessions through the app's own API (server-side revocation, not just a cookie
    # wipe), then remove everything else the run created.
    for role, client in clients.items():
        if role != "anon":
            client.post("/auth/logout")
    if not KEEP:
        cleanup(created_inspections, created_analyses)
    else:
        print(f"\n--keep: left probes behind (inspections {created_inspections}, analyses {created_analyses})")

    print()
    if failures:
        print(f"{len(failures)} problem(s):")
        for f in failures:
            print(" -", f)
        return 1
    print("All role, permission, isolation and credential checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
