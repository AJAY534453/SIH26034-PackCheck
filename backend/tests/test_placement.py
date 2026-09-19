"""Placement check (Rule 6(1) / Rule 9 / Rule 7(1)) — evidence-based location reporting.

The check must never convert "we could not see it on the panel we captured" into a violation, and it
must never claim that the photographed surface IS the principal display panel.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.rules.engine import CHECKS, check_placement
from backend.rules.placement import build_panel_evidence

PARAMS = {
    "fields": ["net_quantity", "mrp", "manufacturer"],
    "panel_roles": ["FRONT", "TOP"],
    "exempt_rule7_1": "Rule 7(1): a card or tape may serve as the principal display panel.",
}


def _rv():
    return SimpleNamespace(
        source_reference="LM (PC) Rules, 2011 — Rule 6(1), Rule 9, Rule 7(1)",
        requirement="Declarations borne on the package and prominent.",
    )


def _context(field_roles: dict[str, str], roles_supplied=("FRONT", "BACK")):
    views = [{"image_id": i + 1, "role": role, "quality_status": "GOOD", "filename": f"{role}.png"}
             for i, role in enumerate(roles_supplied)]
    role_to_id = {v["role"]: v["image_id"] for v in views}
    fields = {
        name: {
            "image_id": role_to_id.get(role),
            "role": role,
            "state": "DETECTED",
            "display_value": value,
        }
        for name, (role, value) in field_roles.items()
    }
    return {
        "panel_evidence": {
            "views": views,
            "roles_supplied": list(roles_supplied),
            "fields": fields,
        }
    }


def test_placement_check_is_registered():
    assert "placement" in CHECKS


def test_all_watched_declarations_on_a_panel_view_pass_with_the_caveat_stated():
    ctx = _context({"net_quantity": ("FRONT", "750 g"), "mrp": ("FRONT", "₹220.00"), "manufacturer": ("TOP", "AVA")})
    status, reason, observed, confidence, detail = check_placement(_rv(), PARAMS, {}, ctx)
    assert status == "PASS"
    assert detail["satisfied"] is True
    # The PASS must not certify the surface as the principal display panel: it reports where the
    # declarations were photographed and says so.
    assert "does not certify" in reason
    assert "Rule 7(1)" in reason
    assert confidence < 0.7


def test_declarations_only_on_a_side_view_are_uncertain_never_fail():
    ctx = _context({"net_quantity": ("BACK", "750 g"), "mrp": ("BACK", "₹220.00")})
    status, reason, _, _, detail = check_placement(_rv(), PARAMS, {}, ctx)
    assert status == "UNCERTAIN"
    assert "principal display panel" in reason
    assert detail["off_panel"]
    assert detail["on_panel"] == []


def test_split_across_views_is_uncertain():
    ctx = _context({"net_quantity": ("FRONT", "750 g"), "mrp": ("BACK", "₹220.00")})
    status, reason, _, _, _ = check_placement(_rv(), PARAMS, {}, ctx)
    assert status == "UNCERTAIN"
    assert "split across views" in reason


def test_no_roles_assigned_is_uncertain_and_says_what_to_do():
    ctx = _context({"mrp": ("", "₹220.00")}, roles_supplied=("",))
    status, reason, _, _, detail = check_placement(_rv(), PARAMS, {}, ctx)
    assert status == "UNCERTAIN"
    assert "no view role assigned" in reason
    assert detail["role_unknown"] == ["mrp"]


def test_nothing_detected_is_uncertain_and_is_not_read_as_a_placement_breach():
    ctx = _context({})
    status, reason, _, _, _ = check_placement(_rv(), PARAMS, {}, ctx)
    assert status == "UNCERTAIN"
    assert "not evidence of a placement breach" in reason


def test_no_watch_list_configured_is_uncertain():
    status, reason, _, _, _ = check_placement(_rv(), {}, {}, _context({}))
    assert status == "UNCERTAIN"
    assert "watch-list" in reason


def test_a_bare_anchor_label_is_not_treated_as_a_detected_declaration():
    # "Manufactured by" with no entity is an anchor, not a declaration — it must not count as
    # evidence that a declaration appears on a panel.
    ctx = _context({"manufacturer": ("FRONT", "Manufactured by")})
    status, reason, _, _, detail = check_placement(_rv(), PARAMS, {}, ctx)
    assert status == "UNCERTAIN"
    assert "manufacturer" in detail["not_detected"]
    assert detail["on_panel"] == []
    assert "not evidence of a placement breach" in reason


def test_build_panel_evidence_maps_each_field_to_the_view_it_came_from():
    images = [
        SimpleNamespace(id=1, role="FRONT", quality_status="GOOD", original_filename="f.png"),
        SimpleNamespace(id=2, role="BACK", quality_status="POOR", original_filename="b.png"),
    ]
    field_rows = {
        "mrp": SimpleNamespace(source_image_id=2, state="DETECTED", display_value="₹220"),
        "manufacturer": SimpleNamespace(source_image_id=1, state="DETECTED", display_value="AVA"),
        "brand": SimpleNamespace(source_image_id=None, state="DETECTED", display_value="MEDIMIX"),
    }
    evidence = build_panel_evidence(images, field_rows)
    assert evidence["roles_supplied"] == ["FRONT", "BACK"]
    assert evidence["fields"]["mrp"]["role"] == "BACK"
    assert evidence["fields"]["manufacturer"]["role"] == "FRONT"
    assert evidence["fields"]["brand"]["role"] == ""


@pytest.mark.parametrize("role", ["LEFT_SIDE", "RIGHT_SIDE", "BOTTOM", "CLOSE_UP"])
def test_non_panel_roles_never_produce_a_pass_on_their_own(role):
    ctx = _context({"mrp": (role, "₹220.00")}, roles_supplied=(role,))
    status, _, _, _, _ = check_placement(_rv(), PARAMS, {}, ctx)
    assert status == "UNCERTAIN"
