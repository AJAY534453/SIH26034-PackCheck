"""Net quantity extraction: Net-context anchored, unit-whitelisted, nutrition-excluded.

Quantity model: {value, unit, quantity_type}. Never confuse nutrition values with net quantity.
"""
from __future__ import annotations

import json
import re

from backend.extraction.base import (
    IDENTIFIER_ANCHOR_RE,
    Candidate,
    compose_confidence,
    identifier_value_line_flags,
    is_nutrition_line,
    lines_sorted,
)
from backend.ocr.base import OcrLine

NET_KEYWORD_RE = re.compile(
    r"\bnet\s*(?:qty|quantity|wt\.?|weight|volume|contents?|content)\b|\bnett+\s*(?:wt\.?|weight)\b",
    re.IGNORECASE,
)
# The unit may be RUN INTO the following word by OCR ('28.4g+6.1gEXTRA-34'), so the unit is
# terminated by "not a lowercase letter or digit" rather than by a word boundary: a trailing
# `\b` fails on 'gEXTRA' and the whole quantity is then missed. Lowercase continuations are
# still rejected, so 'gms'/'gram' keep matching through the alternation and a unit can never be
# carved out of a longer word.
QUANTITY_RE = re.compile(
    r"\b([\d.,]+)\s*(kg|kgs|kilogram(?:s)?|g|gm|gms|gram(?:s)?|ml|millilit(?:e|er)s?|l|ltr|litre|liter|"
    r"nos?|no\.?|n\b(?=\s|$)|pcs?|pieces?|units?|cm|m|mm)\.?(?!(?-i:[a-z0-9]))",
    re.IGNORECASE,
)

# A stated total whose unit was lost by OCR ('28.4g+6.1gEXTRA-34', '28.4 g + 6.1 g = 34.5'): the
# separator before the final bare number is an equals sign (or its dot-matrix/OCR stand-in, a
# hyphen) and the number closes the declaration.
BARE_TOTAL_RE = re.compile(r"[=\-\u2013\u2014]\s*([\d.,]+)\s*\.?$")

UNIT_MAP = {
    "kg": ("kg", "MASS"), "kgs": ("kg", "MASS"), "kilogram": ("kg", "MASS"), "kilograms": ("kg", "MASS"),
    "g": ("g", "MASS"), "gm": ("g", "MASS"), "gms": ("g", "MASS"), "gram": ("g", "MASS"), "grams": ("g", "MASS"),
    "n": ("N", "NUMBER"),
    "ml": ("ml", "VOLUME"), "millilitre": ("ml", "VOLUME"), "millilitres": ("ml", "VOLUME"),
    "milliliter": ("ml", "VOLUME"), "milliliters": ("ml", "VOLUME"),
    "l": ("L", "VOLUME"), "ltr": ("L", "VOLUME"), "litre": ("L", "VOLUME"), "liter": ("L", "VOLUME"),
    "nos": ("N", "NUMBER"), "no": ("N", "NUMBER"), "no.": ("N", "NUMBER"), "pcs": ("N", "NUMBER"),
    "pc": ("N", "NUMBER"), "piece": ("N", "NUMBER"), "pieces": ("N", "NUMBER"), "unit": ("N", "NUMBER"), "units": ("N", "NUMBER"),
    "cm": ("cm", "LENGTH"), "m": ("m", "LENGTH"), "mm": ("mm", "LENGTH"),
}


def _norm_value(raw: str) -> str | None:
    v = raw.replace(",", "").strip()
    try:
        f = float(v)
    except ValueError:
        return None
    if f <= 0:
        return None
    if f == int(f):
        return str(int(f))
    return ("%g" % f)


# ---------- composite quantity declarations ----------
#
# Real labels declare quantities in more than one form:
#   'Net Wt. 500 g'
#   '28.4 g + 6.1 g EXTRA'          (promotional extra)
#   '28.4 g + 6.1 g EXTRA = 34.5 g' (stated total)
#   '10 x 20 g'                     (multi-pack)
#   'Net Qty 1 kg + 100 g'
# Reporting only the first number ('28.4 g') mis-states the declaration, so a composite is
# parsed as a whole and the arithmetic is recorded instead of guessed.

# Conversion factors into each family's canonical base unit (g / ml / N).
_TO_BASE: dict[str, tuple[str, float]] = {
    "g": ("g", 1.0), "kg": ("g", 1000.0),
    "ml": ("ml", 1.0), "L": ("ml", 1000.0),
    "N": ("N", 1.0), "cm": ("cm", 1.0), "m": ("m", 1.0), "mm": ("mm", 1.0),
}

PACK_NOTATION_RE = re.compile(
    r"\b(\d{1,4})\s*[x\u00d7*]\s*([\d.,]+)\s*"
    r"(kg|kgs|kilogram(?:s)?|g|gm|gms|gram(?:s)?|ml|millilit(?:e|er)s?|l|ltr|litre|liter|"
    r"nos?|pcs?|pieces?|units?)\b\.?",
    re.IGNORECASE,
)

EXTRA_WORD_RE = re.compile(r"\b(?:extra|free|bonus|additional|complimentary)\b", re.IGNORECASE)

# Connector words that may legitimately separate two declared quantities.
_CONNECTOR_WORDS = {
    "", "+", "&", ",", "-", "and", "with", "plus", "of", "total", "net", "approx", "approximately",
    "extra", "free", "bonus", "additional", "complimentary",
    "wt", "wt.", "weight", "qty", "qty.", "quantity", "contents", "content",
}


def _fmt_num(f: float) -> str:
    return str(int(f)) if f == int(f) else ("%g" % f)


def _classify_connector(between: str) -> str | None:
    """Classify the text between two quantity tokens as a PACK / TOTAL / ADD connector."""
    b = between.strip().lower()
    if re.fullmatch(r"[x\u00d7*]", b):
        return "PACK"
    words = [w for w in (x.strip(".,;:=") for x in re.split(r"\s+", b)) if w]
    if not all(w in _CONNECTOR_WORDS for w in words):
        return None
    return "TOTAL" if "=" in b else "ADD"


def parse_quantity_expression(text: str) -> dict | None:
    """Parse a composite quantity declaration out of one OCR line.

    Returns None when the line carries a single (or non-combinable) quantity, so the caller keeps
    the plain single-token path. Never invents a number: every part comes from a printed token.
    """
    # --- multi-pack notation: '10 x 20 g', optionally combined: '10 x 20 g + 5 g FREE' ---
    pack = PACK_NOTATION_RE.search(text)
    pack_token: dict | None = None
    pack_count: int | None = None
    pack_unit_value: str | None = None
    scan_text = text
    if pack:
        info = UNIT_MAP.get(pack.group(3).lower().rstrip("."))
        count = _norm_value(pack.group(1))
        per = _norm_value(pack.group(2))
        if info is not None and count is not None and per is not None:
            unit, qtype = info
            pack_token = {"value": float(count) * float(per), "unit": unit, "qtype": qtype,
                          "start": 0, "end": 0}
            pack_count = int(float(count))
            pack_unit_value = _fmt_num(float(per))
            # Scan the rest of the line only, so the per-pack number is not counted twice.
            scan_text = text[pack.end():]

    tokens: list[dict] = [pack_token] if pack_token is not None else []
    for m in QUANTITY_RE.finditer(scan_text):
        info = UNIT_MAP.get(m.group(2).lower().rstrip("."))
        value = _norm_value(m.group(1))
        if info is None or value is None:
            return None
        tokens.append({
            "value": float(value), "unit": info[0], "qtype": info[1],
            "start": m.start(), "end": m.end(),
        })

    pack_note = ""
    if pack_token is not None:
        pack_note = (
            f"multi-pack declaration '{pack_count} x {pack_unit_value} {pack_token['unit']}' "
            "combined arithmetically"
        )
    if len(tokens) < 2:
        # A pure multi-pack declaration is itself a complete statement of quantity.
        if pack_token is not None:
            return {
                "combined": (_fmt_num(pack_token["value"]), pack_token["unit"], pack_token["qtype"]),
                "base": (pack_unit_value, pack_token["unit"]),
                "extra": None,
                "declared_total": None,
                "pack_count": pack_count,
                "unit_value": (pack_unit_value, pack_token["unit"]),
                "uncombined": [],
                "expression": text.strip(),
                "note": pack_note,
            }
        return None

    kinds: list[str] = []
    for i in range(len(tokens) - 1):
        kind = _classify_connector(scan_text[tokens[i]["end"]:tokens[i + 1]["start"]])
        if kind is None:
            return None  # unrelated text between the numbers — not one quantity declaration
        kinds.append(kind)

    total_token: dict | None = None
    if "TOTAL" in kinds:
        total_idx = kinds.index("TOTAL") + 1
        total_token = tokens[total_idx]
        parts = tokens[:total_idx]
    else:
        parts = tokens

    # Promotional extras: in 'base + extra EXTRA' the part after the sum sign is the promotional
    # one. A token used as the stated total is never counted as an extra.
    extras: list[dict] = [parts[1]] if (len(parts) >= 2 and EXTRA_WORD_RE.search(text)) else []

    qtypes = {t["qtype"] for t in parts}
    if len(qtypes) != 1:
        # Different measurement families (e.g. '500 g + 1 N') cannot be summed safely.
        return {
            "combined": None,
            "base": (_fmt_num(tokens[0]["value"]), tokens[0]["unit"]),
            "extra": None,
            "declared_total": None,
            "pack_count": None,
            "unit_value": None,
            "uncombined": parts,
            "expression": text.strip(),
            "note": "composite declaration mixes different measurement units and was not combined",
        }

    qtype = qtypes.pop()
    units = {t["unit"] for t in parts}
    # Report in the declared unit when every part shares one, otherwise in the family base unit.
    out_unit = parts[0]["unit"] if len(units) == 1 else _TO_BASE[parts[0]["unit"]][0]

    # Sum in each part's own base unit, then convert once into the reporting unit.
    total_base = sum(t["value"] * _TO_BASE[t["unit"]][1] for t in parts)
    reported = total_base / _TO_BASE[out_unit][1]
    # A printed total may carry no unit ("... = 34") or be closed by an OCR stand-in for '='.
    # The unit is then INHERITED from the declaration's own parts, which is only sound when every
    # part shares one unit — a mixed declaration never gets an invented unit.
    bare_total: float | None = None
    if total_token is None and len(units) == 1:
        bm = BARE_TOTAL_RE.search(text[tokens[-1]["end"]:])
        if bm:
            parsed_bare = _norm_value(bm.group(1))
            if parsed_bare is not None:
                bare_total = float(parsed_bare)
    declared_total = None
    total_read: str | None = None
    total_discrepancy = False
    if total_token is not None:
        reported = total_token["value"] * _TO_BASE[total_token["unit"]][1] / _TO_BASE[out_unit][1]
        declared_total = (_fmt_num(reported), out_unit)
        total_read = _fmt_num(reported)
    elif bare_total is not None:
        declared_total = (_fmt_num(bare_total), out_unit)
        total_read = _fmt_num(bare_total)
        # The printed total governs the declaration when it agrees with the printed parts. When
        # it does not — normally because OCR truncated or misread it — the total computed from
        # the fully read parts is reported and the disagreement is surfaced for review, rather
        # than publishing a number the package cannot actually be stating.
        if abs(bare_total - reported) > 0.05 + 0.01 * reported:
            total_discrepancy = True
    extra_value = None
    if extras:
        extra_base = sum(t["value"] * _TO_BASE[t["unit"]][1] for t in extras)
        extra_value = (_fmt_num(extra_base / _TO_BASE[out_unit][1]), out_unit)

    notes: list[str] = []
    if pack_note:
        notes.append(pack_note)
    notes.append(
        "composite declaration combined "
        + ("from the stated total" if declared_total else "arithmetically from its printed parts")
    )
    if extra_value:
        notes.append(f"promotional extra of {extra_value[0]} {extra_value[1]} recorded")
    if total_discrepancy:
        notes.append(
            f"printed total '{total_read}' {out_unit} does not match the printed parts "
            f"(which combine to {_fmt_num(reported)} {out_unit}) — the printed total appears "
            "truncated or misread; the combined value is reported for review"
        )
    return {
        "combined": (_fmt_num(reported), out_unit, qtype),
        "base": (_fmt_num(parts[0]["value"] * _TO_BASE[parts[0]["unit"]][1] / _TO_BASE[out_unit][1]), out_unit),
        "extra": extra_value,
        "declared_total": declared_total,
        "total_discrepancy": total_discrepancy,
        "pack_count": pack_count,
        "unit_value": (pack_unit_value, out_unit) if pack_unit_value else None,
        "uncombined": [],
        "expression": text.strip(),
        "note": "; ".join(notes),
    }


def extract_net_quantity(lines: list[OcrLine], flags: list[bool] | None = None) -> list[Candidate]:
    """Extract net-quantity candidates. Nutrition-flagged lines are excluded."""
    lines = lines_sorted(lines)
    fl = flags if flags is not None else [False] * len(lines)
    id_value_flags = identifier_value_line_flags(lines, fl)
    candidates: list[Candidate] = []
    for idx, line in enumerate(lines):
        text = line.text.strip()
        if not text or fl[idx]:
            continue
        # OCR-error-tolerant token scan: real packages print 'i500g' / ':5009' / '5OO g'.
        # Try the raw line first (authoritative), then a numerically-corrected VIEW for
        # token discovery only — the candidate keeps the raw text and records the fix.
        correction_note = ""
        qm = QUANTITY_RE.search(text)
        if qm is None:
            # A token sitting in the VALUE column of a batch/lot label is an identifier
            # ('A0137X'), not a quantity. Recovering a quantity there means rewriting a
            # letter into a digit and reading the code as litres — an invented measurement.
            if id_value_flags[idx]:
                continue
            from backend.ocr.textnorm import normalize_numeric_text

            # A batch/lot line declares an identifier, not a quantity: correcting its letters into
            # digits ('A0137X' → '40137X') would invent a litre reading out of a part code.
            corrected, _corrs = normalize_numeric_text(
                text, identifier_context=bool(IDENTIFIER_ANCHOR_RE.search(text))
            )
            if corrected != text:
                qm = QUANTITY_RE.search(corrected)
                if qm is not None:
                    correction_note = f"numeric OCR errors corrected for token discovery (raw preserved: '{text}')"
        if not qm:
            continue
        unit_raw = qm.group(2).lower().rstrip(".")
        unit_info = UNIT_MAP.get(unit_raw)
        if unit_info is None:
            continue
        # A SINGLE-LETTER unit ('m', 'l') adjacent to an abbreviation dot, or carrying a
        # PIN-code-shaped value, is a misread abbreviation — not a measurement. '561203.M.LNo.'
        # is a PIN code followed by a licence number, and it was previously published as
        # '561203 m'. Unambiguous units ('kg', 'ml', 'nos') are unaffected by this guard.
        if unit_raw in ("m", "l"):
            if re.match(r"\.\s*[A-Z]", text[qm.end() :]):
                continue
            if re.fullmatch(r"[1-9]\d{5}", qm.group(1).replace(".", "").strip()):
                continue  # 6-digit PIN shape is never a metre/litre figure on a label
        # Guard against fused OCR digits: '5009' is NOT '500 g' — the unit must be a real
        # token in the (corrected) text, not digits fused with a lost unit letter.
        value = _norm_value(qm.group(1))
        if value is None:
            continue
        if correction_note:
            corrected_unit = qm.group(2).lower().rstrip(".")
            if corrected_unit not in text.lower():
                # The unit exists ONLY in the corrected view — the correction invented it. A
                # quantity that needs an invented unit is a guess, so it is not offered.
                continue
        if qm.group(1).isdigit() and len(qm.group(1).replace(".", "")) >= 4 and (
            f"{qm.group(1)}" in text and not any(
                u in text.lower() for u in ("g", "kg", "ml", " l", "nos", "pcs")
            )
 ):
            # 4+ digit value with no unit token anywhere in the raw line: 'Net Weight :5009'
            # is ambiguous (could be 500 g, 500 ml or a code) — never guess.
            continue
        has_net = bool(NET_KEYWORD_RE.search(text))
        # general context bonus if a NET keyword appears on a neighboring line
        context = 0.85 if has_net else 0.4
        if not has_net and idx > 0 and NET_KEYWORD_RE.search(lines[idx - 1].text):
            context = 0.7
        if not has_net and idx + 1 < len(lines) and NET_KEYWORD_RE.search(lines[idx + 1].text):
            context = 0.7
        strength = 0.95 if has_net else 0.55

        # Composite declarations (promotional extras / multi-packs / stated totals) are parsed as a
        # whole so the reported quantity matches the printed declaration instead of its first number.
        composite = parse_quantity_expression(text)
        if composite is None and correction_note:
            composite = parse_quantity_expression(corrected)
        composite_note = ""
        composite_inferred = False
        declared_total_key = None
        extra_key = None
        if composite is not None:
            composite_note = composite["note"]
            if composite["combined"] is None or composite.get("total_discrepancy"):
                # Mixed measurement families, or a printed total that disagrees with the printed
                # parts: report the combined reading and flag it for human review.
                composite_inferred = True
            if composite["declared_total"] is not None:
                declared_total_key = composite["declared_total"]
            if composite["extra"] is not None:
                extra_key = composite["extra"]
            strength = max(strength, 0.9 if has_net else 0.7)
        conf = compose_confidence(line.confidence, strength, context)
        if composite is None:
            unit, qtype = unit_info
        elif composite["combined"] is not None:
            value, unit, qtype = composite["combined"]
        else:
            value, unit, qtype = composite["base"][0], composite["base"][1], unit_info[1]

        payload: dict = {"value": value, "unit": unit, "quantity_type": qtype}
        if composite is not None:
            payload["base_value"] = composite["base"][0]
            if composite["pack_count"] is not None:
                payload["pack_count"] = composite["pack_count"]
                payload["unit_value"] = composite["unit_value"][0]
            if extra_key is not None:
                payload["extra_value"] = extra_key[0]
            if declared_total_key is not None:
                payload["declared_total"] = declared_total_key[0]
            payload["expression"] = composite["expression"]
            if composite["combined"] is None:
                payload["parts_uncombined"] = True
            if composite.get("total_discrepancy"):
                payload["total_discrepancy"] = True
        normalized = json.dumps(payload)
        display = f"{value} {unit}"
        candidates.append(
            Candidate(
                field_name="net_quantity",
                value=normalized,
                raw_value=text,
                confidence=round(conf, 3),
                score=conf + (0.25 if has_net else 0.0),
                engine=line.engine,
                variant=line.variant,
                bbox=line.bbox,
                source_text=text,
                inferred=composite_inferred,
                reason=(
                    f"quantity pattern with recognized unit '{unit}'"
                    + ("; 'Net' keyword on same line" if has_net else "; no explicit Net keyword on line")
                    + (f"; {correction_note}" if correction_note else "")
                    + (f"; {composite_note}" if composite_note else "")
                ),
            )
        )
    # dedupe by normalized value+bbox
    seen: set[tuple[str, str]] = set()
    unique: list[Candidate] = []
    for c in candidates:
        key = (c.value, str(c.bbox))
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique
