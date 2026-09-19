"""Extraction engine: runs field extractors over OCR lines from all images/variants.

Two-run architecture for OCR robustness:
  RUN 1 (authoritative): extractors over raw OCR text.
  RUN 2 (numeric-recovery): extractors over a numerically-corrected VIEW of the text
      (O→0, I→1, S→5 ...) — used ONLY for numeric-context fields (MRP, quantity, dates,
      FSSAI, phone). Every candidate from this run keeps the ORIGINAL raw text as
      raw_value/source_text and records an explicit correction explanation in `reason`.
      Words are never touched, so no manufacturer/product name can be corrupted.

Candidate scoring is per-field; conflicts (multiple distinct values with material support)
are flagged — never silently resolved. The winner is the highest-scoring candidate; ties
with material opposition are marked CONFLICTING for human review.
"""
from __future__ import annotations

from backend.extraction.base import (
    IDENTIFIER_ANCHOR_RE,
    Candidate,
    lines_sorted,
    nutrition_line_flags,
)
from backend.extraction.dates import extract_dates
from backend.extraction.text_repair import repair_ocr_spacing
from backend.extraction.identifiers import (
    extract_batch_lot,
    extract_contacts,
    extract_fssai,
    extract_website,
)
from backend.extraction.manufacturer import extract_country_of_origin, extract_manufacturer
from backend.extraction.mrp import extract_mrp, extract_unit_sale_price
from backend.extraction.product_identity import (
    cross_panel_brand_candidates,
    extract_product_identity,
)
from backend.extraction.quantity import extract_net_quantity
from backend.ocr.base import OcrLine
from backend.ocr.textnorm import explain_correction, normalize_numeric_text

EXTRACTORS = [
    extract_mrp,
    extract_unit_sale_price,
    extract_net_quantity,
    extract_dates,
    extract_batch_lot,
    extract_fssai,
    extract_manufacturer,
    extract_country_of_origin,
    extract_contacts,
    extract_website,
    extract_product_identity,
]

# Fields eligible for the numeric-recovery run. Text fields (manufacturer, product name,
# brand, website, addresses) are NEVER corrected — a corrected 'ABO' would be fabrication.
NUMERIC_RECOVERY_FIELDS = {
    "mrp", "net_quantity", "date_manufacturing", "date_packing", "date_import",
    "date_expiry", "date_best_before", "fssai_license", "consumer_care_phone", "batch_lot",
}


def _augment_reason(candidate: Candidate, ledger: list[dict], original_text: str) -> Candidate:
    """Attach correction provenance to a candidate produced from a corrected view."""
    candidate.raw_value = original_text
    candidate.source_text = original_text
    candidate.reason = (
        f"{candidate.reason}; {explain_correction(ledger)}"
        if ledger
        else candidate.reason
    )
    return candidate


def _is_corrected_candidate(c: Candidate) -> bool:
    return "OCR character correction" in (c.reason or "")


def run_extraction(lines_by_image: dict[int, list[OcrLine]]) -> dict[str, list[Candidate]]:
    """Run every extractor over all lines. Returns field_name -> candidates (deduped, scored)."""
    per_field: dict[str, list[Candidate]] = {}
    for image_id, lines in lines_by_image.items():
        if not lines:
            continue
        # One canonical (reading) order for the whole image: nutrition flags AND every
        # extractor must agree on indexes. Extractors re-sort internally (a no-op on
        # already-sorted input); computing flags on a DIFFERENT order silently misaligns
        # flags[i] onto the wrong line and poisons extraction (e.g. a Customer Care line
        # flagged as a nutrition-table remnant).
        lines = lines_sorted(lines)
        flags = nutrition_line_flags(lines)
        # RUN 1: authoritative extraction on raw text.
        for extractor in EXTRACTORS:
            try:
                candidates = extractor(lines, flags)
            except Exception:
                continue
            for c in candidates:
                c.source_image_id = c.source_image_id or image_id
                per_field.setdefault(c.field_name, []).append(c)

        # RUN 2: numeric-recovery pass. Only numeric-context fields, only on lines where a
        # correction ledger exists, and only candidates that DID NOT already appear from raw
        # text (i.e. the raw-text run found nothing usable for that line/field).
        corrected_map: dict[int, tuple[OcrLine, list[dict], str]] = {}
        for i, line in enumerate(lines):
            identifier_line = bool(IDENTIFIER_ANCHOR_RE.search(line.text))
            corrected, ledger = normalize_numeric_text(line.text, identifier_context=identifier_line)
            if ledger:
                corrected_map[i] = (
                    OcrLine(text=corrected, confidence=line.confidence, bbox=line.bbox,
                            engine=line.engine, variant=line.variant),
                    ledger,
                    line.text,
                )
        if corrected_map:
            corrected_lines = [corrected_map[i][0] if i in corrected_map else l for i, l in enumerate(lines)]
            corrected_flags = nutrition_line_flags(corrected_lines)
            raw_pairs = {(l.text, l.bbox) for l in lines}
            for extractor in EXTRACTORS:
                try:
                    candidates = extractor(corrected_lines, corrected_flags)
                except Exception:
                    continue
                for c in candidates:
                    if c.field_name not in NUMERIC_RECOVERY_FIELDS:
                        continue
                    if (c.raw_value, c.bbox) in raw_pairs:
                        continue  # raw-text run already produced this exact candidate
                    # map back to the original line for provenance
                    matched = None
                    for i, (cl, ledger, orig) in corrected_map.items():
                        if cl.text == c.raw_value and cl.bbox == c.bbox:
                            matched = (ledger, orig)
                            break
                    if matched is None:
                        continue
                    ledger, orig = matched
                    _augment_reason(c, ledger, orig)
                    c.source_image_id = c.source_image_id or image_id
                    per_field.setdefault(c.field_name, []).append(c)

    # ---- cross-panel evidence fusion -------------------------------------------------------
    # A brand mark read on one panel is stronger when the same token also appears in a printed
    # declaration on ANOTHER panel of the package. Only the full set of panels can show that, so
    # it runs once, after every panel has been through RUN 1 and RUN 2 — never inside the
    # per-image loop.
    try:
        for c in cross_panel_brand_candidates(lines_by_image):
            bucket = per_field.setdefault(c.field_name, [])
            # The same reading may already have been offered by the per-image pass; a duplicate
            # would only clutter the evidence list.
            already = any(
                (e.value or "").strip().lower() == (c.value or "").strip().lower()
                and e.source_image_id == c.source_image_id
                for e in bucket
            )
            if not already:
                bucket.append(c)
    except Exception:
        pass  # fusion is an enhancement: it must never take the extraction down

    # dedupe identical (field, value, bbox) candidates. Additionally, for numeric-recovery
    # fields, a corrected-run candidate supersedes a raw-run candidate for the SAME evidence
    # region (same field + bbox): the corrected view is a strictly better reading of that
    # region (e.g. '₹2' mis-parse vs '₹200.00'), and the raw text is preserved in
    # raw_value/source_text either way.
    for field, cands in per_field.items():
        seen: set[tuple[str, str]] = set()
        unique: list[Candidate] = []
        corrected_bboxes = {
            str(c.bbox) for c in cands if _is_corrected_candidate(c)
        } if field in NUMERIC_RECOVERY_FIELDS else set()
        for c in cands:
            key = (c.value, str(c.bbox))
            if key in seen:
                continue
            if (
                field in NUMERIC_RECOVERY_FIELDS
                and not _is_corrected_candidate(c)
                and str(c.bbox) in corrected_bboxes
            ):
                continue  # superseded by the corrected reading of the same region
            seen.add(key)
            unique.append(c)
        per_field[field] = sorted(unique, key=lambda c: c.score, reverse=True)

    # ---------- identity collision: one printed word is not both brand and product title ----------
    # A panel whose only title-shaped line is the wordmark yields that word as the product name,
    # while the logo detector independently reads the same word as the brand mark on another
    # panel. The same printed string cannot be asserted as two different declarations: the winning
    # BRAND reading stands (it is corroborated by the logo band and by repetition) and that string
    # is dropped as a product name, so the title is reported as not detected rather than
    # duplicated. Only the brand WINNER is compared — other marks in the logo band are separate
    # candidates (a product wordmark is not disqualified because another mark was also read).
    brand_winner, _ = pick_winner(per_field.get("brand", []))
    if brand_winner is not None and (brand_winner.value or "").strip():
        brand_key = _identity_key(brand_winner.value)
        per_field["product_name"] = [
            c for c in per_field.get("product_name", [])
            if _identity_key(c.value) != brand_key
        ]

    # ---------- display repair: word boundaries lost by the recogniser ----------
    # On real packaging OCR returns fused runs ('AYURVEDIC SOAPWITH18HERBS',
    # 'BRITANNIA INDUSTRIESLTD.', 'Mfd.By'). The READING is faithful — only the separators were
    # lost. For identity/entity DISPLAY values the separators are restored from a generic packaging
    # vocabulary, and only when the run decomposes completely (see extraction.text_repair). The raw
    # OCR text stays on raw_value/source_text untouched, and the repair is stated in the reason.
    for field_name in SPACING_REPAIR_FIELDS:
        for c in per_field.get(field_name, []):
            repaired = repair_ocr_spacing(c.value)
            if repaired != (c.value or ""):
                c.value = repaired
                c.reason = ((c.reason + " | ") if c.reason else "") + (
                    "word boundaries restored for display from OCR-fused text "
                    "(raw OCR text preserved on the candidate)"
                )
    return per_field


#: Fields whose display value is repaired for lost word boundaries. Declaration VALUES that must
#: be parsed (quantities, prices, dates, codes) are deliberately excluded: inserting a separator
#: there could change a parsed figure.
SPACING_REPAIR_FIELDS = frozenset(
    {
        "product_name",
        "brand",
        "common_name",
        "manufacturer",
        "packer",
        "importer",
        "marketer",
        "manufacturer_address",
        "packer_address",
        "importer_address",
    }
)


def _identity_key(value: str | None) -> str:
    """Case/space/punctuation-insensitive key for cross-field identity comparison."""
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _canon_number(value: str) -> str | None:
    """Canonical form for pure-numeric values so 200 and 200.00 agree; None if not numeric."""
    try:
        return str(float(value.replace(",", "")))
    except (ValueError, AttributeError):
        return None


def _values_agree(a: str, b: str) -> bool:
    """Values agree if equal, or numerically equal (200 == 200.00), or same modulo format."""
    if a == b:
        return True
    na, nb = _canon_number(a), _canon_number(b)
    return na is not None and na == nb


def pick_winner(cands: list[Candidate]) -> tuple[Candidate | None, bool]:
    """Pick the winning candidate and whether a material conflict exists.

    Conflict policy: if a materially different value exists whose score is within 85% of the
    winner's, the field is CONFLICTING — never silently choose one. Numeric formatting
    differences (200 vs 200.00) are agreement, not conflict. Values in different scripts
    (e.g. English + Tamil product names) are duplicate multilingual evidence, NOT conflicts.
    """
    from backend.ocr.multilingual import is_multilingual_duplicate

    if not cands:
        return None, False
    ordered = sorted(cands, key=lambda c: c.score, reverse=True)
    winner = ordered[0]
    conflict = False
    for other in ordered[1:]:
        if _values_agree(other.value, winner.value):
            continue
        # multilingual duplicate declarations are reinforcement, not conflict
        if is_multilingual_duplicate(
            winner.field_name,
            winner.value or winner.raw_value,
            other.value or other.raw_value,
        ):
            continue
        if other.score >= 0.85 * winner.score and other.score > 0.3:
            conflict = True
            break
    return winner, conflict
