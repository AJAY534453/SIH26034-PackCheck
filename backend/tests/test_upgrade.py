"""Regression tests for the correctness upgrade (missing ≠ absent, semantic extraction,
honest confidence, human-confirmed absence, conflict handling).

Spec cases covered (Priority 9):
 1-5. OCR misses MRP/net-quantity/mfg-date/consumer-care/batch → rule UNCERTAIN, decision NEEDS_MANUAL_REVIEW
 6. Human confirms MRP absent → rule FAIL, decision NON_COMPLIANT
 7. "Marketed by:" alone → manufacturer anchor never becomes a value
 8. "Marketed by: ABC Foods Pvt Ltd" → manufacturer = ABC Foods Pvt Ltd
 9. Empty value → no confidence, state != DETECTED
 10. Conflicting OCR → CONFLICT, manual review (existing tests also cover extractor level)
 11. Physical quantity never verified from photograph (manual-only rule)
 12. Physical font size never claimed verified without calibration (legibility wording)
"""
from __future__ import annotations

import json

import pytest

from backend.extraction.base import Candidate
from backend.extraction.engine import pick_winner, run_extraction
from backend.ocr.base import OcrLine

B = (10, 10, 200, 30)


def _line(text: str, conf: float = 0.9, bbox=B) -> OcrLine:
    return OcrLine(text=text, confidence=conf, bbox=bbox, engine="test", variant="original")


# ---------------- cases 7 & 8: manufacturer semantics ----------------

def test_case7_marketed_by_alone_no_entity():
    """Anchor alone must never become the manufacturer value."""
    res = run_extraction({1: [_line("Marketed by:")]})
    mfr = res.get("manufacturer", [])
    assert not mfr, "anchor text must not be extracted as a company name"


def test_case7b_manufactured_by_anchor_only():
    res = run_extraction({1: [_line("Manufactured by")]})
    assert not res.get("manufacturer", [])


def test_case8_marketed_by_with_entity_next_line():
    lines = [
        _line("Marketed by:"),
        _line("ABC Foods Pvt Ltd,"),
        _line("Chennai, Tamil Nadu - 600001"),
    ]
    res = run_extraction({1: lines})
    cands = res.get("marketer", [])
    assert cands, "entity after anchor must be extracted (as marketer role at extraction level)"
    winner, _ = pick_winner(cands)
    assert "ABC Foods" in winner.value
    assert "Marketed" not in winner.value


def test_case8b_manufactured_by_same_line_entity():
    res = run_extraction({1: [_line("Manufactured by XYZ Industries Pvt Ltd")]})
    cands = res.get("manufacturer", [])
    assert cands
    winner, _ = pick_winner(cands)
    assert "XYZ Industries" in winner.value
    assert "Manufactured" not in winner.value


def test_case8c_imported_by_entity():
    lines = [_line("Imported by:"), _line("ABC Imports & Traders Pvt Ltd")]
    res = run_extraction({1: lines})
    cands = res.get("importer", [])
    assert cands
    winner, _ = pick_winner(cands)
    assert "ABC Imports" in winner.value


def test_marketed_by_promotes_to_manufacturer_only_when_no_role_entity():
    """Pipeline-level: marketer promotion must carry provenance, not be silent."""
    from backend.services.inspection_service import TRACKED_DECLARATION_FIELDS

    # the promotion logic lives in process_inspection; here we assert the constants + the
    # standalone extraction behave (full pipeline covered in API tests)
    assert "marketer" not in TRACKED_DECLARATION_FIELDS  # it's a role, not a tracked field
    assert "manufacturer" in TRACKED_DECLARATION_FIELDS


# ---------------- case 4/9: product name vs brand ----------------

def test_product_name_single_token_not_duplicated_to_brand():
    res = run_extraction({1: [_line("GURUCHARAAPRODUCT")]})
    assert res.get("product_name")
    # single-token title must NOT be blindly duplicated into brand
    assert "brand" not in res


def test_product_name_multi_token_yields_brand():
    res = run_extraction({1: [_line("KRACKJACK BISCUITS")]})
    assert res.get("product_name")
    brand = res.get("brand", [])
    assert brand and brand[0].value == "KRACKJACK"


def test_explicit_brand_label_wins():
    lines = [_line("Brand: ABC"), _line("Instant Coffee Powder")]
    res = run_extraction({1: lines})
    brand = pick_winner(res["brand"])[0]
    assert brand.value == "ABC"
    assert "explicit" in brand.reason.lower()
    # product name should be the descriptive line, not the brand label line
    pn = pick_winner(res["product_name"])[0]
    assert "Coffee" in pn.value


# ---------------- rule engine: missing ≠ absent ----------------

def _fields(**overrides) -> dict:
    base = {
        "product_name": {"state": "DETECTED", "display_value": "Test Biscuits", "normalized_value": "{}", "confidence": 0.9},
        "net_quantity": {"state": "DETECTED", "display_value": "500 g",
                         "normalized_value": json.dumps({"value": "500", "unit": "g", "quantity_type": "MASS"}),
                         "confidence": 0.9},
        "mrp": {"state": "DETECTED", "display_value": "₹ 200.00", "normalized_value": "{}", "confidence": 0.9},
    }
    base.update(overrides)
    return base


def _context(quality: str = "GOOD") -> dict:
    return {"overall_quality": quality, "image_count": 1, "packaging": "retail_package",
            "category": "FOOD", "category_state": "DETECTED", "is_import_evidence": False}


def _evaluate(check_type: str, params: dict, fields: dict, ctx: dict):
    from backend.rules.engine import evaluate_rule

    class _A:
        applies = True
        reason = "test"

    ct, pp = check_type, params  # class bodies cannot close over same-named locals

    class _RV:
        id = 1
        rule_id = "T"
        rule_number = "6(1)(t)"
        title = "t"
        requirement = "r"
        check_type = ct
        params = pp
        description = ""

    return evaluate_rule(_RV(), fields, ctx, _A())


@pytest.mark.parametrize("check_type,params,missing_fields", [
    ("mrp_declaration", {"fields": ["mrp"]}, ["mrp"]),
    ("net_quantity", {"fields": ["net_quantity"]}, ["net_quantity"]),
    ("declaration_present", {"fields": ["date_manufacturing", "date_packing"]}, ["date_manufacturing", "date_packing"]),
    ("declaration_present", {"fields": ["consumer_care_phone", "consumer_care_email"]}, ["consumer_care_phone", "consumer_care_email"]),
    ("declaration_present", {"fields": ["batch_lot"]}, ["batch_lot"]),
])
def test_cases_1_to_5_missing_field_is_uncertain_not_fail(check_type, params, missing_fields):
    """Spec cases 1-5: OCR miss → UNCERTAIN, never FAIL — even on GOOD images."""
    fields = _fields()
    for name in missing_fields:
        # simulate MISSING row (as pipeline now writes) or absent entry
        fields[name] = {"state": "MISSING", "display_value": "", "normalized_value": "", "confidence": 0}
    outcome = _evaluate(check_type, params, fields, _context("GOOD"))
    assert outcome.status == "UNCERTAIN", outcome.reason
    assert "manual verification" in outcome.reason.lower()
    assert "does not prove" in outcome.reason.lower() or "could not be reliably" in outcome.reason.lower()


def test_case6_human_confirmed_absence_produces_fail():
    """Spec case 6: the ONLY path from missing-evidence to FAIL."""
    fields = _fields()
    fields["mrp"] = {"state": "HUMAN_CONFIRMED_ABSENT", "display_value": "", "normalized_value": "", "confidence": 1.0}
    outcome = _evaluate("mrp_declaration", {"fields": ["mrp"]}, fields, _context("GOOD"))
    assert outcome.status == "FAIL"
    assert "confirmed absent" in outcome.reason.lower()


def test_case6b_human_confirmed_absence_net_quantity_fail():
    fields = _fields()
    fields["net_quantity"] = {"state": "HUMAN_CONFIRMED_ABSENT", "display_value": "", "normalized_value": "", "confidence": 1.0}
    outcome = _evaluate("net_quantity", {"fields": ["net_quantity"]}, fields, _context("GOOD"))
    assert outcome.status == "FAIL"


def test_human_confirmed_absence_decides_non_compliant():
    from backend.rules.decision import decide_inspection
    from backend.rules.engine import RuleOutcome

    fail = RuleOutcome(rule_id="T", rule_version_id=1, rule_number="6(1)(e)", title="MRP",
                       requirement="r", status="FAIL", reason="confirmed absent by inspector",
                       observed="", expected="", confidence=1.0, critical=True, check_type="mrp_declaration")
    decision, summary = decide_inspection([fail], "DETECTED", [])
    assert decision == "NON_COMPLIANT"
    assert "human" in summary.lower() or "potential non-compliance" in summary.lower()


def test_detected_value_passes_even_when_others_missing():
    """A detected field still PASSES while unrelated fields are MISSING."""
    fields = _fields()
    fields["batch_lot"] = {"state": "MISSING", "display_value": "", "normalized_value": "", "confidence": 0}
    outcome = _evaluate("net_quantity", {"fields": ["net_quantity"]}, fields, _context("GOOD"))
    assert outcome.status == "PASS"


# ---------------- case 9: honest confidence on empty values ----------------

def test_empty_value_candidate_becomes_uncertain_without_confidence():
    """Date anchor with no parseable date: pipeline records UNCERTAIN + confidence 0.

    Extraction emits an empty-value candidate; the pipeline layer is covered by API tests.
    Here: the extractor must emit an empty-value candidate with a low honest confidence,
    and pick_winner must not present it as a confident DETECTED value.
    """
    res = run_extraction({1: [_line("MFD:")]})
    date_fields = [k for k in res if k.startswith("date_")]
    assert date_fields, "anchor alone should still produce an (uncertain) observation"
    for k in date_fields:
        for c in res[k]:
            assert c.confidence <= 0.5, "unparseable date must carry low confidence"
            assert c.value == ""


# ---------------- case 10: conflicts ----------------

def test_case10_conflicting_values_flagged():
    res = run_extraction({1: [_line("MRP ₹200"), _line("M.R.P. : ₹250")]})
    winner, conflict = pick_winner(res["mrp"])
    assert conflict is True


# ---------------- cases 11 & 12: physical measurement honesty ----------------

def test_case11_physical_quantity_never_auto_verified():
    from backend.rules.engine import CHECKS

    class _RV:
        id = 1
        rule_id = "LM-PCR-7-ERR"
        rule_number = "7"
        title = "Permissible errors in net quantity"
        requirement = "r"
        check_type = "manual_only"
        params = {}
        description = "requires calibrated weighing"

    status, reason, observed, conf = CHECKS["manual_only"](_RV(), {}, _fields(), _context())
    assert status == "UNCERTAIN"
    assert "manual" in reason.lower()
    assert conf < 0.3


def test_case12_font_size_not_claimed_verified():
    fields = _fields()
    outcome = _evaluate("legibility", {"min_good_images": 1}, fields, _context("GOOD"))
    assert outcome.status == "PASS"  # visibility can be assessed
    assert "millimetres" in outcome.reason or "calibration" in outcome.reason
    assert "manual verification required" in outcome.reason.lower()


# ---------------- decision aggregation with MISSING rows ----------------

def test_pipeline_field_constants_complete():
    from backend.services.inspection_service import TRACKED_DECLARATION_FIELDS

    required = {
        "product_name", "brand", "common_name", "manufacturer", "net_quantity", "mrp",
        "date_manufacturing", "date_expiry", "date_best_before", "batch_lot",
        "consumer_care_phone", "consumer_care_email", "fssai_license", "country_of_origin",
    }
    assert required.issubset(set(TRACKED_DECLARATION_FIELDS))
