"""Field normalization: canonical value + display value per field family.

Raw text is always preserved separately for audit. Normalization never invents data — it
reformats what extraction actually captured.
"""
from __future__ import annotations

import json
import re


def normalize_mrp(value: str) -> dict:
    """MRP value '200' or '200.00' -> {value: 200.0, currency: 'INR', display: '₹ 200.00'}."""
    try:
        amount = round(float(value.replace(",", "")), 2)
    except (ValueError, AttributeError):
        return {"value": None, "currency": None, "display": value}
    return {"value": amount, "currency": "INR", "display": f"₹ {amount:,.2f}"}


def normalize_quantity(value: str) -> dict:
    """Quantity JSON string {value, unit, quantity_type, ...} -> canonical display.

    Composite declarations (promotional extras, multi-packs, stated totals) keep their printed
    expression, extra part and stated total so the inspector can see that the reported quantity is
    the whole declaration rather than only its first number.
    """
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        return {"value": None, "unit": None, "quantity_type": None, "display": value}
    v, u, t = data.get("value"), data.get("unit"), data.get("quantity_type")
    display = f"{v} {u}" if u else str(v)
    out = {"value": v, "unit": u, "quantity_type": t, "display": display}
    expression = data.get("expression")
    if expression and str(expression).strip() and str(expression).strip() != display:
        out["declared_as"] = str(expression).strip()
        out["display"] = f"{display} (declared: {str(expression).strip()})"
    if data.get("extra_value") is not None:
        out["extra_value"] = f"{data['extra_value']} {u}".strip()
    if data.get("declared_total") is not None:
        out["declared_total"] = f"{data['declared_total']} {u}".strip()
    if data.get("pack_count") is not None:
        out["pack_count"] = data["pack_count"]
    if data.get("parts_uncombined"):
        out["parts_uncombined"] = True
    return out


_REFERENCE_LABEL = {
    "MANUFACTURING_DATE": "the date of manufacture",
    "PACKING_DATE": "the date of packing",
    "IMPORT_DATE": "the date of import",
}


def normalize_declaration(value: str) -> dict:
    """A structured (JSON) declaration value -> canonical parts + a human-readable display.

    Used for duration declarations such as 'BEST BEFORE 12 MONTHS FROM THE DATE OF
    MANUFACTURING', which are stored semantically (type / duration / reference) rather than being
    converted into a calendar date. The display is built from those parts so the inspector reads
    the declaration rather than raw JSON.
    """
    try:
        obj = json.loads(value)
    except Exception:
        return {"value": value, "display": value}
    if not isinstance(obj, dict) or "type" not in obj:
        return {"value": value, "display": value}
    out = dict(obj)
    out["value"] = value
    if obj.get("type") == "DURATION_FROM_REFERENCE":
        ref = _REFERENCE_LABEL.get(str(obj.get("reference")), "the reference date")
        out["display"] = f"{obj.get('duration', '')} from {ref}".strip()
    else:
        out.setdefault("display", value)
    return out


def normalize_date(value: str) -> dict:
    """Date value 'YYYY-MM-DD' | 'YYYY-MM' (|AMBIGUOUS suffix) -> canonical parts.

    A duration declaration (structured JSON) is normalised structurally instead — it is a
    declaration, not a calendar date, and is never converted into one.
    """
    if value.lstrip().startswith("{"):
        return normalize_declaration(value)
    ambiguous = value.endswith("|AMBIGUOUS")
    v = value.replace("|AMBIGUOUS", "")
    parts = v.split("-")
    out: dict = {"iso": v or None, "ambiguous": ambiguous, "display": v}
    if len(parts) == 3:
        out["day"], out["month"], out["year"] = parts[2], parts[1], parts[0]
    elif len(parts) == 2:
        out["month"], out["year"] = parts[1], parts[0]
    return out


def normalize_email(value: str) -> dict:
    v = (value or "").strip().strip("\"'").replace("\\", "")
    ok = "@" in v and "." in v.split("@")[-1] and " " not in v
    return {"value": v if ok else value, "valid": ok, "display": v if ok else value}


def normalize_website(value: str) -> dict:
    v = (value or "").strip().lower().replace("\\", "")
    ok = ("www." in v or v.startswith("http")) and " " not in v
    return {"value": v if ok else value, "valid": ok, "display": v if ok else value}


def normalize_phone(value: str) -> dict:
    digits = re_digits(value)
    valid = len(digits) == 10 or (len(digits) in (11, 12) and digits.startswith(("0", "91")))
    return {"value": digits, "valid": valid, "display": value}


_CASE_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _space_word_joins(text: str) -> str:
    """Insert the space an OCR run joined away: 'KonguNagar' -> 'Kongu Nagar'.

    Only a lowercase-letter -> uppercase-letter transition inside a token is treated as a join,
    and only when both sides are at least two characters, so ALL-CAPS fused strings (which we
    genuinely cannot split without inventing words, e.g. an all-caps company name) are left
    exactly as read.
    """
    out: list[str] = []
    for token in text.split(" "):
        if re.search(r"[a-z][A-Z]", token):
            parts = [p for p in _CASE_BOUNDARY.split(token) if p]
            if len(parts) > 1 and all(len(p) >= 2 for p in parts):
                out.append(" ".join(parts))
                continue
        out.append(token)
    return " ".join(out)


def normalize_text_block(value: str) -> dict:
    """Readable display for entity/address blocks WITHOUT touching the raw OCR evidence.

    Fixes only presentational defects that OCR introduces inside a correctly-read string:
    word joins, repeated/spaced commas, missing space after a comma, and a PIN-code hyphen.
    No word is ever added, removed, reordered or corrected — the raw value stays authoritative
    and is what evidence displays.
    """
    raw = value or ""
    text = raw.strip()
    if not text or "\n" in text:
        return {"value": raw, "display": raw}
    text = _space_word_joins(text)
    text = re.sub(r"\s*,\s*(?:,\s*)+", ", ", text)  # ',' repeated -> single comma
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(?<=[A-Za-z])-(?=\d)", " - ", text)
    text = re.sub(r"(?<=\d)-(?=[A-Za-z])", " - ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,;")
    return {"value": raw, "display": text or raw}


def normalize_generic(value: str) -> dict:
    return {"value": value, "display": value}


def re_digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


NORMALIZERS = {
    "mrp": normalize_mrp,
    "unit_sale_price": normalize_generic,
    "net_quantity": normalize_quantity,
    "batch_lot": normalize_generic,
    "fssai_license": normalize_generic,
    "country_of_origin": normalize_generic,
    "consumer_care_phone": normalize_phone,
    "consumer_care_email": normalize_email,
    "website": normalize_website,
    "manufacturer": normalize_text_block,
    "packer": normalize_text_block,
    "importer": normalize_text_block,
    "marketer": normalize_text_block,
}

# Entity/address families carry free text; their display is tidied by the text-block normalizer.
_TEXT_BLOCK_PREFIXES = ("manufacturer", "packer", "importer", "marketer")


def normalize_field(field_name: str, value: str) -> dict:
    """Normalize a field value. Falls back to generic for any unknown field (extensible)."""
    if field_name.startswith("date_"):
        fn = normalize_date
    elif field_name in NORMALIZERS:
        fn = NORMALIZERS[field_name]
    elif field_name.endswith("_address") or field_name.startswith(_TEXT_BLOCK_PREFIXES):
        fn = normalize_text_block
    else:
        fn = normalize_generic
    try:
        return fn(value)
    except Exception:
        return {"value": value, "display": value}


def normalize_date_fields(fields: dict[str, str]) -> dict[str, dict]:
    """Normalize all date_* fields in a dict of field->value."""
    out: dict[str, dict] = {}
    for name, value in fields.items():
        if name.startswith("date_"):
            out[name] = normalize_date(value)
    return out
