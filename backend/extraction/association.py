"""Label-value association: joining split label/value OCR fragments generically.

Real labels often OCR as separate lines:
    "M.R.P." / "₹" / "200.00"
    "Net Weight :" / "500g"
    "Mfg. :" / "04/02/25"

This module rebuilds those pairs from OCR geometry — NOT from fixed coordinates. Rules:
  - a value line is associated with a label line when it is the nearest line whose
    vertical gap is small (same block), horizontally overlapping or right-adjacent,
  - and the label line alone has no value (anchor-only),
  - and the value line carries no competing anchor of its own.

The association only feeds *candidates* to the extractors (merged lines are appended as
synthesized lines with provenance 'assoc:<variant>'); the extractors' own anchors and
negative guards still decide what is trustworthy. Nothing is forced into fields.
"""
from __future__ import annotations

import re

from backend.ocr.base import OcrLine

# A line that is (mostly) a label anchor with no value yet, ending with an optional colon.
_LABEL_ONLY_RE = re.compile(
    r"^(?:m\.?\s*r\.?\s*p\.?|mrp|maximum\s+retail\s+price|retail\s+(?:sale\s+)?price|"
    r"net\s*(?:qty|quantity|wt\.?|weight|volume|contents?)|nett\s*wt\.?|"
    r"mfd|mfg(?:d)?\.?|mfg\.?\s*date|manufacturing\s*date|pkd|packed|packaging\s*date|"
    r"exp(?:iry)?\.?|use\s*by|best\s*before|batch\s*(?:no\.?|number)?|lot\s*(?:no\.?)?|"
    r"fssai(?:\s*lic\.?\s*no\.?)?|lic\.?\s*no\.?|consumer\s*care|customer\s*care|"
    r"country\s*of\s*origin|made\s*in|manufactured\s*(?:&|and)?\s*by|marketed\s*by|"
    r"packed\s*by|imported\s*by)\b[\s:.\-]*$",
    re.IGNORECASE,
)
# A line that plausibly carries a value (has a digit, currency, email, URL, or ≥3 letters).
_HAS_VALUE_RE = re.compile(r"(\d|[₹@]|www\.|[A-Za-z]{3,})")
# A value line must NOT itself start with another label anchor of a different family.
_ANCHOR_START_RE = re.compile(
    r"^\s*(?:m\.?\s*r\.?\s*p|mrp|net\s|nett\s|mfd|mfg|pkd|packed|exp|use\s*by|best\s*before|"
    r"batch|lot|fssai|lic\.|consumer|customer|country|made\s*in|manufactured|marketed|imported)",
    re.IGNORECASE,
)
# A bare currency marker with NO digits: '₹' / 'Rs' / 'Rs.' — a bridge fragment.
_CURRENCY_BRIDGE_RE = re.compile(r"^\s*(?:₹|rs\.?|inr)\s*$", re.IGNORECASE)
_NUMERIC_CONT_RE = re.compile(r"\d")
# Value lines that must NOT pair with a batch/lot label: long pure-digit codes that are
# themselves other identifiers (FSSAI-like 14+ digits, phone-like 10-13 digits). A real
# batch/lot value is short alphanumeric (e.g. 80130, B0142, LOT23A91).
_BATCH_VALUE_SHAPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/]{2,19}$")
_LONG_DIGITS_RE = re.compile(r"^\d{10,}$")


def _y_center(l: OcrLine) -> float:
    return (l.bbox[1] + l.bbox[3]) / 2.0


def _height(l: OcrLine) -> float:
    return max(1.0, l.bbox[3] - l.bbox[1])


def associate_label_values(lines: list[OcrLine]) -> list[OcrLine]:
    """Return synthesized 'label+value' lines for anchor-only labels with a nearby value line.

    Original lines are returned unchanged; synthesized lines are APPENDED with
    variant='assoc:<original-variant>' so evidence provenance stays explicit.
    """
    if len(lines) < 2:
        return list(lines)
    out = list(lines)
    synth: list[OcrLine] = []
    for i, lab in enumerate(lines):
        text = lab.text.strip()
        if not _LABEL_ONLY_RE.match(text):
            continue
        # candidate value lines: nearest below or same row to the right
        best: tuple[float, OcrLine] | None = None
        for j, val in enumerate(lines):
            if j == i:
                continue
            vtext = val.text.strip()
            if not _HAS_VALUE_RE.search(vtext) or _ANCHOR_START_RE.match(vtext):
                continue
            vgap = _y_center(val) - _y_center(lab)
            h = max(_height(lab), _height(val))
            same_row_right = (
                abs(vgap) <= 0.6 * h and val.bbox[0] >= lab.bbox[2] - 0.25 * h
            )
            below = 0 < vgap <= 2.2 * h
            x_overlap = min(lab.bbox[2], val.bbox[2]) - max(lab.bbox[0], val.bbox[0])
            if not (same_row_right or (below and x_overlap > 0)):
                continue
            # batch/lot labels pair only with short alphanumeric values — never a long
            # pure-digit neighbor that is itself another identifier (FSSAI/phone).
            is_batch_label = bool(re.match(r"^\s*batch\b|^\s*lot\b", text, re.IGNORECASE))
            if is_batch_label and (
                not _BATCH_VALUE_SHAPE_RE.match(vtext) or _LONG_DIGITS_RE.match(vtext)
            ):
                continue
            # reject value lines that are themselves anchors (handled by _LABEL_ONLY_RE above)
            if _LABEL_ONLY_RE.match(vtext):
                continue            # same-row-right is the strong cue (declared beside the label); below is weaker
            dist = abs(vgap) + (0 if same_row_right else 0.5 * h)
            if best is None or dist < best[0]:
                best = (dist, val)
        if best is None:
            continue
        val = best[1]
        parts = [text.rstrip(': '), val.text.strip()]
        bbox = (
            min(lab.bbox[0], val.bbox[0]),
            min(lab.bbox[1], val.bbox[1]),
            max(lab.bbox[2], val.bbox[2]),
            max(lab.bbox[3], val.bbox[3]),
        )
        conf = min(lab.confidence, val.confidence)
        # bridge case: the chosen value is a bare currency marker ('₹' / 'Rs'); the actual
        # number usually sits on the same row to its right. Join the numeric continuation.
        if _CURRENCY_BRIDGE_RE.match(val.text.strip()):
            for k, cont in enumerate(lines):
                if cont is lab or cont is val:
                    continue
                if not _NUMERIC_CONT_RE.search(cont.text):
                    continue
                if _LABEL_ONLY_RE.match(cont.text.strip()):
                    continue
                # same row as the bridge, immediately to its right
                if abs(_y_center(cont) - _y_center(val)) <= 0.6 * _height(val) and (
                    cont.bbox[0] >= val.bbox[2] - 0.25 * _height(val)
                ):
                    parts.append(cont.text.strip())
                    bbox = (bbox[0], bbox[1], max(bbox[2], cont.bbox[2]), max(bbox[3], cont.bbox[3]))
                    conf = min(conf, cont.confidence)
                    break
        synth.append(OcrLine(
            text=" ".join(parts),
            confidence=conf,
            bbox=bbox,
            engine=lab.engine,
            variant=f"assoc:{lab.variant}",
        ))
    # Cross-variant dedupe: the same label can exist in several variants (original + upscaled
    # each produce an assoc line for the same declaration). Keep one per label bbox
    # (IoU >= 0.7), preferring the higher-confidence synthesis — duplicates would otherwise
    # double-count candidates inside the extractors.
    deduped: list[OcrLine] = []
    for s in synth:
        dup = None
        for k, keep in enumerate(deduped):
            if _bbox_iou(s.bbox, keep.bbox) >= 0.7:
                dup = k
                break
        if dup is None:
            deduped.append(s)
        elif s.confidence > deduped[dup].confidence:
            deduped[dup] = s
    return out + deduped


def _bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter == 0:
        return 0.0
    area_a = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / float(area_a + area_b - inter)
