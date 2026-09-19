"""Online product-listing extraction (e-commerce listings, product pages, listing text).

A listing is treated as a one-page document: its lines are synthesised into the SAME ``OcrLine``
structure the image pipeline produces, and the existing extractors (MRP, net quantity, dates,
manufacturer / packer / importer, identifiers, product identity, consumer-care contact, country of
origin) are then run over it unchanged. Nothing about package extraction is re-implemented here —
a listing simply reads line-by-line instead of pixel-by-pixel.

Two things exist only for a listing and are extracted by the small deterministic layer at the end:

* ``listing_title``  — the displayed product title,
* ``listing_price``  — the price the buyer is shown (which is NOT automatically the MRP),
* ``listing_seller`` — the seller / dispatcher entity as printed on the page.

Kept deliberately literal: nothing is inferred into a declaration field that the page does not
print, and every value carries the line it came from.
"""
from __future__ import annotations

import json
import re

from backend.extraction.base import Candidate, looks_like_company_entity
from backend.extraction.engine import run_extraction
from backend.extraction.mrp import UNANCHORED_REASON_MARKER
from backend.ocr.base import OcrLine

LISTING_ENGINE = "listing_text"
LISTING_VARIANT = "listing"

#: Fields whose printed form is the whole line (a value + its printed wording must both be kept),
#: as opposed to entity/text fields where the extracted value IS the declaration.
_PRINTED_FORM_FIELDS = {
    "mrp",
    "net_quantity",
    "unit_sale_price",
    "date_manufacturing",
    "date_packing",
    "date_import",
    "date_expiry",
    "date_best_before",
    "batch_lot",
    "fssai_license",
}

_AMOUNT_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.IGNORECASE)
_BARE_AMOUNT_RE = re.compile(r"\b([0-9]{1,6}(?:\.[0-9]{1,2})?)\b")
_PRICE_LINE_RE = re.compile(
    r"\b(?:price|mrp|deal|offer|buy\s*(?:at|now)|sale\s*price|selling\s*price|you\s*pay)\b",
    re.IGNORECASE,
)
_TITLE_ANCHOR_RE = re.compile(r"^\s*(?:product\s*name|title|item\s*name|name)\s*[:\-]\s*(.+)$", re.IGNORECASE)
_SELLER_ANCHOR_RE = re.compile(
    r"^\s*(?:sold\s*by|seller|dispatched\s*by|shipped\s*by|ships?\s*from|fulfilled\s*by|"
    r"market(?:ed)?\s*by)\s*[:\-]\s*(.*)$",
    re.IGNORECASE,
)
_BOILERPLATE_RE = re.compile(
    r"^(?:\s*|add\s+to\s+(?:cart|basket)|buy\s+now|wishlist|share|qty\s*:?\s*\d+|"
    r"\d+\s*(?:customer\s+)?review[s]?|in\s+stock|out\s+of\s+stock|free\s+delivery|"
    r"save\s+\d+%?|emi\s+from.*|[0-9]{1,3}%\s*off)\s*$",
    re.IGNORECASE,
)

#: A line that carries a DECLARATION or a price is not the product title. Used only by the title
#: inference, which otherwise tends to pick the longest declaration line on the page.
_DECLARATION_LINE_RE = re.compile(
    r"\b(?:net\s*(?:qty|quantity|weight|volume|content)|mrp|m\.r\.p|maximum\s+retail|"
    r"retail\s+(?:sale\s+)?price|unit\s+(?:sale\s+)?price|brand|country\s+of\s+origin|made\s+in|"
    r"consumer\s+care|customer\s+care|helpline|e-?mail|batch|lot\s*(?:no|number)|sold\s+by|"
    r"packed\s+by|manufactured\s+by|mfd\.?\s*by|marketed\s+by|imported\s+by|"
    r"fssai|lic(?:ence|ense)\s+no|best\s+before|use\s+before|expiry|price|₹|rs\.?\s*\d)\b",
    re.IGNORECASE,
)


def _clean_amount(raw: str) -> str:
    return raw.replace(",", "").strip()


def lines_from_text(text: str, *, page_width: int = 1200, image_id: int = 1) -> list[OcrLine]:
    """Turn plain text into synthesised OCR lines with a stable reading order and geometry.

    Geometry is synthetic and is labelled as such (``variant='listing'``): x is derived from the
    line's indentation, y from its position in the document. It exists so spatial helpers behave
    deterministically on listing text — it is never presented as a measurement.
    """
    lines: list[OcrLine] = []
    row_height = 22
    y = 10
    for raw_line in (text or "").splitlines():
        stripped = raw_line.rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip())
        x1 = 10 + min(indent, 40) * 6
        width = min(page_width - x1 - 10, max(80, len(stripped.strip()) * 9))
        lines.append(
            OcrLine(
                text=stripped.strip(),
                confidence=0.95,  # the caller supplied the text; there is no OCR uncertainty
                bbox=(x1, y, x1 + width, y + row_height - 4),
                engine=LISTING_ENGINE,
                variant=LISTING_VARIANT,
            )
        )
        y += row_height
    return lines


def _best_candidates(lines: list[OcrLine]) -> dict[str, Candidate]:
    """Run every proven extractor once over the listing and keep the strongest candidate per field."""
    per_field = run_extraction({1: lines})
    best: dict[str, Candidate] = {}
    for name, candidates in per_field.items():
        usable = [c for c in candidates if (c.value or "").strip()]
        if not usable:
            continue
        winner = max(usable, key=lambda c: (c.score, c.confidence))
        best[name] = winner
    return best


def _display_for(name: str, candidate: Candidate) -> str:
    if name in _PRINTED_FORM_FIELDS:
        return (candidate.raw_value or candidate.value or "").strip()[:160]
    return (candidate.value or "").strip()[:160]


def _extract_title(lines: list[OcrLine]) -> dict | None:
    for line in lines:
        m = _TITLE_ANCHOR_RE.match(line.text)
        if m and m.group(1).strip():
            return {
                "display_value": m.group(1).strip()[:160],
                "confidence": 0.9,
                "source_text": line.text,
                "method": "explicit title/product-name label",
            }
    # No anchor: the displayed title is conventionally the longest substantial line near the top
    # that is not itself a declaration or a price line.
    head = [
        ln
        for ln in lines[:8]
        if len(ln.text.strip()) >= 6
        and not _BOILERPLATE_RE.match(ln.text)
        and not _DECLARATION_LINE_RE.search(ln.text)
    ]
    if not head:
        return None
    candidate = max(head, key=lambda ln: len(ln.text.strip()))
    return {
        "display_value": candidate.text.strip()[:160],
        "confidence": 0.45,
        "source_text": candidate.text,
        "method": "inferred from page position (no title label printed)",
        "inferred": True,
    }


def _extract_price(lines: list[OcrLine]) -> dict | None:
    for line in lines:
        if not _PRICE_LINE_RE.search(line.text):
            continue
        m = _AMOUNT_RE.search(line.text) or _BARE_AMOUNT_RE.search(line.text)
        if m:
            return {
                "display_value": line.text.strip()[:160],
                "normalized_value": _clean_amount(m.group(1)),
                "confidence": 0.8 if _AMOUNT_RE.search(line.text) else 0.5,
                "source_text": line.text,
                "method": "price-labelled line on the listing page",
            }
    for line in lines:  # fall back to any currency amount on the page
        m = _AMOUNT_RE.search(line.text)
        if m:
            return {
                "display_value": line.text.strip()[:160],
                "normalized_value": _clean_amount(m.group(1)),
                "confidence": 0.4,
                "source_text": line.text,
                "method": "currency amount found without a price label",
                "inferred": True,
            }
    return None


def _extract_seller(lines: list[OcrLine]) -> dict | None:
    for idx, line in enumerate(lines):
        m = _SELLER_ANCHOR_RE.match(line.text)
        if not m:
            continue
        inline = m.group(1).strip()
        if inline:
            return {
                "display_value": inline[:160],
                "confidence": 0.85,
                "source_text": line.text,
                "method": "seller/dispatch label on the listing page",
            }
        if idx + 1 < len(lines):
            nxt = lines[idx + 1].text.strip()
            if looks_like_company_entity(nxt):
                return {
                    "display_value": nxt[:160],
                    "confidence": 0.7,
                    "source_text": f"{line.text} / {nxt}",
                    "method": "entity on the line following the seller/dispatch label",
                }
    return None


def extract_listing(text: str, *, source: str = "MANUAL_TEXT") -> dict:
    """Extract declaration and listing fields from listing text.

    Returns the package-equivalent declaration fields (with provenance) plus the three listing-only
    concepts. Never invents a value: an absent declaration is simply absent from the payload.
    """
    lines = lines_from_text(text or "")
    if not lines:
        return {
            "engine": LISTING_ENGINE,
            "source": source,
            "line_count": 0,
            "fields": {},
            "listing_only": {},
            "notes": ["The listing text was empty, so nothing could be extracted."],
        }

    best = _best_candidates(lines)
    fields: dict[str, dict] = {}
    unanchored_price: Candidate | None = None
    for name, candidate in best.items():
        # A price shown on a listing page is not automatically the MRP declaration. The package
        # extractor deliberately offers a keyword-less currency amount as a WEAK mrp candidate;
        # on a listing that amount is recorded under listing_price instead, and the MRP field stays
        # empty unless an MRP keyword was actually read. (A listing price can be a discounted or
        # negotiated price — recording it as the retail sale price would fabricate a declaration.)
        if name == "mrp" and UNANCHORED_REASON_MARKER in (candidate.reason or ""):
            unanchored_price = candidate
            continue
        payload: dict = {
            "display_value": _display_for(name, candidate),
            "normalized_value": (candidate.value or "").strip()[:400],
            "raw_value": (candidate.raw_value or "").strip()[:400],
            "confidence": round(float(candidate.confidence or 0.0), 3),
            "source_text": (candidate.source_text or "").strip()[:400],
            "method": candidate.reason or "listing text extraction",
        }
        if name == "net_quantity":
            try:
                q = json.loads(candidate.value or "{}")
                payload["display_value"] = f"{q.get('value')} {q.get('unit')}".strip()
                payload["quantity"] = q.get("value")
                payload["unit"] = q.get("unit")
                payload["quantity_type"] = q.get("quantity_type")
            except (json.JSONDecodeError, TypeError):
                pass
        if candidate.inferred:
            payload["inferred"] = True
        if candidate.role_uncertain:
            payload["role_uncertain"] = True
        fields[name] = payload

    listing_only: dict[str, dict] = {}
    if (title := _extract_title(lines)) is not None:
        listing_only["listing_title"] = title
    if (price := _extract_price(lines)) is not None:
        listing_only["listing_price"] = price
    elif unanchored_price is not None:
        listing_only["listing_price"] = {
            "display_value": (unanchored_price.raw_value or "")[:160],
            "normalized_value": unanchored_price.value,
            "confidence": round(float(unanchored_price.confidence or 0.0), 3),
            "source_text": unanchored_price.source_text,
            "method": "currency amount printed without an MRP declaration",
            "inferred": True,
        }
    if (seller := _extract_seller(lines)) is not None:
        listing_only["listing_seller"] = seller
        # A listing prints the SELLER ("Sold by: …"); the declaration the law requires is the
        # manufacturer / packer / IMPORTER, and a marketplace seller is frequently none of those.
        # An entity read from the seller line is therefore kept as the seller — it is already in
        # listing_only — and is not also offered as a manufacturer declaration, which would assert
        # a role the page never printed. A manufacturer named behind its own anchor ("Mfd. by: …")
        # is unaffected: that IS a declaration of the role.
        manufacturer = fields.get("manufacturer")
        if manufacturer and manufacturer.get("role_uncertain"):
            seller_text = (seller.get("display_value") or "").strip().lower()
            mfr_text = (manufacturer.get("display_value") or "").strip().lower()
            if seller_text and (seller_text in mfr_text or mfr_text in seller_text):
                fields.pop("manufacturer")

    return {
        "engine": LISTING_ENGINE,
        "source": source,
        "line_count": len(lines),
        "fields": fields,
        "listing_only": listing_only,
        "notes": [],
    }
