"""Extraction foundation: candidates, line utilities, region classification.

Core invariants:
- A candidate is an OBSERVATION, never a legal conclusion.
- Confidence is composed from OCR confidence + pattern strength + context — never invented.
- Nutrition-table regions are identified so numeric table rows never leak into other fields.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from backend.ocr.base import OcrLine

# ---------- Candidate ----------


@dataclass
class Candidate:
    field_name: str
    value: str  # normalized value (string form; JSON where structured)
    raw_value: str
    confidence: float  # 0..1
    score: float  # ranking score (can exceed confidence slightly; used for winner selection)
    engine: str = ""
    variant: str = "original"
    bbox: tuple[int, int, int, int] | None = None
    source_image_id: int | None = None
    source_text: str = ""
    reason: str = ""
    # True when the value was inferred from layout/typography rather than read from an explicit
    # declaration label (e.g. a brand mark read off a logo, or a title split into brand+product).
    # Inferred values are offered for human review; they are never presented as detected facts.
    inferred: bool = False
    # True when the DECLARATION ROLE is not established by the package text itself (e.g. a
    # company entity printed with no 'Manufactured by' / 'Marketed by' anchor). The value may be
    # read perfectly and still be offered for review, because which legal role it fills is a
    # judgement for the inspector — never a silent assignment by the extractor.
    role_uncertain: bool = False


# ---------- Line helpers ----------


def line_y_center(line: OcrLine) -> float:
    return (line.bbox[1] + line.bbox[3]) / 2.0


def line_height(line: OcrLine) -> float:
    return max(1.0, line.bbox[3] - line.bbox[1])


def lines_sorted(lines: list[OcrLine]) -> list[OcrLine]:
    return sorted(lines, key=lambda l: (round(line_y_center(l) / 12), l.bbox[0]))


def adjacent_lines(lines: list[OcrLine], idx: int, max_gap_factor: float = 1.6) -> list[int]:
    """Indexes of lines vertically adjacent to lines[idx] (below or above, tight gap)."""
    base = lines[idx]
    out: list[int] = []
    for j, other in enumerate(lines):
        if j == idx:
            continue
        gap = abs(line_y_center(other) - line_y_center(base))
        if gap <= max_gap_factor * max(line_height(base), line_height(other)):
            out.append(j)
    return sorted(out, key=lambda j: abs(line_y_center(lines[j]) - line_y_center(base)))


def following_line(lines: list[OcrLine], idx: int) -> int | None:
    """The nearest line directly below lines[idx] (same column-ish), or None."""
    base = lines[idx]
    best: int | None = None
    best_gap = None
    for j, other in enumerate(lines):
        if j == idx:
            continue
        if line_y_center(other) <= line_y_center(base):
            continue
        h_overlap = min(base.bbox[2], other.bbox[2]) - max(base.bbox[0], other.bbox[0])
        if h_overlap <= 0:
            continue
        gap = line_y_center(other) - line_y_center(base)
        if best_gap is None or gap < best_gap:
            best, best_gap = j, gap
    return best


# ---------- Company-entity recognition ----------
#
# A company/entity line (the manufacturer, packer, marketer or importer name) must never be
# reported as the product name, and must be recognised wherever it appears even when no role
# anchor ("Manufactured by", "Marketed by", ...) was read. Real OCR routinely fuses company
# words together — 'ACME INDUSTRIESLTD.', 'ABC FOODSPVTLTD', 'XYZ PRIVATELIMITED' — so a
# \b-anchored suffix test misses exactly the lines that matter. The suffix/word tests below run
# against a SQUASHED (letters+digits only) view of the line, which recovers those fusions while
# staying safe: only legal-entity suffixes and unambiguous company words are used, and a
# squashed tail like 'WALTDISNEY' does not end in any of them.
_ENTITY_TAIL_SUFFIXES = (
    "LTD", "LIMITED", "PVT", "PVTLTD", "PRIVATE", "PRIVATELIMITED", "LLP", "LLC", "PLC",
    "INCORPORATED", "CORPORATION", "CORP", "COMPANY", "ENTERPRISES", "INDUSTRIES",
    "HOLDINGS", "GMBH", "PTE", "SDNBHD",
)
_ENTITY_STRONG_WORDS = (
    "INDUSTRIES", "ENTERPRISES", "CORPORATION", "PRIVATELIMITED", "PVTLTD",
    "MANUFACTURERS", "PACKERS", "IMPORTERS", "DISTRIBUTORS",
)
COMPANY_SUFFIX_WORD_RE = re.compile(
    r"\b(?:pvt|private|ltd|limited|llp|llc|plc|inc|incorporated|corp|corporation|company|"
    r"industries|enterprises|holdings|gmbh|manufacturers|packers|importers|distributors)\b\.?",
    re.IGNORECASE,
)


def squashed(text: str) -> str:
    """Letters+digits only, uppercased — one view for fused-OCR tolerant matching."""
    return re.sub(r"[^A-Za-z0-9]", "", text or "").upper()


def looks_like_company_entity(text: str) -> bool:
    """Does this line carry a company/legal-entity name (fused-OCR tolerant)?"""
    t = (text or "").strip()
    if len(t) < 4:
        return False
    if COMPANY_SUFFIX_WORD_RE.search(t):
        return True
    s = squashed(t)
    if len(s) < 5:
        return False
    if any(s.endswith(suffix) for suffix in _ENTITY_TAIL_SUFFIXES):
        return True
    return any(word in s for word in _ENTITY_STRONG_WORDS)


# ---------- Value-column geometry ----------
#
# Package declaration blocks are printed as two columns: a LABEL column ('PKD.', 'USE BY',
# 'LOT No.', 'Net Wt.') and a VALUE column beside it. OCR row offsets between the two columns
# are routinely larger than one line height on small/dot-matrix print, so 'same row' overlap
# tests alone lose the value. These helpers express the weaker, still-safe relation: a line in
# the anchor's value column within a few row heights of it.


def value_column_neighbours(
    lines: list[OcrLine], idx: int, max_row_heights: float = 2.2
) -> list[int]:
    """Indexes of lines sitting in lines[idx]'s value column within max_row_heights rows.

    A candidate must be horizontally beside the anchor (to its right, or overlapping it) and
    vertically within the row band of the anchor. Purely geometric — no field knowledge.
    """
    anchor = lines[idx]
    ax0, ay0, ax1, ay1 = anchor.bbox
    ah = max(1, ay1 - ay0)
    acy = (ay0 + ay1) / 2
    out: list[int] = []
    for j, other in enumerate(lines):
        if j == idx:
            continue
        bx0, by0, bx1, by1 = other.bbox
        if not (other.text or "").strip():
            continue
        bcy = (by0 + by1) / 2
        if abs(bcy - acy) > max_row_heights * max(ah, max(1, by1 - by0)):
            continue
        if bx0 >= ax1 - 2 or (min(ax1, bx1) - max(ax0, bx0)) > 0:
            out.append(j)
    out.sort(key=lambda j: abs((lines[j].bbox[1] + lines[j].bbox[3]) / 2 - acy))
    return out


def identifier_value_line_flags(lines: list[OcrLine], fl: list[bool] | None = None) -> list[bool]:
    """Mark lines that occupy the VALUE position of a batch/lot/serial identifier anchor.

    Used to keep identifier codes out of unrelated fields: a token read as the value of a
    'LOT No.' label is an identifier, never a quantity, volume, date or price.
    """
    flags = fl if fl is not None else [False] * len(lines)
    out = [False] * len(lines)
    for i, line in enumerate(lines):
        if flags[i] or not line.text.strip():
            continue
        if not IDENTIFIER_ANCHOR_RE.search(line.text):
            continue
        for j in value_column_neighbours(lines, i):
            out[j] = True
    return out


# ---------- Nutrition region exclusion ----------

NUTRITION_HEADER_RE = re.compile(
    r"nutrition(?:al)?\s*(?:information|facts|table)?|per\s*(?:100\s*g|100\s*ml|serving|scoop)",
    re.IGNORECASE,
)
NUTRITION_ROW_RE = re.compile(
    r"\b(?:energy|protein|carbohydrate|total\s*sugar|sugars?|fat|saturated\s*fat|trans\s*fat|"
    r"cholesterol|sodium|dietary\s*fib(?:re|er)|fibre|fiber|calcium|iron|vitamin|kcal|kJ)\b|"
    r"g/\s*\d{1,3}\s*g|g/100\s*(?:g|ml)",
    re.IGNORECASE,
)
NUMERIC_HEAVY_RE = re.compile(r"^(?:[\d.,\s%/]+|[\d.,]+\s*(?:g|mg|kg|ml|kcal|kJ|iu)%?)$", re.IGNORECASE)


def nutrition_line_flags(lines: list[OcrLine]) -> list[bool]:
    """Flag lines that belong to a nutrition table (header, keyword rows, numeric-only rows near them)."""
    flags = [bool(NUTRITION_HEADER_RE.search(l.text) or NUTRITION_ROW_RE.search(l.text)) for l in lines]
    # Numeric-heavy lines sitting between nutrition rows are table remnants (e.g. "0.90", "89.97", "g/100g").
    flagged_idx = [i for i, f in enumerate(flags) if f]
    if flagged_idx:
        lo, hi = min(flagged_idx), max(flagged_idx)
        for i in range(lo, hi + 1):
            if not flags[i] and (NUMERIC_HEAVY_RE.match(lines[i].text.strip()) or NUTRITION_ROW_RE.search(lines[i].text)):
                flags[i] = True
    return flags


def is_nutrition_line(lines: list[OcrLine], idx: int, flags: list[bool] | None = None) -> bool:
    fl = flags if flags is not None else nutrition_line_flags(lines)
    return fl[idx]


# A line whose declaration is an alphanumeric IDENTIFIER rather than a number. Inside such a
# line, numeric OCR correction that leaves a real letter standing ('A0137X' → '40137X') is
# speculation about a printed code, not a numeric repair — the raw reading is authoritative.
IDENTIFIER_ANCHOR_RE = re.compile(
    r"\b(?:batch|lot|b\.?\s*no|batch\s*code|serial|part\s*no)\b", re.IGNORECASE
)


# ---------- Negative patterns (shared false-positive guards) ----------

PHONE_RE = re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{9}\b|\b1800[\s-]?\d{3}[\s-]?\d{3,4}\b")

PIN_RE = re.compile(r"\b[1-9]\d{5}\b")
FSSAI_RE = re.compile(r"\b1\d{13}\b")
DATEISH_RE = re.compile(r"\b\d{1,4}[/.\-]\d{1,4}(?:[/.\-]\d{2,4})?\b")
UNIT_PRICE_RE = re.compile(
    r"(?:₹|rs\.?|inr)\s*[\d.,]+\s*(?:/|per\s)\s*(?:g|kg|gm|gram|grams|ml|l|litre|liter|nos?|no|pcs|unit|100\s*g|100\s*ml)\b",
    re.IGNORECASE,
)


def looks_like_phone(text: str) -> bool:
    return bool(PHONE_RE.search(text)) and not UNIT_PRICE_RE.search(text)


# ---------- Confidence composition (honest, never fabricated) ----------


def compose_confidence(ocr_conf: float, pattern_strength: float, context: float) -> float:
    """Compose field confidence from evidence quality components, each 0..1."""
    ocr = max(0.0, min(1.0, ocr_conf))
    p = max(0.0, min(1.0, pattern_strength))
    c = max(0.0, min(1.0, context))
    return round(max(0.0, min(1.0, 0.5 * ocr + 0.3 * p + 0.2 * c)), 3)
