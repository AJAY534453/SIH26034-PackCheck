"""Golden regression: Britannia-shaped biscuit label (second real-photo test case).

The failures this locks down were observed on the real package:
  * the manufacturer entity was reported as the PRODUCT NAME;
  * the composite quantity was truncated to its first number;
  * the alphanumeric batch code had its letters rewritten into digits.

Every assertion is SEMANTIC (declaration type, entity separation, whole-declaration quantity,
label→date association). Nothing here may be implemented as a product-specific shortcut in
production code — the no-hardcoding guard covers that separately.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.extraction.engine import pick_winner, run_extraction
from backend.ocr.base import OcrLine

FIXTURE = Path(__file__).parent / "fixtures" / "golden_britannia_ocr.json"


@pytest.fixture(scope="module")
def britannia_out():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    lines = [
        OcrLine(
            text=item["text"],
            confidence=item["confidence"],
            bbox=tuple(item["bbox"]),
            engine="rapidocr",
            variant=item["variant"],
        )
        for item in data["ocr_lines"]
    ]
    return run_extraction({1: lines}), data["expected_fields"]


def _winner(out, field):
    return pick_winner(out.get(field, []))[0]


def test_product_name_is_the_product_not_the_manufacturer_entity(britannia_out):
    out, _ = britannia_out
    winner = _winner(out, "product_name")
    assert winner is not None, "product name not detected at all"
    assert "Classic Sweet & Salty" in winner.value
    assert "Industries" not in winner.value, (
        "the manufacturer entity must never become the product name"
    )


def test_brand_is_separated_from_product_name(britannia_out):
    out, _ = britannia_out
    brand = _winner(out, "brand")
    product = _winner(out, "product_name")
    assert brand is not None and product is not None
    assert brand.value.strip().upper() == "BRITANNIA"
    assert brand.value.strip().lower() not in product.value.strip().lower()
    # A logo wordmark is typography, not a labelled declaration: it stays reviewable.
    assert brand.inferred is True


def test_manufacturer_entity_and_address_are_both_recovered(britannia_out):
    out, _ = britannia_out
    manufacturer = _winner(out, "manufacturer")
    assert manufacturer is not None
    assert "Britannia Industries" in manufacturer.value
    address = _winner(out, "manufacturer_address")
    assert address is not None, "the address block following the entity was discarded"
    assert "Bengaluru" in address.value or "560048" in address.value


def test_composite_quantity_is_reported_whole(britannia_out):
    out, expected = britannia_out
    quantity = _winner(out, "net_quantity")
    data = json.loads(quantity.value)
    spec = expected["net_quantity"]
    assert data["value"] == spec["value"], data
    assert data["unit"] == spec["unit"]
    assert spec["declared_as"] in data.get("expression", "")
    assert data.get("extra_value") == "6.1"
    assert data.get("declared_total") == "34.5"


def test_alphanumeric_batch_code_keeps_its_letters(britannia_out):
    out, expected = britannia_out
    batch = _winner(out, "batch_lot")
    assert batch is not None
    assert batch.value == expected["batch_lot"]["value"], (
        f"letters in a batch code must never be rewritten as digits (got {batch.value!r})"
    )
    assert "L" in batch.value and "807269" not in batch.value


def test_dates_are_associated_with_their_own_labels(britannia_out):
    out, _ = britannia_out
    packing = _winner(out, "date_packing")
    expiry = _winner(out, "date_expiry")
    assert packing is not None and packing.value.startswith("2026-07-20")
    assert expiry is not None and expiry.value.startswith("2027-01-19")
    # the two declarations must not be conflated
    assert packing.value != expiry.value


def test_identifiers_and_contact_channels(britannia_out):
    out, expected = britannia_out
    for field, spec in expected.items():
        if field in ("product_name", "net_quantity", "batch_lot", "date_packing", "date_expiry"):
            continue
        winner = _winner(out, field)
        assert winner is not None, f"{field} not detected"
        needle = spec.get("value_contains") or spec.get("value")
        if needle:
            assert needle.lower() in str(winner.value).lower(), (field, winner.value)


def test_mrp_is_the_declared_retail_price_not_a_unit_price(britannia_out):
    out, _ = britannia_out
    mrp = _winner(out, "mrp")
    assert mrp is not None and mrp.value.startswith("5.00")
    assert "Inclusive" not in mrp.value
