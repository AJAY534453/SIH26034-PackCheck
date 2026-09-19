"""Golden regression: the REAL multi-view package photo set (3 surfaces of one pack).

This is the only fixture built from an actual uploaded photo set rather than a rendered label,
and it is the case that exposed the remaining real-world defects:

  * the company entity line ('BRITANNIA INDUSTRIESLTD.' — company words fused by OCR) was
    reported as the PRODUCT NAME;
  * the PRODUCT TITLE was printed on stacked lines ('CLASSIC' / 'SWEET' / '&SALTY') interrupted
    by a promo tag, so no single line scored as a title;
  * the two-column date block was cross-wired: 'PKD.' took the date printed beside 'USE BY';
  * the composite net quantity was truncated to its first number because the printed total lost
    its unit ('28.4g+6.1gEXTRA-34');
  * an ordinary word ('OTHER') beside 'LOT No.' was published as the batch code while the real
    alphanumeric code sat in the value column one row above.

Every assertion is SEMANTIC (declaration type, entity separation, whole quantity declarations,
label→value association, identifier shape). Nothing here may be implemented as a product-specific
shortcut in production code — the no-hardcoding guard covers that separately.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.extraction.engine import pick_winner, run_extraction
from backend.normalization.service import normalize_field
from backend.ocr.base import OcrLine

FIXTURE = Path(__file__).parent / "fixtures" / "golden_real_multiview_ocr.json"


@pytest.fixture(scope="module")
def real_out():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    by_image: dict[int, list[OcrLine]] = {}
    for item in data["ocr_lines"]:
        by_image.setdefault(item["image"], []).append(
            OcrLine(
                text=item["text"],
                confidence=item["confidence"],
                bbox=tuple(item["bbox"]),
                engine="rapidocr",
                variant=item["variant"],
            )
        )
    return run_extraction(by_image), data["expected_fields"]


def _winner(out, field):
    return pick_winner(out.get(field, []))[0]


def test_company_entity_never_becomes_the_product_name(real_out):
    out, expected = real_out
    winner = _winner(out, "product_name")
    assert winner is not None, "the stacked product title was not recovered"
    assert "Classic" in winner.value.title() or "CLASSIC" in winner.value.upper()
    assert "Industries" not in winner.value.title(), (
        "the company entity must never become the product name"
    )
    assert expected["product_name"]["value"].replace(" ", "") in winner.value.replace(" ", "")


def test_stacked_title_is_read_as_one_block(real_out):
    out, _ = real_out
    winner = _winner(out, "product_name")
    # the printed lines are merged, not published as a single fragment
    assert len(winner.value.split()) >= 3
    assert "SWEET" in winner.value.upper() and "SALTY" in winner.value.upper()
    # ...and the merged block's evidence spans the printed block
    assert winner.bbox[3] - winner.bbox[1] > 100


def test_wordmark_is_the_brand_and_is_offered_as_inferred(real_out):
    out, _ = real_out
    brand = _winner(out, "brand")
    product = _winner(out, "product_name")
    assert brand is not None and brand.value.strip().upper() == "BRITANNIA"
    assert brand.inferred is True, "a logo wordmark is typography, not a labelled declaration"
    # one printed word is never asserted as both brand and product title
    assert brand.value.strip().lower() not in product.value.strip().lower()


def test_unanchored_company_entity_is_recovered_with_its_role_unresolved(real_out):
    out, expected = real_out
    manufacturer = _winner(out, "manufacturer")
    assert manufacturer is not None, "the company entity line was dropped entirely"
    assert "BRITANNIA INDUSTRIES" in manufacturer.value.upper().replace(" ", " ")
    assert manufacturer.role_uncertain is True, (
        "no role anchor was read, so which legal role the entity fills must stay open"
    )
    assert "role" in manufacturer.reason.lower()


def test_composite_quantity_is_reported_whole_when_the_printed_total_lost_its_unit(real_out):
    out, expected = real_out
    quantity = _winner(out, "net_quantity")
    data = json.loads(quantity.value)
    assert data["value"] == expected["net_quantity"]["value"], data
    assert data["unit"] == expected["net_quantity"]["unit"]
    # the printed declaration is preserved verbatim, including the truncated total
    assert "28.4g+6.1gEXTRA-34" in data.get("expression", "")
    assert data.get("base_value") == "28.4"
    assert data.get("total_discrepancy") is True, (
        "a printed total that disagrees with the printed parts must be flagged, not published"
    )
    assert quantity.inferred is True
    # and the display states the mismatch for the inspector
    display = normalize_field("net_quantity", quantity.value)["display"]
    assert "34.5" in display and "declared" in display.lower()


def test_batch_code_is_the_identifier_not_the_adjacent_word(real_out):
    out, expected = real_out
    batch = _winner(out, "batch_lot")
    assert batch is not None
    assert batch.value == expected["batch_lot"]["value"], (
        f"expected the alphanumeric code beside the label, got {batch.value!r}"
    )
    assert "OTHER" not in batch.value.upper()
    assert "L" in batch.value  # letters in an identifier are never rewritten as digits


def test_packing_and_use_by_dates_are_not_cross_wired(real_out):
    out, expected = real_out
    packing = _winner(out, "date_packing")
    expiry = _winner(out, "date_expiry")
    assert packing is not None and packing.value.startswith(expected["date_packing"]["value"])
    assert expiry is not None and expiry.value.startswith(expected["date_expiry"]["value"]), (
        "the use-by label was left unresolved while its date sat in the same block"
    )
    assert packing.value != expiry.value
    # the re-association is explained, not silent
    assert "re-associated" in packing.reason or "re-associated" in expiry.reason


def test_identifiers_are_still_recovered_from_the_real_photo(real_out):
    out, expected = real_out
    for field in ("fssai_license", "consumer_care_email"):
        winner = _winner(out, field)
        assert winner is not None, f"{field} not detected"
        assert expected[field]["value_contains"].lower() in str(winner.value).lower()
