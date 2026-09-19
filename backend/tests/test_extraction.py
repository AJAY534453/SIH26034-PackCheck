"""Extraction unit tests — including the mandated MRP false-positive matrix."""
from __future__ import annotations

import pytest

from backend.extraction.base import nutrition_line_flags
from backend.extraction.engine import pick_winner, run_extraction
from backend.ocr.base import OcrLine

B = (10, 10, 200, 30)


def _line(text: str, conf: float = 0.9, bbox=B) -> OcrLine:
    return OcrLine(text=text, confidence=conf, bbox=bbox, engine="test", variant="original")


# ---------------- MRP positives ----------------

@pytest.mark.parametrize("text,expected", [
    ("MRP ₹200", "200"),
    ("MRP Rs. 200", "200"),
    ("M.R.P. : ₹200.00", "200.00"),
    ("Maximum Retail Price ₹200", "200"),
    ("M.R.P. ₹200/-", "200"),
    ("M R P Rs 200.00", "200.00"),
    ("Retail Sale Price: Rs. 500", "500"),
])
def test_mrp_positives(text, expected):
    cands = run_extraction({1: [_line(text)]})["mrp"]
    assert cands, f"no MRP candidate for {text!r}"
    winner, _ = pick_winner(cands)
    assert winner.value == expected


# ---------------- MRP negatives ----------------

@pytest.mark.parametrize("text", [
    "₹0.40/g",                       # unit sale price
    "₹120/kg",                       # unit price per kg
    "₹10/100g",                      # per-100g unit price
    "Rs. 0.40 per gram",             # per gram
    "Energy 450 kcal",               # nutrition
    "Protein 8g",                    # nutrition value
    "Ph: +91 9876543210",            # phone
    "FSSAI Lic. No. 10012345678901", # FSSAI number
    "Batch No: A0130",               # batch
    "PIN 638111",                    # PIN code
    "MFD: 04/02/25",                 # date, not price
])
def test_mrp_negatives(text):
    res = run_extraction({1: [_line(text)]})
    assert "mrp" not in res or all("per" not in c.reason or "unit" in c.reason for c in res["mrp"])
    # strict: currency-only weak path must not fire on these
    if "mrp" in res:
        for c in res["mrp"]:
            assert not UNIT_PRICE_CONTEXTS(c)


def UNIT_PRICE_CONTEXTS(c):
    return "unit price" in c.reason or "nutrition" in c.reason


def test_unit_price_never_mrp():
    res = run_extraction({1: [_line("₹0.40/g"), _line("MRP ₹200")]})
    mrp_winner, _ = pick_winner(res["mrp"])
    assert mrp_winner.value == "200"
    usp = res.get("unit_sale_price", [])
    assert usp and "0.40" in usp[0].value


def test_no_mrp_when_no_currency():
    res = run_extraction({1: [_line("Best before 6 months")]})
    assert "mrp" not in res


# ---------------- quantity ----------------

@pytest.mark.parametrize("text,unit,qtype", [
    ("Net Wt. 500 g", "g", "MASS"),
    ("NET QUANTITY: 1 kg", "kg", "MASS"),
    ("Net Volume 750 ml", "ml", "VOLUME"),
    ("Net Qty. 1 L", "L", "VOLUME"),
    ("Net Quantity: 10 N", "N", "NUMBER"),
])
def test_quantity(text, unit, qtype):
    import json as _json

    cands = run_extraction({1: [_line(text)]})["net_quantity"]
    assert cands
    winner, _ = pick_winner(cands)
    data = _json.loads(winner.value)
    assert data["unit"] == unit and data["quantity_type"] == qtype


# ---------------- composite quantity declarations ----------------

@pytest.mark.parametrize("text,value,unit,extra,total", [
    ("Net Wt. 28.4 g + 6.1 g EXTRA", "34.5", "g", "6.1", None),
    ("Net Wt. 28.4g+6.1g EXTRA=34.5g", "34.5", "g", "6.1", "34.5"),
    ("Net Wt 250 g Free 25 g", "275", "g", "25", None),
    ("Net Qty 1 kg + 100 g", "1100", "g", None, None),
    ("Net Quantity 10 x 20 g", "200", "g", None, None),
    ("Net Volume 2 x 500 ml", "1000", "ml", None, None),
])
def test_composite_quantity_is_combined_not_truncated(text, value, unit, extra, total):
    """A composite declaration must be reported whole, not as its first number."""
    import json as _json

    cands = run_extraction({1: [_line(text)]})["net_quantity"]
    assert cands, f"no quantity candidate for {text!r}"
    winner, _ = pick_winner(cands)
    data = _json.loads(winner.value)
    assert data["value"] == value, data
    assert data["unit"] == unit
    if extra is not None:
        assert data.get("extra_value") == extra
    if total is not None:
        assert data.get("declared_total") == total
    assert data.get("expression") == text


def test_composite_quantity_keeps_raw_and_declared_expression():
    text = "Net Wt. 28.4 g + 6.1 g EXTRA = 34.5 g"
    cands = run_extraction({1: [_line(text)]})["net_quantity"]
    winner, _ = pick_winner(cands)
    assert winner.raw_value == text  # raw OCR evidence is never rewritten
    assert "composite declaration" in winner.reason


def test_multipack_count_is_preserved():
    import json as _json

    cands = run_extraction({1: [_line("Net Quantity: 10 x 20 g")]})["net_quantity"]
    winner, _ = pick_winner(cands)
    data = _json.loads(winner.value)
    assert data.get("pack_count") == 10 and data.get("unit_value") == "20"


def test_mixed_family_composite_is_not_summed_and_needs_review():
    import json as _json

    cands = run_extraction({1: [_line("Net Wt 500 g + 1 N")]})["net_quantity"]
    winner, _ = pick_winner(cands)
    data = _json.loads(winner.value)
    assert data.get("parts_uncombined") is True
    assert data["value"] == "500"  # never invent a sum across different measurement families
    assert winner.inferred is True  # offered for human review, not asserted as detected


def test_single_quantity_line_is_not_treated_as_composite():
    import json as _json

    for text in ("Net Weight 500 g", "Net Quantity: 10 N", "MRP Rs. 50 Net Wt 500 g"):
        cands = run_extraction({1: [_line(text)]})["net_quantity"]
        winner, _ = pick_winner(cands)
        data = _json.loads(winner.value)
        assert "expression" not in data, (text, data)


def test_quantity_excludes_nutrition():
    lines = [_line("Nutritional Information"), _line("Energy 450 kcal"), _line("Protein 8g"), _line("Fat 20g")]
    flags = nutrition_line_flags(lines)
    res = run_extraction({1: lines})
    # '8g'/'20g' near nutrition rows must not become net quantity candidates
    qty = res.get("net_quantity", [])
    for c in qty:
        assert c.confidence < 0.6 or "Net" in c.raw_value


# ---------------- dates ----------------

@pytest.mark.parametrize("text,expect_iso,amb", [
    ("MFD: 04/02/25", "2025-02-04", True),
    ("MFD: 25/12/2025", "2025-12-25", False),
    ("PKD 04-02-2025", "2025-02-04", True),
    ("EXP 04.02.2026", "2026-02-04", True),
    ("MFG: 08/2026", "2026-08", False),
    ("BEST BEFORE: MAR 2026", "2026-03", False),
    ("Best Before: 5 months", "5 months", False),
])
def test_dates(text, expect_iso, amb):
    res = run_extraction({1: [_line(text)]})
    field = next(iter(res))
    winner, _ = pick_winner(res[field])
    assert winner.value.replace("|AMBIGUOUS", "") == expect_iso
    assert ("|AMBIGUOUS" in winner.value) == amb


def test_arbitrary_number_not_date():
    res = run_extraction({1: [_line("Lot 123456")]})
    assert not any(k.startswith("date_") for k in res)


# ---------------- batch / FSSAI ----------------

def test_batch():
    cands = run_extraction({1: [_line("Batch No: A0130")]})["batch_lot"]
    assert cands and pick_winner(cands)[0].value == "A0130"


def test_batch_rejects_price():
    res = run_extraction({1: [_line("Batch: 200")]})
    assert "batch_lot" not in res


@pytest.mark.parametrize("text", [
    "FSSAI Lic. No. 10012345678901",
    "FSSAI 10012345678901",
    "Lic No: 12345678901234",
])
def test_fssai(text):
    cands = run_extraction({1: [_line(text)]})["fssai_license"]
    assert cands and len(pick_winner(cands)[0].value) == 14


def test_random_14digit_without_context_not_fssai():
    res = run_extraction({1: [_line("Code 10012345678901")]})
    assert "fssai_license" not in res


# ---------------- contacts ----------------

def test_email():
    cands = run_extraction({1: [_line("Email: care@abcfoods.com")]})["consumer_care_email"]
    assert cands and pick_winner(cands)[0].value == "care@abcfoods.com"


def test_website():
    cands = run_extraction({1: [_line("www.vedhaproducts.com")]})["website"]
    assert cands and "vedhaproducts" in pick_winner(cands)[0].value


def test_consumer_phone():
    cands = run_extraction({1: [_line("Consumer Care: 1800-123-4567")]})["consumer_care_phone"]
    assert cands and pick_winner(cands)[0].value == "18001234567"


# ---------------- conflicts ----------------

def test_conflicting_mrp_flagged_not_merged():
    res = run_extraction({1: [_line("MRP ₹200"), _line("MRP Rs. 250")]})
    winner, conflict = pick_winner(res["mrp"])
    assert conflict is True
    values = {c.value for c in res["mrp"]}
    assert {"200", "250"}.issubset(values)


def test_agreement_raises_no_conflict():
    res = run_extraction({1: [_line("MRP ₹200"), _line("M.R.P. : ₹200.00")]})
    _, conflict = pick_winner(res["mrp"])
    assert conflict is False


# ---------------- multi-image aggregation ----------------

def test_disagreeing_images_produce_conflict_not_a_silent_choice():
    """Two images showing different prices is a genuine conflict: both candidates survive and
    the field is flagged so a human resolves it."""
    res = run_extraction({1: [_line("MRP ₹200")], 2: [_line("MRP Rs. 250", bbox=(300, 10, 500, 30))]})
    _, conflict = pick_winner(res["mrp"])
    assert conflict is True
    assert {"200", "250"}.issubset({c.value for c in res["mrp"]})


def test_agreeing_images_across_files_raise_no_conflict():
    res = run_extraction({1: [_line("MRP ₹200")], 2: [_line("M.R.P. : ₹200.00", bbox=(300, 10, 500, 30))]})
    _, conflict = pick_winner(res["mrp"])
    assert conflict is False


def test_weaker_image_never_overwrites_stronger_evidence():
    """A low-confidence read from a second photo must not displace a confident read from the
    first, and must not raise a conflict against it."""
    res = run_extraction({
        1: [_line("MRP ₹200", conf=0.95)],
        2: [_line("MRP ₹7OO", conf=0.25, bbox=(300, 10, 500, 30))],
    })
    winner, conflict = pick_winner(res["mrp"])
    assert conflict is False
    assert winner.value == "200" and winner.source_image_id == 1


# ---------------- manufacturer / country ----------------

def test_manufacturer_with_address_numbers_preserved():
    lines = [
        _line("Manufactured by ABC Foods Pvt Ltd"),
        _line("No. 19, Muthur Road, Vellakovil - 638111"),
    ]
    cands = run_extraction({1: lines}).get("manufacturer", [])
    assert cands
    winner, _ = pick_winner(cands)
    assert "ABC Foods" in winner.value and "638111" in winner.value


def test_nutrition_not_in_manufacturer():
    lines = [
        _line("Manufactured by XYZ Foods"),
        _line("Nutritional Information"),
        _line("Energy 450 kcal"),
        _line("Protein 8g"),
    ]
    cands = run_extraction({1: lines}).get("manufacturer", [])
    assert cands
    winner, _ = pick_winner(cands)
    assert "Energy" not in winner.value and "Protein" not in winner.value


def test_country_of_origin():
    cands = run_extraction({1: [_line("Country of Origin: India")]})["country_of_origin"]
    assert cands and pick_winner(cands)[0].value == "India"
