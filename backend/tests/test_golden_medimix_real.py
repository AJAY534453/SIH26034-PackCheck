"""Golden regression on the REAL OCR of the photographed Medimix pack (three surfaces).

``fixtures/golden_medimix_real_ocr.json`` is the recogniser output retained in the application's
own evidence store, verbatim: fused word boundaries, misread prices, a PIN code fused into a licence
line. The sibling fixture (``golden_medimix_ocr.json``) carries clean text and proves the pipeline
reads a tidy label; this one proves it stays honest on an untidy one:

  * the fused title is repaired for display and no character is invented;
  * the printed RELATIONSHIP INSTRUCTION ('For Mfd. Unit Address, please refer to the first
    character of the Batch No.') never becomes the product name or the brand;
  * the brand is the pack's wordmark, not a product word and not the offer burst ('FREE!');
  * a 6-digit PIN code is not published as a 561203-metre quantity;
  * a misread price stays NOT DETECTED instead of being published as an invented figure;
  * the country of origin is found even where the print is fused ('…AUS-579MADEININDIA');
  * the pack is never classified as FOOD.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.classification import classify_from_ocr
from backend.extraction.engine import pick_winner, run_extraction
from backend.ocr.base import OcrLine

FIXTURE = Path(__file__).parent / "fixtures" / "golden_medimix_real_ocr.json"


@pytest.fixture(scope="module")
def pack():
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


def _value(out, field) -> str:
    winner, _ = pick_winner(out.get(field, []))
    return (winner.value if winner else "") or ""


def _candidate(out, field):
    winner, _ = pick_winner(out.get(field, []))
    return winner


# ----------------------------------------------------------------- identity


def test_fused_title_is_repaired_without_inventing_characters(pack):
    out, data, _ = pack
    name = _value(out, "product_name")
    assert name == data["expected_fields"]["product_name"]["value"]
    # every character of the reading survived: only separators were added
    assert name.replace(" ", "") == "AYURVEDICSOAPWITH18HERBS"


def test_the_relationship_instruction_is_never_a_title_or_a_brand(pack):
    out, data, _ = pack
    instruction = data["must_not_be_product_name"][0]
    for field, candidates in out.items():
        for candidate in candidates:
            assert (candidate.value or "").strip() != instruction, f"instruction published as {field}"
    assert _value(out, "product_name") not in data["must_not_be_product_name"]
    assert _value(out, "brand") not in data["must_not_be_brand"]


def test_the_brand_is_the_wordmark_not_a_product_word_or_an_offer(pack):
    out, _, _ = pack
    brand = _candidate(out, "brand")
    assert brand is not None, "no brand candidate at all"
    value = brand.value.strip().upper()
    assert value.startswith("MEDIMI"), f"brand read as {value!r}"
    assert value not in {"FREE", "PACK", "SUPERSAVER", "AYURVEDIC"}
    # a wordmark is typography, never a labelled declaration
    assert brand.inferred is True


def test_manufacturer_entity_is_the_entity_and_its_address_is_separate(pack):
    out, data, _ = pack
    manufacturer = _value(out, "manufacturer")
    assert data["expected_fields"]["manufacturer"]["value_contains"] in manufacturer
    assert data["expected_fields"]["manufacturer"]["must_not_contain"].lower() not in manufacturer.lower()
    # the licence label that introduces the entity is a label, not part of the name
    assert not manufacturer.lower().startswith("licensed")
    address = _value(out, "manufacturer_address")
    assert data["expected_fields"]["manufacturer_address"]["value_contains"] in address


# ----------------------------------------------------------------- values that must NOT be invented


def test_a_pin_code_is_not_published_as_a_quantity(pack):
    out, _, _ = pack
    quantity = _value(out, "net_quantity")
    assert "561203" not in quantity
    assert "750" in quantity and '"g"' in quantity


def test_a_misread_price_is_reported_as_not_detected_not_invented(pack):
    out, _, _ = pack
    # the pack's price line was recognised as 'F280.00' — unusable, and 280 is not a printed MRP
    assert _value(out, "mrp") == ""
    assert _value(out, "unit_sale_price") == ""


def test_fused_country_of_origin_is_still_read(pack):
    out, data, _ = pack
    assert "India" in _value(out, "country_of_origin")
    assert _value(out, "consumer_care_phone") == data["expected_fields"]["consumer_care_phone"]["value_contains"]
    assert data["expected_fields"]["consumer_care_email"]["value_contains"] in _value(out, "consumer_care_email")
    assert data["expected_fields"]["website"]["value_contains"] in _value(out, "website")


# ----------------------------------------------------------------- classification


def test_an_untidy_soap_is_never_classified_as_food(pack):
    _, data, all_text = pack
    result = classify_from_ocr(all_text)
    assert result["category"] != "FOOD", "a fused toiletry label must not become a food product"
    # With this much fusion the honest outcome may be an uncertain classification — which is
    # exactly why unrelated (food-specific) requirements must not be activated from it.
    assert result["category"] in {"COSMETIC", "OTHER"}
