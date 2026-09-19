"""Date extraction: context anchors (MFD/PKD/EXP/USE BY/BEST BEFORE) + format grammar.

Never convert arbitrary numbers to dates. Ambiguity => flagged AMBIGUOUS (manual review),
never silently guessed. Full dates (DD/MM/YY) take precedence over bare MM/YYYY.

Value association is SPATIAL, because OCR routinely splits a declaration into a label line and a
separate value line ('Mfg.' / '04/02/25'). A date is only ever accepted from the anchor's own
line, the same visual row to its right, or the next row below in the same column — and never from
a barcode/EAN/GS1 long-numeric run.
"""
from __future__ import annotations

import datetime as _dt
import json
import re

from backend.extraction.base import Candidate, compose_confidence, lines_sorted
from backend.ocr.base import OcrLine

CONTEXT_RE = re.compile(
    r"\b(?:mfd|mfg|mfged|manufactured|manufacturing\s*date|mfg\.?\s*date|pkd|packed|packaging\s*date|"
    r"pre[-\s]?pack(?:ed|ing)?|exp|expiry|use\s*by|use\s*before|best\s*before)\b\.?\s*[:\-]?",
    re.IGNORECASE,
)
FULL_DATE_RE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")
MONTH_YEAR_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{2,4})\s*$")  # only when nothing follows (no day part)
MONTH_NAME_RE = re.compile(r"\b([A-Za-z]{3,9})\s*[- ]?\s*(\d{4})\b")
PERIOD_RE = re.compile(r"\b(\d{1,3})\s*(month|months|day|days|year|years|week|weeks)\b", re.IGNORECASE)
# DURATION references: '... 12 months from (the date of) manufacturing/packing/packaging'
DURATION_REF_RE = re.compile(
    r"\bfrom\b[^A-Za-z0-9]*(?:the\s*)?(?:date\s*of\s*)?(manufactur\w*|mfg\w*|mfd|pack\w*|pre[-\s]?pack\w*|import\w*)\b",
    re.IGNORECASE,
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "junе": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

DTYPE_MAP = {
    "manufacturing date": "MANUFACTURING", "mfg. date": "MANUFACTURING", "mfg date": "MANUFACTURING",
    "mfd": "MANUFACTURING",
    "mfg": "MANUFACTURING", "manufactured": "MANUFACTURING", "manufactured on": "MANUFACTURING",
    "date of manufacture": "MANUFACTURING", "date of mfg": "MANUFACTURING",
    "production date": "MANUFACTURING", "mfged": "MANUFACTURING",
    "packaging date": "PACKING", "pre-pack": "PACKING", "pre pack": "PACKING", "pkd": "PACKING",
    "packed": "PACKING", "packing date": "PACKING", "date of packing": "PACKING",
    "packed on": "PACKING", "pkd date": "PACKING", "pack date": "PACKING",
    "expiry": "EXPIRY", "exp": "EXPIRY", "use by": "EXPIRY", "use before": "EXPIRY",
    "consume before": "EXPIRY", "expiry date": "EXPIRY", "exp date": "EXPIRY",
    "best before": "BEST_BEFORE", "best before end": "BEST_BEFORE",
    "b.b": "BEST_BEFORE", "bb": "BEST_BEFORE", "best bf": "BEST_BEFORE",
    "import date": "IMPORT", "date of import": "IMPORT", "imported on": "IMPORT",
    "imported": "IMPORT", "imp date": "IMPORT",
}

# OCR-confusable spellings of the short abbreviation anchors. Applied ONLY to a normalized VIEW
# of the line for anchor detection — the stored raw text is never altered. `Mig.`/`M1g` for
# `Mfg`, `B.B.` for `bb`, etc. are routine on dot-matrix/inkjet prints.
_CONFUSABLE_ANCHORS = (
    (re.compile(r"\bmig\b"), "mfg"),
    (re.compile(r"\bm1g\b"), "mfg"),
    (re.compile(r"\bmf9\b"), "mfg"),
    (re.compile(r"\bmid\b"), "mfg"),
    (re.compile(r"\bmfq\b"), "mfg"),
    (re.compile(r"\bmfg\b"), "mfg"),
    (re.compile(r"\bmfged\b"), "mfged"),
)

# Another declaration starting between the anchor and a candidate value line ends the search for
# that value. Without this boundary, a value was pulled from a line belonging to a DIFFERENT
# declaration a hundred pixels below (an 'Mfd. by' anchor took the date printed on the
# 'Mfg. & Use Before' row beneath it, and the manufacturing date came out as the use-before date).
_DECLARATION_BOUNDARY_RE = re.compile(
    r"\b(?:m\.?r\.?p\.?|mrp|net\s*(?:qty|quantity|wt\.?|weight|volume|content)|batch|lot\b|"
    r"fssai|lic(?:ence|ense)\s*no|consumer\s*care|customer\s*care|helpline|e-?mail|www\.|"
    r"pack(?:ed|ing)?|pkg|mfg|mfd|mfged|manufactur\w*|pkd|exp(?:iry)?\b|best\s*before|"
    r"use\s*before|use\s*by|unit\s+sale\s+price|unit\s+price)\b",
    re.IGNORECASE,
)

# A bare run of >=13 digits is a barcode / EAN / GS1 / long article code — never a date.
LONG_NUMERIC_RE = re.compile(r"\d{13,}")
# Separator-less date code: DDMMYY or DDMMYYYY (Indian DD/MM convention).
DATE_CODE_RE = re.compile(r"(?<!\d)(\d{6}|\d{8})(?!\d)")


def _anchor_view(text: str) -> str:
    """A lowercase, dotted-abbreviation-collapsed, confusable-corrected VIEW of a line used only
    for anchor matching. Never stored, never used as a value. Dots between single letters are
    collapsed so `M.F.G.` / `M. F. D` / `B.B.` match the same anchors as `MFG` / `MFD` / `BB`.
    """
    t = text.lower()
    t = re.sub(r"\b([a-z])\.\s*(?=[a-z]\.)", r"\1", t)  # M.F.G. -> MFG.
    t = re.sub(r"\b([a-z])\.\s*(?=[a-z]\b)", r"\1", t)  # B.B -> BB
    for rx, repl in _CONFUSABLE_ANCHORS:
        t = rx.sub(repl, t)
    return t


def _detect_dtype(text: str) -> tuple[str, str] | None:
    t = _anchor_view(text)
    for key in sorted(DTYPE_MAP, key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", t):
            return DTYPE_MAP[key], key
    return None


def _try_full_date(d: str, m: str, y: str) -> tuple[str, bool] | None:
    """Parse DD/MM/YY[YY] with Indian DD/MM convention. Returns (iso, ambiguous) or None."""
    try:
        dd, mm, yy = int(d), int(m), int(y)
    except ValueError:
        return None
    if yy < 100:
        yy += 2000
    ambiguous = dd <= 12 and mm <= 12 and dd != mm  # both orders valid -> flag for review
    if mm > 12 and dd <= 12:  # clearly MM/DD — still show but flag
        dd, mm = mm, dd
        ambiguous = True
    try:
        _dt.date(yy, mm, dd)
    except ValueError:
        return None
    return f"{yy:04d}-{mm:02d}-{dd:02d}", ambiguous


def _try_date_code(code: str) -> tuple[str, bool] | None:
    """Parse a separator-less 6/8-digit inkjet date code as DDMMYY[YY].

    Returns (iso, ambiguous) or None. Both day-first and month-first readings are usually valid
    for a DDMMYY code, so the result is always flagged AMBIGUOUS — it is offered as a candidate
    for review, never silently committed as fact.
    """
    if len(code) == 6:
        dd, mm, yy = int(code[0:2]), int(code[2:4]), int(code[4:6]) + 2000
    elif len(code) == 8:
        dd, mm, yy = int(code[0:2]), int(code[2:4]), int(code[4:8])
    else:
        return None
    if not (1 <= dd <= 31 and 1 <= mm <= 12 and 2000 <= yy <= 2100):
        return None
    try:
        _dt.date(yy, mm, dd)
    except ValueError:
        return None
    return f"{yy:04d}-{mm:02d}-{dd:02d}", True


def _row_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Vertical overlap of two bboxes as a fraction of the shorter line's height."""
    inter = min(a[3], b[3]) - max(a[1], b[1])
    if inter <= 0:
        return 0.0
    return inter / max(1, min(a[3] - a[1], b[3] - b[1]))


def _col_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Horizontal overlap of two bboxes as a fraction of the narrower line's width."""
    inter = min(a[2], b[2]) - max(a[0], b[0])
    if inter <= 0:
        return 0.0
    return inter / max(1, min(a[2] - a[0], b[2] - b[0]))


def _union(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


# A reference date (when the commodity was made/packed) can never post-date the use-by/expiry
# date printed on the same pack. That is a property of the declaration block itself, and it is
# the disambiguator when two columns of dates are printed side by side.
_REFERENCE_DTYPES = ("MANUFACTURING", "PACKING", "IMPORT")
_DEADLINE_DTYPES = ("EXPIRY", "BEST_BEFORE")


#: A bare MM/YY[YY] value. MONTH_YEAR_RE above only accepts one at the very END of a line (so a
#: net-quantity fraction is not read as a date); inside a multi-declaration block the value can sit
#: mid-line, so the pairing pass below uses this bounded form instead.
_MONTH_YEAR_INLINE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*/\s*(\d{2,4})(?!\d)")


def _date_anchors_in(text: str) -> list[tuple[int, int, str]]:
    """Every date anchor on the line as (start, end, dtype), in printed order.

    The longest key wins at an overlapping position ('use before' over 'use'), which mirrors how
    every other anchor decision in this module is made.
    """
    view = _anchor_view(text)
    hits: list[tuple[int, int, str]] = []
    for key, dtype in DTYPE_MAP.items():
        for m in re.finditer(rf"\b{re.escape(key)}\b", view):
            hits.append((m.start(), m.end(), dtype))
    hits.sort(key=lambda h: (h[0], h[0] - h[1]))
    chosen: list[tuple[int, int, str]] = []
    for start, end, dtype in hits:
        if chosen and start < chosen[-1][1]:
            continue  # overlaps an already-chosen (longer) anchor
        chosen.append((start, end, dtype))
    return chosen


def _dates_in(text: str) -> list[tuple[int, int, str, str]]:
    """Printed date values on the line as (start, end, iso, ambiguity marker), in printed order."""
    found: list[tuple[int, int, str, str]] = []
    spans: list[tuple[int, int]] = []
    for m in FULL_DATE_RE.finditer(text):
        parsed = _try_full_date(m.group(1), m.group(2), m.group(3))
        if not parsed:
            continue
        iso, ambiguous = parsed
        found.append((m.start(), m.end(), iso, "AMBIGUOUS" if ambiguous else ""))
        spans.append((m.start(), m.end()))
    for m in _MONTH_YEAR_INLINE_RE.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue  # already consumed as part of a full date
        month = int(m.group(1))
        year_raw = m.group(2)
        if not 1 <= month <= 12:
            continue
        year = int(year_raw)
        year = year if len(year_raw) == 4 else (2000 + year if year <= 50 else 1900 + year)
        found.append((m.start(), m.end(), f"{year:04d}-{month:02d}", ""))
    found.sort(key=lambda f: f[0])
    return found


def _pair_anchors_with_dates(text: str) -> list[tuple[str, str, str, str]]:
    """Bind every date value on a multi-declaration line to ITS anchor, by printed order.

    Packs commonly print two declarations in one line:

        'Mfg. & Use Before: 04/26 & 03/28'
        'PKD. 04/26   USE BY 03/28'

    The line-level reader can only assign ONE declaration type per line, so such a line used to
    yield a single candidate — bound to whichever anchor happened to be longest, carrying whichever
    date happened to match — and the manufacturing date was then reported as the use-before date.

    Pairing rule (typographic, no product knowledge):
      * grouped shape (all anchors, then all values, equal counts) -> k-th anchor with k-th value;
      * interleaved shape -> each value with the nearest preceding anchor.
    Returns [] for any line that is not a multi-declaration line, leaving the existing single-anchor
    path untouched.
    """
    anchors = _date_anchors_in(text)
    dates = _dates_in(text)
    if len(anchors) < 2 or len(dates) < 2:
        return []
    pairing: list[tuple[tuple[int, int, str], tuple[int, int, str, str]]] = []
    grouped = len(anchors) == len(dates) and dates[0][0] > anchors[-1][1]
    if grouped:
        pairing = list(zip(anchors, dates))
    else:
        for value in dates:
            preceding = [a for a in anchors if a[0] < value[0]]
            if preceding:
                pairing.append((preceding[-1], value))
    out: list[tuple[str, str, str, str]] = []
    claimed: set[str] = set()
    for (_, _, dtype), (start, end, iso, ambiguous) in pairing:
        if dtype in claimed:
            # Two values for the same declaration type on one line (e.g. two dated rows). The first
            # printed one is kept and this repeat is dropped, never silently merged.
            continue
        claimed.add(dtype)
        out.append((dtype, iso + ambiguous, text[start:end], ambiguous))
    return out


def _parse_line_date(text: str) -> tuple[str, bool, str] | None:
    """Parse a printed calendar date out of a line (DD/MM/YY[YY] forms only)."""
    m = FULL_DATE_RE.search(text or "")
    if not m:
        return None
    parsed = _try_full_date(m.group(1), m.group(2), m.group(3))
    if not parsed:
        return None
    return parsed[0], parsed[1], m.group(0)


def _col_span(box: tuple[int, int, int, int]) -> tuple[int, int]:
    return box[0], box[2]


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return min(a[1], b[1]) - max(a[0], b[0]) > 0


def _reconcile_date_block(
    candidates: list[Candidate], lines: list[OcrLine], fl: list[bool]
) -> list[Candidate]:
    """Re-pair label anchors with date values inside one printed declaration block.

    Date blocks are printed as two columns (labels left, values right) and small dot-matrix print
    can put the two columns a whole line height out of step. Nearest-neighbour association can then
    pair a label with the value of the row beside it — e.g. 'PKD.' taking the date printed next to
    'USE BY'. Manufacturing/packing cannot post-date the use-by/expiry date of the same pack, so
    when the greedy pass produced an ordering the package cannot print (or left the use-by label
    unresolved while an earlier date value sits in the same block), the earliest value is given to
    the reference label and the latest to the deadline label, provided both sit inside the block.
    The change is recorded in the candidate's reason — evidence is never silently rewritten.
    """
    by_field: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_field.setdefault(c.field_name, []).append(c)

    refs = [f for f in by_field if f[5:].upper() in _REFERENCE_DTYPES]
    deadlines = [f for f in by_field if f[5:].upper() in _DEADLINE_DTYPES]
    if not refs or not deadlines:
        return candidates

    def _anchor_bbox(field: str) -> tuple[int, int, int, int] | None:
        for c in sorted(by_field[field], key=lambda c: -c.score):
            if c.bbox:
                return c.bbox
        return None

    boxes = {f: _anchor_bbox(f) for f in refs + deadlines}
    boxes = {f: b for f, b in boxes.items() if b}
    if not boxes:
        return candidates

    # Group anchors that share a column (same printed block).
    block_fields = [next(iter(boxes))]
    for f in boxes:
        if f in block_fields:
            continue
        if any(_spans_overlap(_col_span(boxes[f]), _col_span(boxes[g])) for g in block_fields):
            block_fields.append(f)
    if not (any(f[5:].upper() in _REFERENCE_DTYPES for f in block_fields)
            and any(f[5:].upper() in _DEADLINE_DTYPES for f in block_fields)):
        return candidates

    group_boxes = [boxes[f] for f in block_fields]
    g_top = min(b[1] for b in group_boxes)
    g_bottom = max(b[3] for b in group_boxes)
    g_left = min(b[0] for b in group_boxes)
    g_right = max(b[2] for b in group_boxes)
    heights = [max(1, b[3] - b[1]) for b in group_boxes]
    h = max(12, sum(heights) // len(heights))
    band_top, band_bottom = g_top - 2.5 * h, g_bottom + 2.5 * h

    # Values of the block: printed calendar dates in the value column beside the labels.
    pool: list[tuple[str, bool, OcrLine, str]] = []
    for j, line in enumerate(lines):
        if fl[j] or not line.text.strip():
            continue
        cy = (line.bbox[1] + line.bbox[3]) / 2
        if not (band_top <= cy <= band_bottom):
            continue
        # value column: beside the label column (to its right) or overlapping it
        if line.bbox[0] < g_left and not _spans_overlap(_col_span(line.bbox), (g_left, g_right)):
            continue
        parsed = _parse_line_date(line.text)
        if not parsed:
            continue
        iso, amb, matched = parsed
        pool.append((iso, amb, line, matched))
    if not pool:
        return candidates

    def _within(value_line: OcrLine, anchor_box: tuple[int, int, int, int]) -> bool:
        tolerance = max(3.0 * max(1, anchor_box[3] - anchor_box[1]), 90)
        acy = (anchor_box[1] + anchor_box[3]) / 2
        vcy = (value_line.bbox[1] + value_line.bbox[3]) / 2
        return abs(vcy - acy) <= tolerance and (
            value_line.bbox[0] >= anchor_box[2] - 2
            or _spans_overlap(_col_span(value_line.bbox), _col_span(anchor_box))
        )

    ref_field = next(f for f in block_fields if f[5:].upper() in _REFERENCE_DTYPES)
    deadline_field = next(f for f in block_fields if f[5:].upper() in _DEADLINE_DTYPES)
    if len([f for f in block_fields if f[5:].upper() in _REFERENCE_DTYPES]) != 1 or \
            len([f for f in block_fields if f[5:].upper() in _DEADLINE_DTYPES]) != 1:
        return candidates

    ref_box, deadline_box = boxes.get(ref_field), boxes.get(deadline_field)
    if ref_box is None or deadline_box is None:
        return candidates

    def _current(field: str) -> str | None:
        for c in sorted(by_field[field], key=lambda c: -c.score):
            if (c.value or "").strip():
                return c.value.replace("|AMBIGUOUS", "")
        return None

    ordered = sorted(pool, key=lambda item: item[0])
    earliest, latest = ordered[0], ordered[-1]
    if earliest[0] >= latest[0]:
        return candidates  # only one distinct value: nothing to re-pair
    if not (_within(earliest[2], ref_box) and _within(latest[2], deadline_box)):
        return candidates
    ref_now, deadline_now = _current(ref_field), _current(deadline_field)
    if ref_now == earliest[0] and deadline_now == latest[0]:
        return candidates  # already coherent
    if ref_now is None and deadline_now is None:
        return candidates  # the greedy pass resolved nothing: this is not a re-pairing
    if ref_now is not None and deadline_now is not None and ref_now <= deadline_now:
        return candidates  # both resolved and already ordered — respect the closer reading

    def _assign(field: str, entry: tuple[str, bool, OcrLine, str], superseded: str | None) -> None:
        iso, amb, line, matched = entry
        value = iso + ("|AMBIGUOUS" if amb else "")
        existing = sorted(by_field[field], key=lambda c: -c.score)
        anchor_text = (existing[0].raw_value.split("|")[0].strip() if existing else field)
        note = (
            f"'{anchor_text}' block: date value re-associated for chronological consistency with "
            "the other dated declaration in the same printed block (a packing/manufacturing date "
            "cannot post-date the use-by/expiry date of the same pack) — the label column and the "
            "value column are offset by more than a line height in this print"
        )
        if superseded:
            # The superseded reading is kept in the audit trail text: evidence is re-associated,
            # never silently rewritten or discarded.
            note += f"; nearest-neighbour reading '{superseded}' superseded"
        conf = compose_confidence(line.confidence, 0.85, 0.8)
        box = _union(existing[0].bbox, line.bbox) if existing and existing[0].bbox else line.bbox
        new = Candidate(
            field_name=field,
            value=value,
            raw_value=f"{anchor_text} | {matched}",
            confidence=round(conf, 3),
            score=conf + 0.3,
            engine=line.engine,
            variant=line.variant,
            bbox=box,
            source_text=f"{anchor_text} | {matched}",
            reason=note,
        )
        if not existing:
            new.raw_value = f"{field} | {matched}"
            new.source_text = new.raw_value
        reassigned.append(new)

    # Every previous candidate for the two re-paired labels is replaced: they were produced by
    # the unreliable nearest-neighbour reading this pass exists to correct. Leaving one behind
    # would let the superseded value win on score and raise a false conflict.
    reassigned: list[Candidate] = []
    _assign(ref_field, earliest, ref_now)
    _assign(deadline_field, latest, deadline_now)
    candidates[:] = [c for c in candidates if c.field_name not in (ref_field, deadline_field)]
    candidates.extend(reassigned)
    return candidates


def extract_dates(lines: list[OcrLine], flags: list[bool] | None = None) -> list[Candidate]:
    lines = lines_sorted(lines)
    fl = flags if flags is not None else [False] * len(lines)
    candidates: list[Candidate] = []

    for idx, line in enumerate(lines):
        text = line.text.strip()
        if not text or fl[idx]:
            continue
        # Multi-declaration line (two anchors + two values): bind each value to its own anchor
        # before anything else looks at the line, then let this line's candidates stand alone.
        pairs = _pair_anchors_with_dates(text)
        if len(pairs) >= 2:
            for dtype, value, printed, ambiguous in pairs:
                reason = (
                    f"two declarations printed on one line: '{printed}' bound to its own anchor by "
                    f"printed order ({text.strip()[:60]})"
                )
                if ambiguous:
                    reason += "; day/month order assumed (Indian DD/MM convention), flagged for review"
                candidates.append(
                    Candidate(
                        field_name=f"date_{dtype.lower()}",
                        value=value,
                        raw_value=text,
                        confidence=round(compose_confidence(line.confidence, 0.85, 0.78), 3),
                        score=compose_confidence(line.confidence, 0.85, 0.78) + 0.3,
                        engine=line.engine,
                        variant=line.variant,
                        bbox=line.bbox,
                        source_text=text,
                        reason=reason,
                    )
                )
            continue
        dtype_info = _detect_dtype(text)
        if dtype_info is None:
            continue
        dtype, anchor = dtype_info
        field_name = f"date_{dtype.lower()}"

        # ---- candidate value lines, in evidence order -------------------------------------
        # 1) the anchor's own line, 2) the same visual ROW to the right, 3) the next ROW below
        # in the same COLUMN. Purely spatial association; no product knowledge.
        def _sources() -> list[tuple[str, OcrLine]]:
            out: list[tuple[str, OcrLine]] = [(text, line)]
            same_row = [
                other
                for j, other in enumerate(lines)
                if j != idx and not fl[j] and other.text.strip()
                and _row_overlap(line.bbox, other.bbox) >= 0.5
                and other.bbox[0] >= line.bbox[2] - 2
            ]
            same_row.sort(key=lambda o: o.bbox[0])
            below_candidates = [
                other
                for j, other in enumerate(lines)
                if j != idx and not fl[j] and other.text.strip()
                and other.bbox[1] >= line.bbox[3] - 2
                and _col_overlap(line.bbox, other.bbox) >= 0.5
                and (other.bbox[1] - line.bbox[3]) <= max(3 * (line.bbox[3] - line.bbox[1]), 60)
            ]
            below_candidates.sort(key=lambda o: o.bbox[1])
            # Walk down the same column but STOP at the first line that starts a declaration of its
            # own (its own date anchor, or a field label such as 'Batch No.'/'Net Qty'). Everything
            # past that boundary belongs to another declaration and is not this anchor's value.
            below: list[OcrLine] = []
            for other in below_candidates:
                if _DECLARATION_BOUNDARY_RE.search(other.text) or _date_anchors_in(other.text):
                    break
                below.append(other)
            out += [(o.text.strip(), o) for o in same_row]
            out += [(o.text.strip(), o) for o in below]
            return out

        sources = _sources()
        # Neighbourhood used ONLY to resolve a duration reference. A duration statement is often
        # split by OCR into two stacked lines ('BEST BEFORE 12 MONTHS' / 'FROM THE DATE OF
        # MANUFACTURING'), so the nearest following text line is included even when it does not
        # overlap the anchor horizontally. This text is never used as a date VALUE.
        following = [
            other
            for j, other in enumerate(lines)
            if j != idx and not fl[j] and other.text.strip()
            and other.bbox[1] >= line.bbox[1]
            and other.bbox[1] - line.bbox[3] <= max(4 * (line.bbox[3] - line.bbox[1]), 80)
        ]
        following.sort(key=lambda o: o.bbox[1])
        neighbour_text = " | ".join([t for t, _ in sources] + [o.text.strip() for o in following[:2]])

        def add(value: str, raw: str, conf: float, reason: str, src: OcrLine | None = None) -> None:
            s = src or line
            candidates.append(
                Candidate(
                    field_name=field_name,
                    value=value,
                    raw_value=raw,
                    confidence=round(conf, 3),
                    score=conf + 0.3,
                    engine=s.engine,
                    variant=s.variant,
                    bbox=s.bbox if s is line else _union(line.bbox, s.bbox),
                    source_text=raw,
                    reason=reason,
                )
            )

        resolved = False
        rejected_numeric = ""
        adjacent_code = ""
        for src_text, src_line in sources:
            if not src_text:
                continue
            # A long numeric run is a barcode / EAN / GS1 / article code — never a date value.
            # This is what stops a barcode printed beside 'Use By' becoming its value.
            long_num = LONG_NUMERIC_RE.search(src_text)
            if long_num:
                if not rejected_numeric:
                    rejected_numeric = long_num.group(0)
                continue
            src_raw = src_text if src_line is line else f"{text} | {src_text}"
            from_other_line = "" if src_line is line else " (value on an associated line)"

            fm = FULL_DATE_RE.search(src_text)
            if fm:
                parsed = _try_full_date(fm.group(1), fm.group(2), fm.group(3))
                if parsed:
                    iso, amb = parsed
                    reason = f"'{anchor}' context with full date {fm.group(0)}{from_other_line}"
                    if amb:
                        reason += "; day/month order assumed (Indian DD/MM convention), flagged for review"
                    add(iso + ("|AMBIGUOUS" if amb else ""), src_raw,
                        compose_confidence(src_line.confidence, 0.85, 0.8), reason, src_line)
                    resolved = True
                    break

            code = DATE_CODE_RE.search(src_text)
            if code:
                parsed_code = _try_date_code(code.group(1))
                if parsed_code:
                    iso, amb = parsed_code
                    adjacent_code = code.group(1)
                    add(
                        iso + ("|AMBIGUOUS" if amb else ""),
                        src_raw,
                        compose_confidence(src_line.confidence, 0.55, 0.5),
                        f"'{anchor}' context with separator-less inkjet date code '{code.group(1)}' "
                        "read as DD/MM/YY (Indian convention); low-confidence dot-matrix print — "
                        f"candidate offered for review{from_other_line}",
                        src_line,
                    )
                    resolved = True
                    break

            nm = MONTH_NAME_RE.search(src_text)
            if nm:
                mon = MONTHS.get(nm.group(1).lower()[:3]) or MONTHS.get(nm.group(1).lower())
                if mon:
                    try:
                        yyi = int(nm.group(2))
                    except ValueError:
                        yyi = 0
                    if yyi:
                        add(f"{yyi:04d}-{mon:02d}", src_raw,
                            compose_confidence(src_line.confidence, 0.9, 0.85),
                            f"'{anchor}' context with 'MON YYYY' date{from_other_line}", src_line)
                        resolved = True
                        break

            pm = PERIOD_RE.search(src_text)
            if pm:
                n, unit = pm.group(1), pm.group(2).lower()
                # DURATION-BASED declaration: '<n> <units> FROM <reference>'. Stored
                # SEMANTICALLY (type / duration / reference) and NEVER converted into a concrete
                # calendar date — computing one would be interpretation, not extraction. The
                # reference may be printed on the line AFTER the duration, so the anchor's own
                # row and column neighbours are searched for it too.
                ref_match = DURATION_REF_RE.search(src_text) or DURATION_REF_RE.search(neighbour_text)
                if ref_match:
                    ref_word = ref_match.group(1).lower()
                    if ref_word.startswith(("manufactur", "mfg", "mfd")):
                        reference = "MANUFACTURING_DATE"
                    elif ref_word.startswith("import"):
                        reference = "IMPORT_DATE"
                    else:
                        reference = "PACKING_DATE"
                    value = json.dumps(
                        {
                            "type": "DURATION_FROM_REFERENCE",
                            "duration": f"{n} {unit}",
                            "reference": reference,
                            "display": f"{n} {unit}",
                        },
                        ensure_ascii=False,
                    )
                    add(
                        value, src_raw, compose_confidence(src_line.confidence, 0.9, 0.85),
                        f"'{anchor}' duration declaration: {n} {unit} from {reference} "
                        "(stored semantically — not converted to a date)",
                        src_line,
                    )
                    resolved = True
                    break
                add(f"{n} {unit}", src_raw, compose_confidence(src_line.confidence, 0.85, 0.8),
                    f"'{anchor}' context with period declaration ({n} {unit})", src_line)
                resolved = True
                break

            my = MONTH_YEAR_RE.search(src_text.rstrip("."))
            if my:
                try:
                    mmi, yyi = int(my.group(1)), int(my.group(2))
                except ValueError:
                    continue
                if 1 <= mmi <= 12:
                    if yyi < 100:
                        yyi += 2000
                    add(f"{yyi:04d}-{mmi:02d}", src_raw,
                        compose_confidence(src_line.confidence, 0.9, 0.85),
                        f"'{anchor}' context with MM/YYYY date{from_other_line}", src_line)
                    resolved = True
                    break

        if resolved:
            continue

        # Anchor present but nothing parseable -> honest UNCERTAIN candidate. Confidence 0.0:
        # there is NO extracted value, so there is no value-confidence. The anchor observation —
        # and any rejected barcode, which is explicitly NOT a date — is preserved so the
        # inspector can see exactly what was read.
        note = ""
        if adjacent_code:
            note = f"; adjacent code '{adjacent_code}' could not be resolved to a date"
        elif rejected_numeric:
            note = (
                f"; adjacent numeric '{rejected_numeric[:24]}' is a barcode/article code, not a date"
            )
        add(
            "",
            text + (f" | {rejected_numeric}" if rejected_numeric else ""),
            0.0,                    f"'{anchor}' present but date not parseable — anchor observation only{note}",
        )

    return _reconcile_date_block(candidates, lines, fl)


def _detect_end(text: str) -> int:  # pragma: no cover - kept for interface stability
    return len(text)
