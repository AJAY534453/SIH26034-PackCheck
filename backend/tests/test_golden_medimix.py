"""Golden regression: the Medimix Ayurvedic Soap pack (front + back of one retail pack).

Provenance of the fixture: the Medimix image files are NOT in this repository, so
``fixtures/golden_medimix_ocr.json`` carries the OCR lines of the pack's two photographed
surfaces. If the photographs are added later, replace that JSON with the OCR dump of the same
two surfaces — nothing in this module (or in production code) needs to change.

Every assertion is SEMANTIC. The case exists because a cosmetic (toilet soap) pack is where the
pipeline was actually wrong:

  * 'MADE IN INDIA' was published as the PRODUCT NAME, and the brand inference then offered its
    first token, 'MADE', as the brand of the pack;
  * the manufacturer line 'Mfd. by: AVA CHOLAYIL ...' produced NO entity at all, because the
    extractor's role pattern lacked the dotted 'Mfd. by' form that the field schema declares;
  * 'MADE IN INDIA' produced no country-of-origin candidate either, for the same reason;
  * the combined 'Mfg. & Use Before: 04/26 & 03/28' line reported the USE-BEFORE date as the
    MANUFACTURING date;
  * the manufacturer anchor took its value from a line belonging to a different declaration;
  * 'PACK COMPOSITION: 4 X 150 G + 1 X 150 G FREE' and 'FOR EXTERNAL USE ONLY' were read as
    product titles (the second published 'FOR' as the brand);
  * the classifier scored FOOD from substring hits ('rice' inside 'Price', 'oil' inside 'TOILET')
    and called a soap a CONFLICTING food product.

None of these may be implemented as a product-specific shortcut in production code.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.classification import classify_from_ocr
from backend.extraction.engine import pick_winner, run_extraction
from backend.ocr.base import OcrLine

FIXTURE = Path(__file__).parent / "fixtures" / "golden_medimix_ocr.json"


@pytest.fixture(scope="module")
def medimix():
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
    out = run_extraction(by_image)
    all_text = " ".join(item["text"] for item in data["ocr_lines"])
    return out, data, all_text


def _winner(out, field):
    winner, _ = pick_winner(out.get(field, []))
    return winner


def _value(out, field) -> str:
    winner = _winner(out, field)
    return (winner.value if winner else "") or ""


# ------------------------------------------------------------------ identity


def test_brand_is_the_wordmark_not_a_title_word(medimix):
    out, _, _ = medimix
    brand = _winner(out, "brand")
    assert brand is not None, "the MEDIMIX wordmark was not read as a brand mark"
    assert brand.value.strip().upper() == "MEDIMIX"
    # A wordmark is typography, not a labelled declaration: it must be offered as INFERRED.
    assert brand.inferred is True


def test_product_name_is_the_title_not_a_country_or_composition_line(medimix):
    out, _, _ = medimix
    name = _value(out, "product_name")
    assert "AYURVEDIC" in name.upper()
    assert "MADE IN INDIA" not in name.upper()
    assert "PACK COMPOSITION" not in name.upper()
    assert "EXTERNAL" not in name.upper()


def test_classification_is_a_cosmetic_and_never_a_food_product(medimix):
    _, data, all_text = medimix
    result = classify_from_ocr(all_text)
    assert result["category"] == data["classification"]["expect_category"]
    assert result["category"] not in data["classification"]["must_not_be"]
    # A conflicting/uncertain classification is what would drag a soap into food-specific demands,
    # so the category has to be positively evidenced, not guessed.
    assert result["state"] == "DETECTED"


def test_no_food_specific_declaration_is_demanded_of_a_soap(medimix):
    _, _, all_text = medimix
    result = classify_from_ocr(all_text)
    assert result["category"] != "FOOD"
    assert "fssai" not in all_text.lower(), "the fixture must not carry a food licence declaration"


# ------------------------------------------------------------------ declarations


def test_net_quantity_is_the_declared_total_not_the_pack_composition(medimix):
    out, data, _ = medimix
    norm = json.loads(_value(out, "net_quantity"))
    assert norm["value"] == data["expected_fields"]["net_quantity"]["value"]
    assert norm["unit"] == "g"
    assert norm["value"] not in data["expected_fields"]["net_quantity"]["must_not_be"]


def test_unit_sale_price_is_never_published_as_the_mrp(medimix):
    out, data, _ = medimix
    assert _value(out, "mrp").startswith("220")
    assert _value(out, "mrp") not in data["expected_fields"]["mrp"]["must_not_be"]
    assert "0.29" in _value(out, "unit_sale_price")


def test_combined_mfg_and_use_before_line_is_not_cross_wired(medimix):
    out, data, _ = medimix
    assert _value(out, "date_manufacturing") == data["expected_fields"]["date_manufacturing"]["value"]
    assert _value(out, "date_expiry") == data["expected_fields"]["date_expiry"]["value"]
    # The use-before date must never be reported as the manufacturing date.
    assert _value(out, "date_manufacturing") not in data["expected_fields"]["date_expiry"]["must_not_be"][:0] + [
        "2028-03"
    ]
    # Only a USE-BEFORE declaration was printed: no expiry date may be invented from nothing.
    assert _value(out, "date_best_before") == ""


def test_manufacturer_is_the_entity_after_the_anchor_with_its_address(medimix):
    out, data, _ = medimix
    manufacturer = _value(out, "manufacturer")
    assert data["expected_fields"]["manufacturer"]["value_contains"] in manufacturer.upper()
    assert "AYURVEDIC" not in manufacturer.upper()
    assert _value(out, "manufacturer_address")


def test_country_of_origin_and_consumer_care_are_read_from_their_printed_wording(medimix):
    out, _, _ = medimix
    assert "INDIA" in _value(out, "country_of_origin").upper()
    assert "18001031282" in _value(out, "consumer_care_phone")
    assert "avacare.in" in _value(out, "consumer_care_email")
    assert "mymedimix" in _value(out, "website")
    assert _value(out, "batch_lot") == "B2404A"


def test_no_expected_declaration_is_silently_absent(medimix):
    out, data, _ = medimix
    missing = [field for field in data["never_auto_absent"] if _winner(out, field) is None]
    assert not missing, f"declarations that were present in evidence came back empty: {missing}"


def test_no_ocr_noise_becomes_a_field_value(medimix):
    """Lines that are not declarations must not be published as any field's value.

    Two of the fixture's lines are instruction/pack-size copy; the third is a genuine address line,
    which belongs to ``manufacturer_address`` (that is its declaration) and to nothing else.
    """
    out, _, _ = medimix
    noise = {"for external use only", "pack composition: 4 x 150 g + 1 x 150 g free"}
    address_only = "41, poonamallee high road, arumbakkam, chennai - 600 106"
    for field, candidates in out.items():
        for candidate in candidates:
            value = (candidate.value or "").strip().lower()
            assert value not in noise, f"OCR noise published as {field}: {value!r}"
            if value == address_only:
                assert field.endswith("_address"), f"an address line became {field!r}"
    # The entity declaration itself also carries its printed address — nothing is removed from it.
    assert address_only.upper() in _value(out, "manufacturer").upper()
