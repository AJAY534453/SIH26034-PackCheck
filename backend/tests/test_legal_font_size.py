"""Rule 7 (LM (PC) Rules, 2011) letter-height compliance: data, measurement, verdict, calibration.

The legal minimum heights are NOT hard-coded anywhere in the application — they are read from
``backend/rules/definitions/lm_pcr_2011_rule7.json``. These tests therefore check the *selection
logic* against that data, plus the honesty policy: a millimetre figure is never produced without a
scale, and the check refuses to FAIL on an estimated panel area alone.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.rules.engine import check_font_size
from backend.rules.font_size import all_tables, requirement_for, requirement_table
from backend.services.font_service import (
    CAP_HEIGHT_RATIO,
    build_font_measurement,
    image_px_per_mm,
    measure_field,
    panel_area,
    resolve_px_per_mm,
)

DEFINITION = Path(__file__).resolve().parent.parent / "rules" / "definitions" / "lm_pcr_2011_rule7.json"


@pytest.fixture(scope="module")
def params() -> dict:
    doc = json.loads(DEFINITION.read_text(encoding="utf-8"))
    rule = next(r for r in doc["rules"] if r["check_type"] == "font_size")
    return rule["params"]


@pytest.fixture()
def rule_version(params):
    doc = json.loads(DEFINITION.read_text(encoding="utf-8"))
    rule = next(r for r in doc["rules"] if r["check_type"] == "font_size")
    return SimpleNamespace(
        title=rule["title"],
        requirement=rule["requirement"],
        source_reference=rule["source_reference"],
        params=params,
        check_type="font_size",
    )


# ----------------------------------------------------------------- data-driven requirement


def test_table_i_values_match_the_published_table(params):
    """Table-I (net quantity by weight/volume) — as substituted by G.S.R. 629(E), 23.06.2017."""
    expected = [
        (50, 1.0, 1.5),
        (80, 1.5, 3.0),
        (300, 2.5, 4.0),
        (1000, 4.0, 6.0),
        (5000, 6.0, 6.0),
    ]
    for area, normal, moulded in expected:
        assert requirement_for(params, quantity_family="MASS", area_cm2=area).required_mm == normal
        assert (
            requirement_for(params, quantity_family="VOLUME", area_cm2=area, form="moulded").required_mm
            == moulded
        )


def test_table_ii_is_selected_for_number_length_and_area_declarations(params):
    for family in ("NUMBER", "LENGTH", "AREA"):
        req = requirement_for(params, quantity_family=family, area_cm2=300)
        assert req is not None and req.table_id == "Table-II"
        assert req.required_mm == 2.0  # above 100 cm2 and up to 500 cm2


def test_unknown_quantity_family_cannot_select_a_table(params):
    assert requirement_for(params, quantity_family="MYSTERY", area_cm2=300) is None
    assert requirement_for(params, quantity_family="", area_cm2=300) is None
    assert requirement_table(params, "MYSTERY") == {}


def test_bracket_boundary_and_adjacent_lower_bracket_are_reported(params):
    req = requirement_for(params, quantity_family="MASS", area_cm2=300)
    assert req.serial == 3 and req.required_mm == 2.5
    # the adjacent lower bracket is needed to keep a verdict honest when the area is estimated
    assert req.lower_bracket_mm == 1.5
    assert "Table-I serial 3" in req.reference and "2.5 mm" in req.reference


def test_both_published_tables_are_available_for_display(params):
    tables = all_tables(params)
    assert [t["id"] for t in tables] == ["Table-I", "Table-II"]
    assert len(tables[0]["rows"]) == 5 and len(tables[1]["rows"]) == 4


# ----------------------------------------------------------------- measurement maths

def _field(name="mrp", text="M.R.P. ₹ 200.00", bbox="10,100,210,140", state="DETECTED"):
    return SimpleNamespace(
        field_name=name, source_text=text, display_value=text, bbox=bbox, state=state,
        source_image_id=1, normalized_value="{}",
    )


def test_letter_height_uses_a_documented_cap_height_ratio():
    measured = measure_field(_field(), 8.0)
    assert measured is not None
    # bbox 40 px tall over 8 px/mm = 5.00 mm line box -> 3.75 mm cap height
    assert measured["line_height_mm"] == 5.0
    assert measured["letter_height_mm"] == round(5.0 * CAP_HEIGHT_RATIO, 2) == 3.75
    assert measured["avg_char_width_mm"] is not None and measured["width_ratio"] is not None


def test_measurement_is_impossible_without_a_scale():
    assert measure_field(_field(), None) is None


def test_png_dpi_metadata_is_trusted_only_when_plausible(tmp_path):
    from PIL import Image

    real = tmp_path / "real.png"
    Image.new("RGB", (50, 50), "white").save(real, dpi=(300, 300))
    px_per_mm, source = image_px_per_mm(real)
    # the PNG pHYs chunk stores dpi as an integer, so compare with the rounding tolerance it implies
    assert px_per_mm == pytest.approx(300 / 25.4, abs=0.01)
    assert "image_dpi" in source

    untrusted = tmp_path / "screenshot.png"
    Image.new("RGB", (50, 50), "white").save(untrusted, dpi=(72, 72))
    px_per_mm, source = image_px_per_mm(untrusted)
    assert px_per_mm is None and "untrustworthy" in source


def test_manual_calibration_takes_precedence_and_panel_area_is_provenanced():
    inspection = SimpleNamespace(
        calibration_px_per_mm=8.0, calibration_source="reference_length(50 mm ≙ 400 px)",
        calibration_note="measured", panel_width_mm=150.0, panel_height_mm=100.0,
        pdp_area_cm2=None, packaging_form="normal",
    )
    calibration = resolve_px_per_mm(inspection, [])
    assert calibration["px_per_mm"] == 8.0 and "reference_length" in calibration["source"]
    area = panel_area(inspection, [], 8.0)
    assert area["cm2"] == 150.0 and area["source"] == "measured"


def test_panel_area_from_an_image_is_flagged_as_an_estimate():
    inspection = SimpleNamespace(
        calibration_px_per_mm=10.0, calibration_source="manual", calibration_note="",
        panel_width_mm=None, panel_height_mm=None, pdp_area_cm2=None, packaging_form="normal",
    )
    image = SimpleNamespace(width=1000, height=800, role="FRONT", stored_filename="x.jpg", original_filename="x.jpg")
    area = panel_area(inspection, [image], 10.0)
    # 100 mm × 80 mm = 80 cm²
    assert area["cm2"] == 80.0 and area["source"] == "estimated"


# ----------------------------------------------------------------- the check

def _context(px_per_mm=8.0, area=300.0, area_source="measured", family="MASS", measurements=None, form="normal"):
    return {
        "font_measurement": {
            "available": bool(px_per_mm and area),
            "px_per_mm": px_per_mm,
            "px_per_mm_source": "reference_length(50 mm ≙ 400 px)" if px_per_mm else None,
            "px_per_mm_note": "",
            "panel_area_cm2": area,
            "panel_area_source": area_source,
            "panel_area_note": "",
            "packaging_form": form,
            "quantity_family": family,
            "measurements": measurements if measurements is not None else [
                {"field_name": "mrp", "text": "₹ 200.00", "letter_height_mm": 3.75, "line_height_mm": 5.0,
                 "width_ratio": 0.5}
            ],
            "smallest_line": None,
            "cap_height_ratio": CAP_HEIGHT_RATIO,
            "method": "test method",
        }
    }


def test_no_calibration_is_uncertain_and_never_a_verdict(rule_version):
    status, reason, observed, confidence, detail = check_font_size(
        rule_version, rule_version.params, {}, _context(px_per_mm=None, area=None)
    )
    assert status == "UNCERTAIN"
    assert "no scale calibration" in reason.lower()
    assert detail["required_mm"] is None and detail["satisfied"] is None and detail["available"] is False


def test_unresolved_quantity_family_is_uncertain(rule_version):
    status, reason, *_ = check_font_size(rule_version, rule_version.params, {}, _context(family=None))
    assert status == "UNCERTAIN" and "Table-I" in reason and "Table-II" in reason


def test_missing_panel_area_is_uncertain_and_names_the_table(rule_version):
    status, reason, _, _, detail = check_font_size(
        rule_version, rule_version.params, {}, _context(area=None, area_source="unavailable")
    )
    assert status == "UNCERTAIN" and "principal display panel" in reason
    assert detail["applicable_table"]["id"] == "Table-I"


def test_sufficient_letter_height_passes_with_required_and_detected_reported(rule_version):
    status, reason, observed, confidence, detail = check_font_size(
        rule_version, rule_version.params, {}, _context()
    )
    assert status == "PASS"
    assert detail["required_mm"] == 2.5 and detail["detected_mm"] == 3.75 and detail["satisfied"] is True
    assert "Rule 7(2), Table-I serial 3" in detail["required_reference"]
    assert "3.75 mm" in reason and "2.5 mm" in reason
    assert confidence >= 0.7  # a measured panel area is authoritative


def test_clear_shortfall_fails_with_the_evidence_stated(rule_version):
    measurements = [{"field_name": "net_quantity", "text": "100 g", "letter_height_mm": 1.2, "line_height_mm": 1.6, "width_ratio": 0.5}]
    status, reason, _observed, _conf, detail = check_font_size(
        rule_version, rule_version.params, {}, _context(measurements=measurements)
    )
    assert status == "FAIL", reason
    assert detail["satisfied"] is False and detail["detected_mm"] == 1.2
    assert "below the minimum height" in reason
    assert "Rule 7(2), Table-I serial 3" in reason


def test_borderline_measurement_is_uncertain_not_a_failure(rule_version):
    measurements = [{"field_name": "mrp", "text": "₹ 200", "letter_height_mm": 2.4, "line_height_mm": 3.2, "width_ratio": 0.5}]
    status, reason, *_ , detail = check_font_size(
        rule_version, rule_version.params, {}, _context(measurements=measurements)
    )
    assert status == "UNCERTAIN" and detail["satisfied"] is None
    assert "not decisive" in reason


def test_estimated_area_cannot_produce_a_failure_on_its_own(rule_version):
    """2.0 mm is below the 2.5 mm requirement for the estimated bracket, but above the adjacent
    lower bracket's 1.5 mm — an estimate must not turn that into a legal failure."""
    measurements = [{"field_name": "mrp", "text": "₹ 200", "letter_height_mm": 2.0, "line_height_mm": 2.6, "width_ratio": 0.5}]
    status, _reason, _o, _c, detail = check_font_size(
        rule_version, rule_version.params, {}, _context(area_source="estimated", measurements=measurements)
    )
    assert status == "UNCERTAIN", "a FAIL must never rest on an estimated panel area alone"
    assert detail["meterage_floor_mm"] == 1.5


def test_moulded_container_uses_the_stricter_column(rule_version):
    _s, _r, _o, _c, detail = check_font_size(
        rule_version, rule_version.params, {}, _context(form="moulded", measurements=[
            {"field_name": "mrp", "text": "₹ 200", "letter_height_mm": 3.0, "line_height_mm": 4.0, "width_ratio": 0.5}
        ])
    )
    assert detail["required_mm"] == 4.0, "the blown/formed/molded column applies"
    assert detail["satisfied"] is False


def test_detail_carries_the_legal_reference_and_the_method(rule_version):
    _s, _r, _o, _c, detail = check_font_size(rule_version, rule_version.params, {}, _context())
    assert "LM (PC) Rules, 2011" in detail["reference"]
    assert "Rule 7(5)" in detail["exempt_note"]
    assert detail["cap_height_ratio"] == CAP_HEIGHT_RATIO
    assert len(detail["tables"]) == 2


# ----------------------------------------------------------------- API: calibration + panel

def test_calibration_endpoint_reevaluates_and_is_permission_gated(client, auth_headers, _label_png, viewer_headers):
    iid = client.post("/inspections", headers=auth_headers).json()["id"]
    client.post(f"/inspections/{iid}/images", headers=auth_headers,
                data={"role": "FRONT"}, files={"file": ("label.png", _label_png, "image/png")})
    assert client.post(f"/inspections/{iid}/process", headers=auth_headers).status_code == 200

    # without a scale the honest answer is "cannot measure"
    before = client.get(f"/repository/font-size/{iid}", headers=auth_headers).json()
    assert before["status"] == "UNCERTAIN"
    assert before["required_mm"] is None and before["tables"]

    # a read-only viewer may not record a calibration
    assert client.post(
        f"/repository/inspections/{iid}/calibration", headers=viewer_headers,
        json={"px_per_mm": 8}, 
    ).status_code == 403

    # nothing measurable supplied -> 422 with an actionable message
    assert client.post(f"/repository/inspections/{iid}/calibration", headers=auth_headers, json={}).status_code == 422

    # a real calibration is accepted and re-evaluates the rules
    r = client.post(
        f"/repository/inspections/{iid}/calibration", headers=auth_headers,
        json={"reference_mm": 50, "reference_px": 400, "panel_width_mm": 150, "panel_height_mm": 100,
              "packaging_form": "normal", "note": "steel rule"},
    )
    assert r.status_code == 200, r.text
    calibration = r.json()["calibration"]
    assert calibration["px_per_mm"] == 8.0 and "reference_length" in calibration["source"]
    assert calibration["pdp_area_cm2"] is None  # 150 mm × 100 mm = 150 cm² is computed by the measurement layer

    after = client.get(f"/repository/font-size/{iid}", headers=auth_headers).json()
    assert after["px_per_mm"] == 8.0
    assert after["panel_area_cm2"] == 150.0 and after["panel_area_source"] == "measured"
    # The blank test label declares no net quantity, so Rule 7(2) cannot select Table-I or Table-II
    # and the requirement stays unresolved — with a reason that says exactly which piece is missing.
    assert after["required_mm"] is None
    assert after["status"] == "UNCERTAIN"
    assert "Table-I" in after["reason"] and "Table-II" in after["reason"]
    assert "Rule 7" in after["source_reference"]
    assert after["source_reference"].endswith("(as substituted by G.S.R. 629(E) dated 23.06.2017)")

    # a bad calibration payload is rejected
    assert client.post(
        f"/repository/inspections/{iid}/calibration", headers=auth_headers,
        json={"reference_mm": 0, "reference_px": 10},
    ).status_code == 422
    assert client.post(
        f"/repository/inspections/{iid}/calibration", headers=auth_headers,
        json={"px_per_mm": 8, "packaging_form": "nonsense"},
    ).status_code == 422


def test_measurement_preview_reports_what_is_missing(client, auth_headers, _label_png):
    iid = client.post("/inspections", headers=auth_headers).json()["id"]
    client.post(f"/inspections/{iid}/images", headers=auth_headers,
                data={"role": "FRONT"}, files={"file": ("label.png", _label_png, "image/png")})
    client.post(f"/inspections/{iid}/process", headers=auth_headers)
    preview = client.get(f"/repository/inspections/{iid}/measurement", headers=auth_headers).json()
    assert preview["available"] is False
    assert preview["px_per_mm"] is None
    assert "no scale calibration" in preview["method"]


def test_font_size_rule_is_seeded_and_exposed_in_the_rule_library(client, auth_headers):
    items = client.get("/rules", headers=auth_headers).json()["items"]
    rule = next(r for r in items if r["check_type"] == "font_size")
    assert rule["rule_number"].startswith("7")
    assert "Rule 7" in rule["source_reference"]
    assert [t["id"] for t in rule["font_size_tables"]] == ["Table-I", "Table-II"]
    assert rule["exempt_note"]
