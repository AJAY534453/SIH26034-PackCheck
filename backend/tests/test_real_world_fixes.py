"""Unit regressions for the real-photo extraction defects.

Each test states a general rule about package declarations (never a rule about one product):
company-suffix recognition under OCR word fusion, whole composite quantities, identifier shape for
batch codes, label/value association inside a printed date block, stacked-title reading, and
contact-domain corroboration.
"""
from __future__ import annotations

import json

from backend.extraction.base import looks_like_company_entity
from backend.extraction.dates import extract_dates
from backend.extraction.engine import pick_winner, run_extraction
from backend.extraction.identifiers import extract_batch_lot
from backend.extraction.product_identity import extract_product_identity
from backend.extraction.quantity import extract_net_quantity, parse_quantity_expression
from backend.normalization.validators import corroborate_email_domain
from backend.ocr.base import OcrLine


def line(text: str, bbox, conf: float = 0.9, variant: str = "original") -> OcrLine:
    return OcrLine(text=text, confidence=conf, bbox=tuple(bbox), engine="rapidocr", variant=variant)


# ---------------------------------------------------------------- company entities


def test_company_entity_recognised_despite_ocr_word_fusion():
    assert looks_like_company_entity("BRITANNIA INDUSTRIESLTD.")
    assert looks_like_company_entity("BRITANNIA INDUSTRIES LIMITED")
    assert looks_like_company_entity("ACME FOODSPVTLTD")
    assert looks_like_company_entity("XYZ PRIVATELIMITED")
    assert looks_like_company_entity("SUNDARAM ENTERPRISES")


def test_ordinary_product_text_is_not_mistaken_for_a_company():
    for text in (
        "CLASSIC SWEET & SALTY",
        "Masala Noodles",
        "WALT DISNEY",
        "Instant Coffee Powder",
        "Turmeric Powder 100 g",
    ):
        assert not looks_like_company_entity(text), text


def test_company_line_is_not_offered_as_the_product_name():
    lines = [
        line("BRITANNIA INDUSTRIESLTD.", (503, 612, 722, 646), 0.902),
        line("BILIREGN.NO.BO-23-000-08-AABCB2066P-22", (495, 632, 733, 669), 0.923),
    ]
    out = extract_product_identity(lines)
    assert not [c for c in out if c.field_name == "product_name"]


# ---------------------------------------------------------------- composite quantities


def test_composite_quantity_survives_a_lost_unit_on_the_printed_total():
    parsed = parse_quantity_expression("Net Wt. 28.4g+6.1g EXTRA-34")
    assert parsed is not None
    assert parsed["combined"] == ("34.5", "g", "MASS")
    assert parsed["base"] == ("28.4", "g")
    assert parsed["extra"] == ("6.1", "g")
    # the printed total as read disagrees with the printed parts -> surfaced, not published
    assert parsed["declared_total"] == ("34", "g")
    assert parsed["total_discrepancy"] is True


def test_composite_quantity_with_a_matching_printed_total_is_not_flagged():
    parsed = parse_quantity_expression("Net Wt. 28.4 g + 6.1 g EXTRA = 34.5 g")
    assert parsed["combined"] == ("34.5", "g", "MASS")
    assert parsed["total_discrepancy"] is False


def test_quantity_unit_is_read_when_ocr_runs_it_into_the_next_word():
    # 'g' followed immediately by an uppercase word used to make the whole token unmatchable, so
    # the quantity was silently missed and the declaration reported as absent
    lines = [line("Net Wt 500gEXTRA", (40, 100, 300, 130))]
    values = [json.loads(c.value)["value"] for c in extract_net_quantity(lines)]
    assert "500" in values


def test_mixed_families_are_never_summed():
    parsed = parse_quantity_expression("500 g + 1 N")
    assert parsed["combined"] is None
    assert parsed["uncombined"]


def test_identifier_value_line_never_becomes_a_quantity():
    # 'B07269L' read in the value column of a lot label: recovering 'L' as litres would invent a
    # measurement out of a part code
    lines = [line("LOT No.", (644, 563, 734, 596)), line("B07269L", (805, 500, 1002, 540), 0.857)]
    assert extract_net_quantity(lines) == []


# ---------------------------------------------------------------- batch / lot


def test_batch_code_is_taken_from_the_value_column_not_the_adjacent_word():
    lines = [
        line("LOT No.", (644, 563, 734, 596), 0.836),
        line("B07269L", (805, 500, 1002, 540), 0.857),
        line("OTHER", (486, 614, 554, 635), 0.823),
    ]
    cands = extract_batch_lot(lines)
    assert cands, "the alphanumeric code beside the label was not recovered"
    assert cands[0].value == "B07269L"


def test_batch_candidates_reject_words_dates_prices_and_number_shapes():
    for text, bbox in (
        ("Batch OTHER", (40, 100, 300, 130)),
        ("Batch No. 19/01/27", (40, 200, 300, 230)),
        ("Batch: 560048", (40, 300, 300, 330)),
        ("Lot No. 200", (40, 400, 300, 430)),
    ):
        assert not extract_batch_lot([line(text, bbox)]), text


# ---------------------------------------------------------------- printed date blocks


def test_date_block_pairing_follows_chronology_when_columns_are_out_of_step():
    lines = [
        line("MRP.", (600, 300, 700, 330)),
        line("Rs.10.00", (800, 270, 950, 310)),
        line("PKD.", (600, 400, 700, 430)),
        line("20/07/26", (800, 352, 950, 394)),
        line("USE BY", (600, 460, 700, 490)),
        line("19/01/27", (800, 405, 950, 447)),
    ]
    out = run_extraction({1: lines})
    packing, _ = pick_winner(out["date_packing"])
    expiry, _ = pick_winner(out["date_expiry"])
    assert packing is not None and packing.value.startswith("2026-07-20")
    assert expiry is not None and expiry.value.startswith("2027-01-19")
    assert "re-associated" in (packing.reason + expiry.reason)


def test_already_coherent_date_block_is_left_untouched():
    lines = [
        line("PKD. 20/07/26", (600, 400, 950, 430)),
        line("USE BY 19/01/27", (600, 460, 950, 490)),
    ]
    out = run_extraction({1: lines})
    packing, _ = pick_winner(out["date_packing"])
    expiry, _ = pick_winner(out["date_expiry"])
    assert packing.value.startswith("2026-07-20")
    assert expiry.value.startswith("2027-01-19")
    assert "re-associated" not in packing.reason
    assert "re-associated" not in expiry.reason


def test_duration_declaration_is_not_reconciled_as_a_calendar_date():
    lines = [
        line("MFD 20/07/26", (40, 100, 400, 130)),
        line("BEST BEFORE 12 MONTHS FROM PACKING", (40, 160, 700, 190)),
    ]
    out = run_extraction({1: lines})
    best_before, _ = pick_winner(out["date_best_before"])
    assert best_before is not None
    payload = json.loads(best_before.value)
    assert payload["type"] == "DURATION_FROM_REFERENCE"
    assert "20/07/26" not in best_before.value


# ---------------------------------------------------------------- stacked titles


def test_stacked_title_is_merged_across_an_interrupting_tag():
    lines = [
        line("BISCUIT", (547, 651, 610, 672), 0.838),
        line("CLASSIC", (340, 676, 609, 770), 0.855),
        line("21%", (617, 662, 745, 802), 0.738),
        line("SWEET", (341, 774, 470, 806), 0.809),
        line("&SALTY", (341, 805, 482, 838), 0.731),
    ]
    out = extract_product_identity(lines)
    product = [c for c in out if c.field_name == "product_name"]
    assert product, "no product title candidate"
    value = product[0].value.upper()
    assert "CLASSIC" in value and "SWEET" in value and "SALTY" in value
    assert "BISCUIT" not in value  # a separate column is not part of the title block


def test_low_confidence_line_is_not_published_as_a_title():
    out = extract_product_identity([line("AINDSTALAREA,EL FHE ULSFA", (564, 914, 829, 954), 0.568)])
    assert not [c for c in out if c.field_name == "product_name"]


# ---------------------------------------------------------------- contact corroboration


def test_uncorroborated_email_domain_is_surfaced_for_review():
    check = corroborate_email_domain(
        "karnataka.e-mail.feedback@briindio.com", "BRITANNIA CLASSIC SWEET SALTY"
    )
    assert check["corroborated"] is False
    assert "briindio.com" in check["note"]


def test_corroborated_and_public_domains_pass():
    assert corroborate_email_domain("care@britannia.co.in", "BRITANNIA Biscuits")["corroborated"] is True
    assert corroborate_email_domain("smallbrand@yahoo.com", "Local Foods")["corroborated"] is True
    assert corroborate_email_domain("", "")["corroborated"] is None
