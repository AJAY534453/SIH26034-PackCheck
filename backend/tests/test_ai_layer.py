"""Vision-layer tests.

The contract this layer must keep, and the reasons these tests exist:

1. With no provider configured, nothing is fabricated and nothing is called out to the network.
2. A model reading that the on-device OCR cannot corroborate is NEVER published as a detected
   fact — it is offered with capped confidence and flagged for review.
3. A reading the OCR does corroborate keeps its confidence and is not needlessly flagged.
4. Malformed model output is rejected, not guessed at. Null values are dropped (never invented).
5. A vision reading entering the pipeline is subject to exactly the same deterministic
   validation, ranking and conflict logic as an OCR candidate.
"""
from __future__ import annotations

import pytest

from backend.ai import provider_status
from backend.ai.extractor import (
    _coerce_bbox,
    _observations_from,
    _ocr_needle_hit,
    _parse_json_object,
    extract_bill_fields,
    extract_label_fields,
    to_candidates,
)
from backend.ai.schema import AI_FIELD_NAMES, AIFieldObservation
from backend.config import settings


def test_provider_disabled_without_key(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "AI_ENABLED", "auto", raising=False)
    status = provider_status()
    assert status.enabled is False
    assert "on-device OCR" in status.reason


def test_provider_enabled_when_key_present(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key-not-real", raising=False)
    monkeypatch.setattr(settings, "AI_ENABLED", "auto", raising=False)
    assert provider_status().enabled is True


def test_ai_disabled_flag_wins_over_key(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key-not-real", raising=False)
    monkeypatch.setattr(settings, "AI_ENABLED", "0", raising=False)
    assert provider_status().enabled is False


def test_extract_label_fields_without_provider_never_calls_out(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "AI_ENABLED", "auto", raising=False)

    def _boom(*_args, **_kwargs):  # pragma: no cover - must never execute
        raise AssertionError("no provider call may happen when none is configured")

    monkeypatch.setattr("backend.ai.extractor.run_vision_json", _boom)
    result = extract_label_fields([(b"not-an-image", "image/jpeg")])
    assert result.used is False
    assert result.observations == []
    assert "on-device OCR" in result.error

    bill = extract_bill_fields([(b"not-an-image", "image/jpeg")])
    assert bill.used is False and bill.observations == []


def test_parse_json_object_strips_code_fences_and_recovers_prose():
    assert _parse_json_object('```json\n{"fields": []}\n```') == {"fields": []}
    assert _parse_json_object('Here is the JSON: {"fields": []} — done') == {"fields": []}
    with pytest.raises(ValueError):
        _parse_json_object("no json at all")


def test_observations_drop_nulls_and_unknown_fields():
    """The model must not be able to invent a field, or have a null become a value."""
    data = {
        "fields": [
            {"field": "mrp", "value": "₹ 200.00", "confidence": 0.9, "source_image": 1},
            {"field": "mrp", "value": None, "confidence": 0.9},
            {"field": "not_a_tracked_field", "value": "invented", "confidence": 0.9},
            {"field": "net_quantity", "value": "500 g", "confidence": 5},  # out of range → clamped
            {"field": "brand", "value": "   "},  # blank → dropped
        ]
    }
    obs = _observations_from(data, set(AI_FIELD_NAMES))
    assert [o.field for o in obs] == ["mrp", "net_quantity"]
    assert obs[0].value == "₹ 200.00"
    assert obs[1].confidence == 1.0


def test_marketed_by_alias_maps_to_pipeline_field():
    obs = _observations_from(
        {"fields": [{"field": "marketed_by", "value": "ACME MARKETING PVT LTD", "confidence": 0.8}]},
        set(AI_FIELD_NAMES),
    )
    assert obs[0].field == "marketer"


def test_bbox_is_only_accepted_in_normalised_form():
    assert _coerce_bbox([0.1, 0.2, 0.3, 0.1]) == [0.1, 0.2, 0.3, 0.1]
    assert _coerce_bbox([0, 0, 0, 0]) is None  # unusable region
    assert _coerce_bbox([120, 40, 300, 60]) is None  # pixels, not normalised — do not guess
    assert _coerce_bbox("nonsense") is None


def test_ocr_corroboration_detects_agreement_and_disagreement():
    assert _ocr_needle_hit("500 g", "NETWT500G") >= 0.82
    assert _ocr_needle_hit("SUKKUKAAPI", "SUKKUKAAPIMASALA") >= 0.82
    # A hallucinated value has nothing to match against.
    assert _ocr_needle_hit("GOLDEN DELUXE 9000", "NETWT500GMRP20000") < 0.6


def test_uncorroborated_reading_is_capped_and_flagged_for_review():
    obs = [
        AIFieldObservation(field="brand", value="GURUCHARA", confidence=0.95, source_image="1"),
        AIFieldObservation(field="net_quantity", value="500 g", confidence=0.93, source_image="1"),
    ]
    cands = to_candidates(
        obs,
        ocr_text_by_index={1: "NETWT500G"},
        dims_by_index={1: (1000, 2000)},
        image_id_by_index={1: 7},
    )
    by_field = {c.field_name: c for c in cands}
    # not corroborated → capped, flagged inferred (the pipeline records these as UNCERTAIN)
    assert by_field["brand"].confidence <= 0.62
    assert by_field["brand"].inferred is True
    assert "not corroborated" in by_field["brand"].reason
    # corroborated → keeps the model's confidence and is not flagged
    assert by_field["net_quantity"].confidence == pytest.approx(0.93)
    assert by_field["net_quantity"].inferred is False
    assert by_field["net_quantity"].engine.startswith("ai_vision")


def test_bbox_is_mapped_into_source_pixels():
    obs = [AIFieldObservation(field="mrp", value="MRP 200", confidence=0.9, source_image="1", bbox=[0.1, 0.5, 0.2, 0.1])]
    cands = to_candidates(
        obs,
        ocr_text_by_index={1: "MRP200"},
        dims_by_index={1: (1000, 2000)},
        image_id_by_index={1: 3},
    )
    assert cands[0].bbox == (100, 1000, 300, 1200)
    assert cands[0].source_image_id == 3


def test_vision_candidate_flows_through_the_deterministic_pipeline(client, auth_headers, monkeypatch, _label_png):
    """A vision reading must be ranked, validated and REVIEWED like any other candidate.

    This is the integration guarantee: the AI cannot bypass extraction, normalization,
    field validation or the rule engine — it only adds one more observation.
    """
    import backend.services.inspection_service as svc
    from backend.extraction.base import Candidate

    def fake_ai(images):
        return (
            [
                Candidate(
                    field_name="mrp",
                    value="₹ 200.00",
                    raw_value="MRP ₹200.00",
                    confidence=0.97,
                    score=0.97,
                    engine="ai_vision:test",
                    variant="vision",
                    reason="test reading corroborated by OCR",
                )
            ],
            "Vision extraction: test stub",
        )

    monkeypatch.setattr("backend.ai.ai_field_candidates", fake_ai)

    r = client.post("/inspections", headers=auth_headers)
    iid = r.json()["id"]
    client.post(
        f"/inspections/{iid}/images",
        headers=auth_headers,
        data={"role": "FRONT"},
        files={"file": ("label.png", _label_png, "image/png")},
    )
    assert client.post(f"/inspections/{iid}/process", headers=auth_headers).status_code == 200

    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    mrp = next(f for f in detail["fields"] if f["field_name"] == "mrp")
    # The value is present, normalized by the SAME validator, and it carries its own provenance.
    assert mrp["display_value"]
    assert mrp["source_engine"] == "ai_vision:test"
    assert mrp["confidence"] > 0
    # And the rule engine still produced a decision from validated facts only.
    assert detail["final_decision"] in ("COMPLIANT", "NON_COMPLIANT", "NEEDS_MANUAL_REVIEW")
    # The provider stage is recorded honestly, and the always-on on-device engine ran too.
    assert detail["stage_status"].get("provider_extraction") == "done"
    assert detail["stage_status"].get("visual_analysis") == "done"
    assert detail["provider_status"] == "USED"
    assert detail["vision_status"] == "COMPLETED_WITH_PROVIDER"


def test_on_device_vision_always_runs_even_without_a_provider(client, auth_headers, _label_png):
    """The root cause of "vision never ran": with no provider key the second perception source
    was simply skipped. The on-device engine now runs regardless, and the ONE thing that is
    skipped is the optional provider call — reported as skipped, never as a silent success."""
    r = client.post("/inspections", headers=auth_headers)
    iid = r.json()["id"]
    client.post(
        f"/inspections/{iid}/images",
        headers=auth_headers,
        data={"role": "FRONT"},
        files={"file": ("label.png", _label_png, "image/png")},
    )
    client.post(f"/inspections/{iid}/process", headers=auth_headers)
    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()

    assert detail["stage_status"].get("visual_analysis") == "done"
    assert detail["stage_status"].get("provider_extraction") == "skipped"
    assert detail["provider_status"] == "NOT_CONFIGURED"
    assert detail["vision_status"] == "ON_DEVICE_ONLY_NO_PROVIDER"
    assert detail["vision_engine"] == "on-device-vision:1"

    # The observations are retained as regions with real pixel measurements, and the readability
    # verdict is recorded separately from the physical font-size requirement.
    regions = detail["vision"]["regions"]
    assert regions, "on-device vision retained no regions"
    kinds = {r["kind"] for r in regions}
    assert "LEGIBILITY" in kinds
    legibility = next(r for r in regions if r["kind"] == "LEGIBILITY")
    assert legibility["note"] and "Rule 7" in legibility["note"]
    # Per-stage timing is recorded on the record itself, not inferred by the client.
    timings = detail["stage_timings"]
    assert timings.get("visual_analysis", 0) >= 0
    assert detail["duration_ms"] > 0
    assert "Vision extraction skipped" in (detail["notes"] or "")
