"""Listing rule catalog loader.

Kept separate from :mod:`backend.rules.registry` on purpose. The registry seeds every rule in
``definitions/*.json`` into the database and the inspection pipeline evaluates every active rule
version — which is correct for package requirements and wrong for e-commerce listing requirements,
because a photograph of a pack cannot evidence a website. The catalog therefore lives in
``definitions/listings/`` (not matched by the registry's ``glob("*.json")``) and is loaded here for
the listing tools only.

Every check carries an explicit ``legal_status``. The application is not permitted to invent a
legal requirement, so a check whose citation is not confidently verified is marked
``LEGAL_REFERENCE_REQUIRES_VERIFICATION`` and rendered as such instead of being asserted.
"""
from __future__ import annotations

import json
from pathlib import Path

CATALOG_DIR = Path(__file__).resolve().parent / "definitions" / "listings"

VALID_STATUSES = (
    "VERIFIED_PRIMARY",
    "VERIFIED_SECONDARY_TEXT",
    "LEGAL_REFERENCE_REQUIRES_VERIFICATION",
    "APPLICATION_POLICY_NOT_A_LEGAL_PROVISION",
)

STATUS_LABELS = {
    "VERIFIED_PRIMARY": "Legal reference verified (primary source)",
    "VERIFIED_SECONDARY_TEXT": "Legal reference: substance verified, notification number to confirm",
    "LEGAL_REFERENCE_REQUIRES_VERIFICATION": "LEGAL_REFERENCE_REQUIRES_VERIFICATION",
    "APPLICATION_POLICY_NOT_A_LEGAL_PROVISION": "Application policy — not a legal provision",
}


def _load() -> list[dict]:
    checks: list[dict] = []
    for path in sorted(CATALOG_DIR.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # a malformed catalog must never take the tools down
            continue
        for check in doc.get("checks", []):
            check.setdefault("legal_status", "LEGAL_REFERENCE_REQUIRES_VERIFICATION")
            if check["legal_status"] not in VALID_STATUSES:
                check["legal_status"] = "LEGAL_REFERENCE_REQUIRES_VERIFICATION"
            check["catalog_id"] = doc.get("catalog_id", path.stem)
            checks.append(check)
    return checks


def all_checks() -> list[dict]:
    return _load()


def check_by_id(check_id: str) -> dict | None:
    return next((c for c in _load() if c.get("check_id") == check_id), None)


def declaration_check() -> dict | None:
    return check_by_id("LIST-6-10-DECLARATIONS")


def consistency_check() -> dict | None:
    return check_by_id("LIST-CONSISTENCY-POLICY")


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)
