"""MRP extraction — the highest-priority extractor.

Never "largest number wins". MRP requires an MRP keyword anchor (MRP / M.R.P. / M R P /
Maximum Retail Price / Retail Sale Price) on the same OCR line as a currency amount, with
negative patterns rejecting unit prices, nutrition, phones, FSSAI, PINs, batches and dates.
Unit sale price is extracted separately and NEVER emitted as MRP.
"""
from __future__ import annotations

import re

from backend.extraction.base import (
    Candidate,
    FSSAI_RE,
    PIN_RE,
    PHONE_RE,
    UNIT_PRICE_RE,
    compose_confidence,
    is_nutrition_line,
    lines_sorted,
)
from backend.ocr.base import OcrLine

# ---------- patterns ----------

MRP_KEYWORD_RE = re.compile(
    r"\b(?:m\.?\s*r\.?\s*p\.?|m\.r\.p\.?|mrp|maximum\s+retail\s+price|retail\s+sale\s+price)\b\.?:?",
    re.IGNORECASE,
)
CURRENCY_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d{1,2})?)")

NEGATIVE_CONTEXT_RE = re.compile(
    r"(?:/|per\s)\s*(?:g|kg|gm|gram|grams|ml|l|litre|liter|nos?|no\.?|pcs|unit|100\s*g|100\s*ml)"
    r"|nutrition|energy|protein|carbohydrate|calories?|kcal|fssai|lic(?:ence|ense)?\s*no|batch|lot\b|"
    r"phone|mobile|tel\b|toll\s*free|email|www\.|http|pin\s*code|invoice|tax",
    re.IGNORECASE,
)
UNIT_PRICE_LINE_RE = UNIT_PRICE_RE

#: Marker in a candidate's ``reason`` meaning "this amount had NO MRP keyword anchor behind it".
#: The listing reader uses it to keep a displayed price out of the MRP field (a price shown on a
#: listing page is not automatically the retail sale price declaration).
UNANCHORED_REASON_MARKER = "without MRP keyword"


def _clean_amount(raw: str) -> str:
    return raw.replace(",", "").strip()


def _parse_amount(raw: str) -> float | None:
    try:
        return float(_clean_amount(raw))
    except ValueError:
        return None


def extract_mrp(lines: list[OcrLine], flags: list[bool] | None = None) -> list[Candidate]:
    """Extract MRP candidates from OCR lines. Empty result means: nothing found (never guessed)."""
    lines = lines_sorted(lines)
    fl = flags if flags is not None else [False] * len(lines)
    candidates: list[Candidate] = []

    for idx, line in enumerate(lines):
        text = line.text.strip()
        if not text or fl[idx]:
            continue
        if UNIT_PRICE_LINE_RE.search(text):
            continue  # unit sale price — handled by the dedicated extractor, never MRP

        kw = MRP_KEYWORD_RE.search(text)
        cur = CURRENCY_RE.search(text)

        if kw and cur:
            amount = _parse_amount(cur.group(1))
            if amount is None:
                continue
            if NEGATIVE_CONTEXT_RE.search(text):
                conf_penalty = 0.15
                reason_note = "negative context present on line"
            else:
                conf_penalty = 0.0
                reason_note = "MRP keyword and currency amount on the same line"
            conf = compose_confidence(line.confidence, 0.95, 0.9) - conf_penalty
            candidates.append(
                Candidate(
                    field_name="mrp",
                    value=_clean_amount(cur.group(1)),
                    raw_value=text,
                    confidence=round(max(0.05, conf), 3),
                    score=conf + 0.3,
                    engine=line.engine,
                    variant=line.variant,
                    bbox=line.bbox,
                    source_text=text,
                    reason=f"{reason_note}; keyword='{kw.group(0)}', amount={_clean_amount(cur.group(1))}",
                )
            )
            continue

        # Anchor present, currency marker lost by OCR (₹ glyph mis-read) but a number follows
        # on the line: bind the FIRST amount to the anchor with reduced confidence. The MRP
        # anchor is the semantic evidence; the missing glyph must not erase the declaration.
        # Fused amounts ('200.0010.4091' = MRP 200.00 + unit price fragments) are decomposed:
        # the leading 2-decimal group becomes the MRP candidate; a /g-style continuation on
        # the line goes to unit_sale_price — never merged into MRP.
        if kw and not cur:
            fused = re.search(r"(\d{2,6})\.(\d{2})(\d{2})\.(\d{2})", text)
            if fused and not UNIT_PRICE_LINE_RE.search(text):
                mrp_val = f"{fused.group(1)}.{fused.group(2)}"
                conf = compose_confidence(line.confidence, 0.7, 0.7)
                candidates.append(
                    Candidate(
                        field_name="mrp",
                        value=mrp_val,
                        raw_value=text,
                        confidence=round(max(0.05, conf), 3),
                        score=conf,
                        engine=line.engine,
                        variant=line.variant,
                        bbox=line.bbox,
                        source_text=text,
                        reason=(
                            f"MRP keyword present; currency marker not detected by OCR; fused amount "
                            f"'{fused.group(0)}' decomposed to MRP {mrp_val} (remainder treated as non-MRP)"
                        ),
                    )
                )
                continue
            simple = AMOUNT_RE.search(text[kw.end():]) if text[kw.end():].strip() else None
            if simple:
                val = _clean_amount(simple.group(1))
                if val and float(val) > 0:
                    conf = compose_confidence(line.confidence, 0.65, 0.6)
                    candidates.append(
                        Candidate(
                            field_name="mrp",
                            value=val,
                            raw_value=text,
                            confidence=round(max(0.05, conf), 3),
                            score=conf,
                            engine=line.engine,
                            variant=line.variant,
                            bbox=line.bbox,
                            source_text=text,
                            reason=(
                                "MRP keyword present; currency marker not detected by OCR; "
                                f"amount '{val}' bound to the MRP anchor at reduced confidence"
                            ),
                        )
                    )
                    continue

        # keyword-only line: accept a currency amount on the IMMEDIATE next line only when
        # horizontally aligned (e.g. 'MRP' header above the value) — tightly bounded, not greedy.
        if kw and not cur:
            below = [
                lines[j]
                for j in range(idx + 1, min(idx + 3, len(lines)))
                if not fl[j]
            ]
            for other in below:
                om = CURRENCY_RE.search(other.text)
                if not om or UNIT_PRICE_LINE_RE.search(other.text) or NEGATIVE_CONTEXT_RE.search(other.text):
                    continue
                # horizontal alignment: overlapping x-ranges
                x_overlap = min(line.bbox[2], other.bbox[2]) - max(line.bbox[0], other.bbox[0])
                if x_overlap <= 0:
                    continue
                amount = _parse_amount(om.group(1))
                if amount is None:
                    continue
                conf = compose_confidence((line.confidence + other.confidence) / 2, 0.8, 0.75)
                candidates.append(
                    Candidate(
                        field_name="mrp",
                        value=_clean_amount(om.group(1)),
                        raw_value=f"{text} ⏎ {other.text}",
                        confidence=round(conf, 3),
                        score=conf + 0.1,
                        engine=line.engine,
                        variant=line.variant,
                        bbox=line.bbox,
                        source_text=f"{text} | {other.text}",
                        reason="MRP keyword on one line, currency amount on the aligned next line",
                    )
                )
                break

        # currency-only line: weak candidate, only if not near any nutrition/other anchors
        if cur and not kw:
            if NEGATIVE_CONTEXT_RE.search(text) or PIN_RE.search(text) or FSSAI_RE.search(text) or PHONE_RE.search(text):
                continue
            amount = _parse_amount(cur.group(1))
            if amount is None or amount <= 0:
                continue
            conf = compose_confidence(line.confidence, 0.45, 0.3)
            candidates.append(
                Candidate(
                    field_name="mrp",
                    value=_clean_amount(cur.group(1)),
                    raw_value=text,
                    confidence=round(conf, 3),
                    score=conf,
                    engine=line.engine,
                    variant=line.variant,
                    bbox=line.bbox,
                    source_text=text,
                    reason=f"currency amount {UNANCHORED_REASON_MARKER} (weak candidate)",
                )
            )

    # dedupe by (value, bbox)
    seen: set[tuple[str, str]] = set()
    unique: list[Candidate] = []
    for c in candidates:
        key = (c.value, str(c.bbox))
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def extract_unit_sale_price(lines: list[OcrLine], flags: list[bool] | None = None) -> list[Candidate]:
    """Unit sale price (e.g. '₹0.40/g') — extracted separately; NEVER emitted as MRP."""
    lines = lines_sorted(lines)
    candidates: list[Candidate] = []
    for line in lines:
        m = UNIT_PRICE_RE.search(line.text)
        if not m:
            continue
        conf = compose_confidence(line.confidence, 0.9, 0.8)
        candidates.append(
            Candidate(
                field_name="unit_sale_price",
                value=m.group(0).strip(),
                raw_value=line.text.strip(),
                confidence=round(conf, 3),
                score=conf,
                engine=line.engine,
                variant=line.variant,
                bbox=line.bbox,
                source_text=line.text.strip(),
                reason="unit price pattern (price per unit) — kept separate from MRP",
            )
        )
    return candidates
