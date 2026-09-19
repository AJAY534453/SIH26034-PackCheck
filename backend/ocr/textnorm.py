"""OCR error-tolerance layer: context-aware character normalization with provenance.

Rules:
- Raw OCR text is NEVER mutated at the source. Corrected forms are derived views that
  carry an explicit correction ledger (original → corrected, per token).
- Character confusions are corrected ONLY inside tokens that are numeric in context
  (prices, quantities, dates, codes, numbers with unit suffixes). Words are never touched.
- Every correction is auditable: the ledger records the exact original token, the
  corrected token, and each character change.

Common confusions: O↔0, I/l↔1, S↔5, B↔8, G↔6, Z↔2.

Examples:
  "MRP ₹2OO"      → "MRP ₹200"       (₹2OO corrected)
  "Batch No: 8O13O" → "Batch No: 80130"  (word "No" untouched)
  "Net Wt. 5OOg"  → "Net Wt. 500g"   (unit suffix g preserved)
  "BEST BEFORE 12 MONTNS" → unchanged (MONTNS is a word, not numeric-context)
"""
from __future__ import annotations

import re

# Letter chars that OCR frequently substitutes inside numeric tokens.
AMBIGUOUS_CHARS = "OoIlSBGZ"
AMBIGUOUS_MAP = {"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "B": "8", "G": "6", "Z": "2"}

# Unit suffixes that may follow a number (stripped before the numeric-context analysis).
_UNIT_SUFFIX_RE = re.compile(
    r"(kg|kgs|kilograms?|gm|gms|grams?|g|ml|millilit(?:e|er)s?|ltr|litres?|liters?|l|"
    r"nos?\.?|no\.?|pcs?|pieces?|units?|months?|years?|days?|weeks?)$",
    re.IGNORECASE,
)
_CURRENCY_PREFIX_RE = re.compile(r"^(?:₹|rs\.?|inr)\s*", re.IGNORECASE)

# Words that must never be corrected (they are real words even though letters overlap).
PROTECTED_WORDS = {"rs", "inr", "no", "nos", "mrp", "fssai", "batch", "lot", "net", "mfg", "mfd", "exp", "pkd"}


def is_numeric_context_token(token: str) -> bool:
    """True when a token is numeric-in-context and safe to apply letter→digit corrections.

    A token qualifies when, after removing currency prefixes and unit suffixes, every
    remaining character is a digit, a separator (., space), or an ambiguous letter
    (O/I/l/S/B/G/Z) — and it contains at least one digit or ambiguous letter.
    Real words fail because they contain letters outside the ambiguous set.

    Unit-terminated tokens ('500g', '250ml', '12months') are quantities by construction —
    a real word never ends in a bare quantity unit — so ONE leading OCR-noise letter
    before the numeric core is tolerated (e.g. 'i5o0g') provided the core contains a real
    digit. The leading character is DROPPED (never mapped to a digit) during correction:
    mapping 'i'→1 would fabricate 1500 from 500.
    """
    core = token.strip().strip(" :.,;()[]")
    if not core:
        return False
    word = re.sub(r"[^A-Za-z]", "", core).lower()
    if word in PROTECTED_WORDS:
        return False
    unit_terminated = bool(_UNIT_SUFFIX_RE.search(core))
    # strip currency prefix and unit suffix, then analyze the numeric core
    core = _CURRENCY_PREFIX_RE.sub("", core)
    core = _UNIT_SUFFIX_RE.sub("", core).strip(" .,")
    if not core:
        return False
    has_digit = any(ch.isdigit() for ch in core)
    has_ambiguous = any(ch in AMBIGUOUS_CHARS for ch in core)
    if not (has_digit or has_ambiguous):
        return False
    # A unit-terminated token WITHOUT a real digit is a word ending in a unit letter
    # ('Blog' → strips to 'Blo', all ambiguous letters) — never a quantity. Quantities
    # always carry at least one real digit; requiring it here closes that hole.
    if unit_terminated and not has_digit:
        return False
    leading_noise_allowed = unit_terminated and has_digit and len(core) > 1
    skipped_leading = False
    for k, ch in enumerate(core):
        if ch.isdigit() or ch in AMBIGUOUS_CHARS or ch in ".,/- ":
            continue
        if ch.isalpha() and leading_noise_allowed and k == 0 and not skipped_leading:
            skipped_leading = True
            continue
        if ch.isalpha():
            return False  # a real letter outside the ambiguous set → a word, not a number
        # other punctuation terminates the numeric core (e.g. ':' in 'No:')
        return False
    return True


def correct_numeric_token(token: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (corrected_token, [(orig_char, new_char), ...]). Non-ambiguous chars unchanged.

    A single leading OCR-noise letter in a unit-terminated numeric token is DROPPED
    (recorded as (char, "")), never mapped to a digit — dropping preserves the honest
    digit count; mapping would fabricate a different value (e.g. 'i5o0g' → '500g',
    not '1500g').
    """
    corrections: list[tuple[str, str]] = []
    out: list[str] = []
    dropped_leading = False
    core = token
    # leading-noise drop decision mirrors is_numeric_context_token's tolerance
    stripped = token.strip().strip(" :.,;()[]")
    unit_terminated = bool(_UNIT_SUFFIX_RE.search(stripped))
    core2 = _UNIT_SUFFIX_RE.sub("", _CURRENCY_PREFIX_RE.sub("", stripped)).strip(" .,")
    if (
        unit_terminated
        and core2
        and core2[0].isalpha()
        and core2[0] not in AMBIGUOUS_CHARS
        and any(ch.isdigit() for ch in core2[1:])
    ):
        corrections.append((core2[0], ""))
        core = token.replace(core2[0], "", 1)
        dropped_leading = True
    for ch in core:
        if ch in AMBIGUOUS_CHARS:
            fixed = AMBIGUOUS_MAP[ch]
            corrections.append((ch, fixed))
            out.append(fixed)
        else:
            out.append(ch)
    if not dropped_leading and not corrections:
        return token, corrections
    return "".join(out), corrections


def normalize_numeric_text(text: str, identifier_context: bool = False) -> tuple[str, list[dict]]:
    """Derive a numeric-corrected view of `text` with a correction ledger.

    Token-based: text is split on whitespace; each token is independently evaluated.
    Words like 'MRP', 'Net', 'No' pass through untouched. Returns
    (corrected_text, corrections) where corrections is a list of
    {token_original, token_corrected, changes: [(orig_char, new_char), ...]}.

    `identifier_context` marks a line whose declaration is an alphanumeric IDENTIFIER (a
    batch/lot/part code). Such codes legitimately mix letters and digits, so a correction that
    leaves a real letter standing ('A0137X' → '40137X') is speculation about a printed code,
    not a numeric repair, and is skipped. Pure-numeric repairs ('8O13O' → '80130') still apply.
    """
    corrections: list[dict] = []
    parts = re.split(r"(\s+)", text)
    for i, part in enumerate(parts):
        if not part or part.isspace():
            continue
        core = part.strip(" :.,;()[]")
        if not core or not is_numeric_context_token(core):
            continue
        fixed, changes = correct_numeric_token(core)
        if not changes:
            continue
        if identifier_context and any(ch.isalpha() and ch not in AMBIGUOUS_CHARS for ch in fixed):
            # The token was only "numeric" because its trailing letter was read as a UNIT ('A0137X'
            # looks like 807269 litres). Correcting the remaining letters would then rewrite a
            # printed part code, so the raw reading stands. When no unit reading is involved
            # ('AO13O' → 'A0130') the correction is a genuine numeric repair and still applies.
            if _UNIT_SUFFIX_RE.search(core):
                continue
        parts[i] = part.replace(core, fixed, 1)
        corrections.append({"token_original": core, "token_corrected": fixed, "changes": changes})
    return "".join(parts), corrections


def extract_number(corrected_text: str) -> str | None:
    """Best-effort numeric value from corrected text ('₹200.00' → '200.00'); None if absent."""
    m = re.search(r"(\d[\d,]*(?:\.\d{1,2})?)", corrected_text)
    if not m:
        return None
    return m.group(1).replace(",", "")


def explain_correction(corrections: list[dict]) -> str:
    """Human-readable correction explanation for evidence/audit display."""
    if not corrections:
        return ""
    parts = []
    for c in corrections:
        changes = ", ".join(f"'{o}'→'{n}'" for o, n in c["changes"])
        parts.append(f"'{c['token_original']}' read as '{c['token_corrected']}' ({changes})")
    return "OCR character correction: " + "; ".join(parts)
