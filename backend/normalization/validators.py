"""Field-specific validators: structural/technical checks per field family.

These validators answer: "Is this extracted value structurally valid for its field type?"
They are fully separate from legal compliance (that is the rule engine's job) and from
extraction confidence (a separate concept). Validators never change values — they add
issues that flow into `uncertainty_reason` for the reviewer.
"""
from __future__ import annotations

import json
import re

EMAIL_OK = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
# Indian mobile (10 digits starting 6-9, optional 0/91 prefix), toll-free (1800 ...),
# and +91 international forms.
PHONE_OK = re.compile(
    r"^(?:(?:0|91)?[6-9]\d{9}|(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}|1800\d{3,7}|"
    r"1800[\s-]\d{3}[\s-]\d{3,4})$"
)
DOMAIN_OK = re.compile(
    r"^(?:https?://)?(?:www\.)?[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}(?:/[^\s]*)?$"
)


def _issues(*found: str) -> list[str]:
    return [f for f in found if f]


def validate_mrp(normalized: dict) -> list[str]:
    v = normalized.get("value")
    if v is None or not isinstance(v, (int, float)) or v <= 0:
        return _issues("MRP is not a positive number")
    if v > 10_000_000:
        return _issues("MRP value is implausibly large — verify against the evidence crop")
    return []


def validate_quantity(normalized: dict) -> list[str]:
    v, u = normalized.get("value"), normalized.get("unit")
    if v in (None, "") or u not in ("g", "kg", "ml", "L", "N", "cm", "m", "mm"):
        return _issues("quantity value or unit not recognized")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return _issues("quantity value is not numeric")
    if f <= 0:
        return _issues("quantity value must be positive")
    return []


def validate_date(normalized: dict) -> list[str]:
    issues: list[str] = []
    iso = normalized.get("iso")
    if not iso:
        return issues
    dur_ok = re.match(r"^\d{1,3}\s*(month|months|day|days|year|years|week|weeks)$", iso, re.IGNORECASE)
    sem = normalized.get("semantic")
    if sem:
        # duration-from-reference semantic value
        if sem.get("type") != "DURATION_FROM_REFERENCE" or not sem.get("reference"):
            issues.append("duration value missing reference")
        return issues
    if not dur_ok and not re.match(r"^\d{4}-\d{2}(-\d{2})?$", iso):
        issues.append("date is not in canonical YYYY-MM-DD or YYYY-MM form")
    if normalized.get("ambiguous"):
        issues.append("date interpretation is ambiguous (day/month order) — verify with the inspector")
    return issues


def validate_email(normalized: dict) -> list[str]:
    v = str(normalized.get("value") or "")
    if v and not EMAIL_OK.match(v):
        return _issues("email is structurally invalid")
    return []


# Mail providers any small brand legitimately uses, so their domain carries no evidence either way.
PUBLIC_MAIL_DOMAINS = {
    "gmail", "googlemail", "yahoo", "yahoomail", "hotmail", "outlook", "live", "msn",
    "rediffmail", "rediff", "protonmail", "proton", "icloud", "me", "aol", "zoho", "yandex",
    "gmx", "mail",
}


def corroborate_email_domain(email: str, other_declarations: str) -> dict:
    """Is this address's domain corroborated by anything else the package declares?

    Structural validity is not correctness: OCR rewrites a domain's letters while leaving the
    '@name.tld' shape intact ('britindia' → 'briindio'), and the result still passes any regex.
    Where the pack gives us something to check the domain against (brand, product, entity,
    website), a domain that matches none of them is surfaced for review instead of being
    published as a detected contact. Public mail providers are exempt.

    Returns {'corroborated': True|False|None, 'note': str} — None means the question cannot be
    asked (no address, or no other declaration to check against).
    """
    value = (email or "").strip()
    if "@" not in value:
        return {"corroborated": None, "note": ""}
    domain = value.rsplit("@", 1)[-1].strip().lower()
    core = domain.split(".")[0]
    if not core:
        return {"corroborated": None, "note": ""}
    if core in PUBLIC_MAIL_DOMAINS:
        return {"corroborated": True, "note": "public mail provider"}
    haystack = "".join(ch for ch in (other_declarations or "").lower() if ch.isalnum())
    if core in haystack:
        return {"corroborated": True, "note": f"domain '{core}' also appears in the package's own text"}
    return {
        "corroborated": False,
        "note": (
            f"Email structure is valid but its domain '{domain}' could not be corroborated by any "
            "other declaration on the package (brand / product / manufacturer / website), and it "
            "is not a public mail provider — possible OCR corruption of the address. Confirm "
            "against the evidence crop."
        ),
    }


def validate_phone(normalized: dict) -> list[str]:
    v = str(normalized.get("value") or "")
    if v and not PHONE_OK.match(v):
        return _issues("phone number is structurally invalid")
    return []


def validate_website(normalized: dict) -> list[str]:
    v = str(normalized.get("value") or "")
    if v and not DOMAIN_OK.match(v):
        return _issues("website is structurally invalid")
    return []


def validate_fssai(normalized: dict) -> list[str]:
    v = str(normalized.get("value") or "")
    if not v:
        return []
    if not v.isdigit():
        return _issues("FSSAI number must be digits only")
    if len(v) == 14:
        return []
    if len(v) == 10:
        return _issues("10-digit FSSAI-like number detected; current licences are 14-digit — verify")
    return _issues(f"FSSAI number length is unusual ({len(v)} digits; expected 14)")


def validate_batch(normalized: dict) -> list[str]:
    v = str(normalized.get("value") or "")
    if not v:
        return []
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9\-/]{2,20}$", v):
        return _issues("batch code has unexpected structure")
    return []


VALIDATORS = {
    "mrp": validate_mrp,
    "net_quantity": validate_quantity,
    "date_manufacturing": validate_date,
    "date_packing": validate_date,
    "date_import": validate_date,
    "date_expiry": validate_date,
    "date_best_before": validate_date,
    "consumer_care_email": validate_email,
    "consumer_care_phone": validate_phone,
    "website": validate_website,
    "fssai_license": validate_fssai,
    "batch_lot": validate_batch,
}


def validate_field(field_name: str, normalized: dict) -> dict:
    """Return {valid: bool, issues: [str]} — structural validity of one field."""
    fn = VALIDATORS.get(field_name)
    if fn is None:
        return {"valid": True, "issues": []}
    try:
        issues = fn(normalized)
    except Exception:
        issues = ["validator error — treat as unverified"]
    return {"valid": not issues, "issues": issues}


def validate_fields(fields: dict[str, dict]) -> dict[str, dict]:
    """Validate a map of field_name -> normalized dict."""
    return {name: validate_field(name, norm) for name, norm in fields.items()}
