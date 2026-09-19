"""Batch/lot, FSSAI, and contact extraction with structural validation and context anchoring."""
from __future__ import annotations

import re

from backend.extraction.base import (
    DATEISH_RE,
    Candidate,
    FSSAI_RE,
    PHONE_RE,
    PIN_RE,
    compose_confidence,
    lines_sorted,
    value_column_neighbours,
)
from backend.ocr.base import OcrLine

BATCH_KEYWORD_RE = re.compile(r"\b(?:batch|lot)\s*(?:no\.?|number)?\b\s*[:.\-]?", re.IGNORECASE)
BATCH_VALUE_RE = re.compile(r"[:.\-\s]*([A-Za-z0-9][A-Za-z0-9\-/]{2,20})$")

FSSAI_KEYWORD_RE = re.compile(
    r"\b(?:fssai(?:\s*lic(?:ence|ense)?\.?\s*no\.?)?|lic(?:ence|ense)?\.?\s*no\.?)\b\s*[:.\-]?",
    re.IGNORECASE,
)
FSSAI_NUMBER_RE = re.compile(r"\b(\d{14})\b|\b(\d{10})\b")

CONSUMER_CARE_RE = re.compile(
    r"\b(?:consumer\s*care|customer\s*care|consumer\s*complaints?|toll\s*free|helpline|contact\s*us?)\b\s*[:.\-]?",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
PHONE_LINE_RE = re.compile(
    r"(?:\+?91[\s-]?)?\b[6-9]\d{9}\b"      # 9876543210 / +91 98765 43210
    r"|\b0?[6-9]\d{4}[\s-]?\d{5}\b"        # landline/mobile with leading 0 + internal space: 044 2233 4455
    r"|\b1800[\s-]?\d{3}[\s-]?\d{3,4}\b",
)  # toll-free
WEBSITE_RE = re.compile(r"\b((?:www\.|https?://)[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s,;]*)?)", re.IGNORECASE)


def _clean_token(tok: str) -> str:
    return tok.strip(" :.,;-")


# A batch/lot value is an IDENTIFIER. Guarding on identifier shape is what stops ordinary words
# ('OTHER'), dates, PIN-shaped numbers, article numbers and machine/time codes from being
# published as a batch code: a real code carries at least one digit, and a value with an embedded
# separator such as ':' ('M/C10:21') is a machine/time print, not a lot code.
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/]{2,19}")


def _identifier_like(value: str) -> bool:
    v = (value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(v):
        return False
    if not any(ch.isdigit() for ch in v):
        return False
    if DATEISH_RE.fullmatch(v) or FSSAI_RE.search(v) or PHONE_RE.search(v) or PIN_RE.fullmatch(v):
        return False
    return True


def extract_batch_lot(lines: list[OcrLine], flags: list[bool] | None = None) -> list[Candidate]:
    lines = lines_sorted(lines)
    fl = flags if flags is not None else [False] * len(lines)
    candidates: list[Candidate] = []
    for idx, line in enumerate(lines):
        text = line.text.strip()
        if not text or fl[idx]:
            continue
        kw = BATCH_KEYWORD_RE.search(text)
        if not kw:
            continue
        rest = text[kw.end():]
        vm = BATCH_VALUE_RE.search(rest) if rest.strip() else None
        value = _clean_token(vm.group(1)) if vm else ""
        source_text = text
        if not value:
            # value may sit on the next line, aligned
            if idx + 1 < len(lines) and not fl[idx + 1]:
                nxt = lines[idx + 1].text.strip()
                if len(nxt) <= 24 and not re.search(r"[€₹$]", nxt) and not FSSAI_RE.search(nxt) and not PIN_RE.search(nxt):
                    # An association-synthesized next line often REPEATS the anchor
                    # ('Batch No' followed by 'Batch No B0142'). The anchor is never part of
                    # the batch code, so take only the portion after it.
                    nkw = BATCH_KEYWORD_RE.match(nxt)
                    value = _clean_token(nxt[nkw.end():] if nkw else nxt)
                    source_text = f"{text} | {nxt}"
        if value and not _identifier_like(value):
            # A neighbouring non-identifier (the next row's word, a date, a price) is not the code.
            # The value column beside the label is searched instead: on real prints the label
            # column is the left one and the code sits beside it, and OCR row offsets between the
            # two columns routinely exceed a line height. Only an identifier-shaped token
            # qualifies — never a word, date, PIN or price.
            value = ""
        if not value:
            for j in value_column_neighbours(lines, idx):
                if fl[j]:
                    continue
                tok = _clean_token(lines[j].text.strip())
                if _identifier_like(tok):
                    value = tok
                    source_text = f"{text} | {lines[j].text.strip()}"
                    break
        if not value:
            continue
        # The LABEL is never the value: association/OCR variants can offer the bare anchor
        # ('Batch No') as the following token — a value that is only the keyword is rejected.
        if re.fullmatch(r"(?i)(?:batch|lot)\s*(?:no\.?|number|code)?\s*", value):
            continue
        # negative guards: don't swallow phones / FSSAI / PINs / short price-like amounts.
        # Pure-digit values are allowed only at batch-code lengths (>=5 digits, excluding
        # PIN-shaped 6-digit numbers): 'Batch: 200' (a price) is rejected, 'Batch: 80130'
        # (numeric batch code) is accepted. Alphanumeric values are unaffected.
        digits_only = re.fullmatch(r"[\d.,]+", value)
        if PHONE_RE.search(value) or FSSAI_RE.search(value):
            continue
        if digits_only and (len(value) < 5 or re.fullmatch(r"[1-9]\d{5}", value)):
            continue
        if not _identifier_like(value):
            # Not an identifier: a bare word ('OTHER'), a date, a PIN-shaped number or a
            # code-with-punctuation is not offered as a batch/lot value. Better MISSING than a
            # confident wrong code in an inspection record.
            continue
        conf = compose_confidence(line.confidence, 0.85, 0.8)
        candidates.append(
            Candidate(
                field_name="batch_lot",
                value=value,
                raw_value=source_text,
                confidence=round(conf, 3),
                score=conf,
                engine=line.engine,
                variant=line.variant,
                bbox=line.bbox,
                source_text=source_text,
                reason=f"batch/lot keyword with adjacent alphanumeric token '{value}'",
            )
        )
    return candidates


def extract_fssai(lines: list[OcrLine], flags: list[bool] | None = None) -> list[Candidate]:
    lines = lines_sorted(lines)
    fl = flags if flags is not None else [False] * len(lines)
    candidates: list[Candidate] = []
    for idx, line in enumerate(lines):
        text = line.text.strip()
        if not text or fl[idx]:
            continue
        kw = FSSAI_KEYWORD_RE.search(text)
        if not kw:
            continue
        rest = text[kw.end():]
        nm = FSSAI_NUMBER_RE.search(rest) or FSSAI_NUMBER_RE.search(text)
        if not nm:
            continue
        digits = nm.group(1) or nm.group(2) or ""
        if not digits:
            continue
        strength = 0.95 if len(digits) == 14 else 0.7  # 14-digit is the expected structure
        conf = compose_confidence(line.confidence, strength, 0.9)
        candidates.append(
            Candidate(
                field_name="fssai_license",
                value=digits,
                raw_value=text,
                confidence=round(conf, 3),
                score=conf + (0.1 if len(digits) == 14 else 0.0),
                engine=line.engine,
                variant=line.variant,
                bbox=line.bbox,
                source_text=text,
                reason=f"FSSAI/licence keyword with {'14-digit' if len(digits) == 14 else '10-digit'} number",
            )
        )
    return candidates


def extract_contacts(lines: list[OcrLine], flags: list[bool] | None = None) -> list[Candidate]:
    """Consumer-care phone and email candidates, kept strictly separate from MRP."""
    lines = lines_sorted(lines)
    fl = flags if flags is not None else [False] * len(lines)
    candidates: list[Candidate] = []
    for idx, line in enumerate(lines):
        text = line.text.strip()
        if not text or fl[idx]:
            continue
        has_cc = bool(CONSUMER_CARE_RE.search(text))
        em = EMAIL_RE.search(text)
        if em:
            conf = compose_confidence(line.confidence, 0.9, 0.85 if has_cc else 0.7)
            candidates.append(
                Candidate(
                    field_name="consumer_care_email",
                    value=em.group(1).lower(),
                    raw_value=text,
                    confidence=round(conf, 3),
                    score=conf,
                    engine=line.engine,
                    variant=line.variant,
                    bbox=line.bbox,
                    source_text=text,
                    reason="email pattern" + (" near consumer-care keyword" if has_cc else ""),
                )
            )
        pm = PHONE_LINE_RE.search(text)
        if pm:
            # exclude lines that are actually prices or identifiers
            if re.search(r"[₹$]|rs\.?\s", text, re.IGNORECASE) or FSSAI_RE.search(text):
                continue
            phone = re.sub(r"[\s\-]", "", pm.group(0))
            conf = compose_confidence(line.confidence, 0.85, 0.85 if has_cc else 0.6)
            candidates.append(
                Candidate(
                    field_name="consumer_care_phone",
                    value=phone,
                    raw_value=text,
                    confidence=round(conf, 3),
                    score=conf + (0.1 if has_cc else 0.0),
                    engine=line.engine,
                    variant=line.variant,
                    bbox=line.bbox,
                    source_text=text,
                    reason="phone pattern" + (" near consumer-care keyword" if has_cc else ""),
                )
            )
    return candidates


def extract_website(lines: list[OcrLine], flags: list[bool] | None = None) -> list[Candidate]:
    lines = lines_sorted(lines)
    candidates: list[Candidate] = []
    for line in lines:
        m = WEBSITE_RE.search(line.text)
        if not m:
            continue
        value = m.group(1).rstrip(".,;")
        conf = compose_confidence(line.confidence, 0.9, 0.8)
        candidates.append(
            Candidate(
                field_name="website",
                value=value.lower(),
                raw_value=line.text.strip(),
                confidence=round(conf, 3),
                score=conf,
                engine=line.engine,
                variant=line.variant,
                bbox=line.bbox,
                source_text=line.text.strip(),
                reason="website URL pattern",
            )
        )
    return candidates
