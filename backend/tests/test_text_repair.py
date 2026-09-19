"""OCR word-boundary repair: separators may be restored, characters may never be invented.

The invariant that matters legally: the repair can only ever INSERT separators. Stripping the
inserted separators must reproduce the original reading exactly, and a run that does not decompose
into real packaging words must come back untouched.
"""
from __future__ import annotations

import pytest

from backend.extraction.text_repair import repair_ocr_spacing


def _without_inserted_spaces(original: str, repaired: str) -> str:
    return repaired.replace(" ", "")


@pytest.mark.parametrize(
    "original,expected",
    [
        # a real fused title from the Medimix pack's front carton
        ("AYURVEDIC SOAPWITH18HERBS", "AYURVEDIC SOAP WITH 18 HERBS"),
        # a fused company suffix
        ("BRITANNIA INDUSTRIESLTD.", "BRITANNIA INDUSTRIES LTD."),
        # a fused declaration with its article
        ("ANAYURVEDICPROPRIETARYMEDICINE", "AN AYURVEDIC PROPRIETARY MEDICINE"),
        # a fused abbreviation keeps its dot and gains a space
        ("Mfd.By:Licensed TM Users", "Mfd. By:Licensed TM Users"),
        ("Pvt.Ltd.", "Pvt. Ltd."),
    ],
)
def test_fused_runs_are_split_for_display(original, expected):
    assert repair_ocr_spacing(original) == expected
    # the repair inserted separators only — the characters are unchanged
    assert _without_inserted_spaces(original, repair_ocr_spacing(original)) == original.replace(" ", "")


@pytest.mark.parametrize(
    "value",
    [
        "MEDIMIX",           # a single genuine word
        "AvA Cholayil",      # mixed case inside a word is not a boundary
        "AJAY-ACER",
        "ANALGESIC",         # would need a piece that is not packaging vocabulary
        "PALMOILCOCONUTOIL",
        "Pricelist",
    ],
)
def test_a_genuine_word_is_never_split(value):
    assert repair_ocr_spacing(value) == value


def test_quantity_bearing_values_are_left_for_their_own_extractors():
    """The repair is applied to identity/entity DISPLAY values only — never to parsed figures.

    The engine excludes quantity/price/date fields, and this test pins the reason: the same repair
    inserts a space between digits and letters, which is harmless for a name and must not be allowed
    to move a parsed number.
    """
    from backend.extraction.engine import SPACING_REPAIR_FIELDS

    assert "product_name" in SPACING_REPAIR_FIELDS and "manufacturer" in SPACING_REPAIR_FIELDS
    for parsed in ("net_quantity", "mrp", "unit_sale_price", "date_manufacturing", "batch_lot"):
        assert parsed not in SPACING_REPAIR_FIELDS


def test_empty_and_none_are_safe():
    assert repair_ocr_spacing("") == ""
    assert repair_ocr_spacing(None) == ""
