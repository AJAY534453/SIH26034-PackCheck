"""Product scan repository, automated compliance review, threshold workflow and finalization.

Covers the requirement that:
* every processed scan is stored with its evidence, checks, score and legal references;
* the compliance percentage over decided checks, the evidence-coverage figure and the 85% threshold
  decide between an AI preliminary pass and a flagged scan;
* the AI preliminary verdict and the human official decision are distinct, and an unfinalized result
  is never presented to a normal user as approved or rejected.

The scoring model itself is specified in more depth in test_scoring_basis.py.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.models.product_scan import PENDING_MESSAGE
from backend.services.compliance_service import (
    AI_COMPLIANT_THRESHOLD,
    score_evaluations,
)


# ----------------------------------------------------------------- score + verdict logic

def _evaluation(rule_number, status, critical=False, check_type="declaration_present"):
    return SimpleNamespace(
        rule_number=rule_number, status=status, critical=critical, check_type=check_type
    )


def test_score_is_weighted_with_critical_rules_counted_twice():
    rows = [
        _evaluation("6(1)(a)", "PASS"),
        _evaluation("6(1)(c)", "PASS", critical=True),
        _evaluation("6(1)(e)", "FAIL", critical=True),
    ]
    result = score_evaluations(rows)
    # (1×1 + 2×1 + 2×0) / (1 + 2 + 2) = 60%
    assert result["score"] == 60.0
    assert result["denominator_weight"] == 5.0


def test_not_applicable_and_manual_only_checks_are_excluded_from_the_score():
    rows = [
        _evaluation("6(1)(a)", "PASS"),
        _evaluation("5", "UNCERTAIN", check_type="manual_only"),
        _evaluation("13(1)", "NOT_APPLICABLE"),
    ]
    result = score_evaluations(rows)
    assert result["score"] == 100.0
    assert len(result["counted"]) == 1 and len(result["excluded"]) == 2


def test_unverified_evidence_earns_no_half_credit_and_shows_as_coverage_loss():
    """UNCERTAIN is not half-compliance: it earns nothing and is reported as coverage lost."""
    result = score_evaluations([_evaluation("6(1)(a)", "UNCERTAIN")])
    assert result["score"] == 0.0
    assert result["coverage"] == 0.0
    assert result["nothing_decided"] is True


def test_threshold_constant_is_eighty_five():
    assert AI_COMPLIANT_THRESHOLD == 85.0


# ----------------------------------------------------------------- integration fixtures

def _processed_inspection(client, headers, role="FRONT"):
    iid = client.post("/inspections", headers=headers).json()["id"]
    # a blank label: OCR finds nothing, so no declaration is ever asserted (no fabricated results)
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, "PNG")
    assert client.post(
        f"/inspections/{iid}/images", headers=headers, data={"role": role},
        files={"file": ("label.png", buf.getvalue(), "image/png")},
    ).status_code == 200
    assert client.post(f"/inspections/{iid}/process", headers=headers).status_code == 200
    return iid


def _scan_for(client, headers, iid):
    listing = client.get("/repository/scans?page_size=100", headers=headers).json()
    return next(s for s in listing["items"] if s["inspection_id"] == iid)


# ----------------------------------------------------------------- repository lifecycle

def test_processing_creates_a_repository_scan_with_checks_and_a_score(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    card = _scan_for(client, auth_headers, iid)
    assert card["compliance_score"] is not None
    assert card["ai_verdict"] in ("COMPLIANT", "NON_COMPLIANT", "NEEDS_MANUAL_REVIEW")
    assert card["review_status"] in ("AI_PRELIMINARY", "PENDING_FINALIZATION")
    # the result is preliminary until an official decides
    assert card["status_message"] == PENDING_MESSAGE or card["review_status"] == "AI_PRELIMINARY"

    detail = client.get(f"/repository/scans/{card['id']}", headers=auth_headers).json()
    assert detail["checks"], "every scan stores its per-requirement checks"
    assert detail["rules_snapshot"], "the rule versions used at scan time are retained"
    for check in detail["checks"]:
        assert check["status"] in ("PASS", "FAIL", "UNCERTAIN", "NOT_APPLICABLE")
        assert "rule_number" in check and "explanation" in check
    # the score is traceable to the checks, not a black box
    assert detail["scoring"]["method"].startswith("compliance % = 100")
    assert "coverage %" in detail["scoring"]["method"]
    assert detail["coverage_score"] == detail["scoring"]["coverage"]
    # the basis is itemised, so a percentage always comes with its reason
    assert "counted" in detail["scoring"] and "excluded" in detail["scoring"]
    assert "coverage_floor" in detail["scoring"]
    assert detail["threshold"] == AI_COMPLIANT_THRESHOLD
    assert detail["ai_verdict_is_preliminary"] is True


def test_repository_list_supports_search_filter_and_sort(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    card = _scan_for(client, auth_headers, iid)

    # search by scan number
    found = client.get(f"/repository/scans?q={card['inspection_number']}", headers=auth_headers).json()
    assert any(s["inspection_number"] == card["inspection_number"] for s in found["items"])

    # filter by review status (facet-driven)
    status = card["review_status"]
    filtered = client.get(f"/repository/scans?review_status={status}", headers=auth_headers).json()
    assert all(s["review_status"] == status for s in filtered["items"])
    assert filtered["total"] >= 1

    # sort by score ascending is a real ordering
    ascending = client.get("/repository/scans?sort=score_asc&page_size=100", headers=auth_headers).json()["items"]
    scores = [s["compliance_score"] for s in ascending]
    assert scores == sorted(scores)

    # a nonsense sort/filter value is rejected rather than silently ignored
    assert client.get("/repository/scans?sort=nonsense", headers=auth_headers).status_code == 422
    assert client.get("/repository/scans?page=0", headers=auth_headers).status_code == 422

    # facets always accompany the page, so the UI filters are data-derived
    assert "review_status" in found["facets"] and "ai_verdict" in found["facets"]


def test_finalization_requires_a_remark_and_records_the_official_decision(client, auth_headers, admin_headers):
    iid = _processed_inspection(client, auth_headers)
    card = _scan_for(client, auth_headers, iid)

    # a remark is mandatory — it is the recorded reason for the official decision
    assert client.post(f"/repository/scans/{card['id']}/finalize", headers=auth_headers,
                       json={"decision": "COMPLIANT", "remarks": "  "}).status_code == 422
    assert client.post(f"/repository/scans/{card['id']}/finalize", headers=auth_headers,
                       json={"decision": "NOT_A_DECISION", "remarks": "x"}).status_code == 422

    r = client.post(f"/repository/scans/{card['id']}/finalize", headers=auth_headers,
                    json={"decision": "NEEDS_MANUAL_REVIEW", "remarks": "Reviewed the label physically."})
    assert r.status_code == 200, r.text
    scan = r.json()["scan"]
    assert scan["review_status"] == "FINALIZED"
    assert scan["official_decision"] == "NEEDS_MANUAL_REVIEW"
    assert scan["finalized_by"] == "inspector"
    assert scan["finalized_at"]
    assert scan["remarks"] == "Reviewed the label physically."
    assert "Final decision recorded" in scan["status_message"]

    # the official decision is separate from the AI verdict (which is unchanged)
    assert scan["ai_verdict"] in ("COMPLIANT", "NON_COMPLIANT", "NEEDS_MANUAL_REVIEW")
    assert scan["ai_verdict_is_preliminary"] is True

    # and it is recorded on the inspection too, as one record with one final decision
    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    assert detail["official_decision"] == "NEEDS_MANUAL_REVIEW"
    assert detail["finalized_by"] == "inspector"

    # it also lands in the audit trail with the actor and the before/after state
    audit = client.get("/audit?action=compliance_finalized", headers=admin_headers)
    assert audit.status_code == 200
    rows = audit.json()["items"]
    assert any(row["actor"] == "inspector" and "NEEDS_MANUAL_REVIEW" in (row["after"] or "") for row in rows)


def test_only_authorized_officials_can_finalize(client, auth_headers, viewer_headers):
    iid = _processed_inspection(client, auth_headers)
    card = _scan_for(client, auth_headers, iid)
    # a read-only viewer can READ the repository but not finalize (server-side, not UI-only)
    assert client.get("/repository/scans", headers=viewer_headers).status_code == 200
    assert client.get(f"/repository/scans/{card['id']}", headers=viewer_headers).status_code == 200
    r = client.post(f"/repository/scans/{card['id']}/finalize", headers=viewer_headers,
                    json={"decision": "COMPLIANT", "remarks": "looks fine"})
    assert r.status_code == 403
    assert client.post(f"/repository/scans/{card['id']}/remarks", headers=viewer_headers,
                       json={"remarks": "note"}).status_code == 403


def test_pending_result_is_never_presented_as_final_to_a_normal_user(client, auth_headers, viewer_headers):
    iid = _processed_inspection(client, auth_headers)
    card = _scan_for(client, auth_headers, iid)

    # a non-reviewer sees the pending wording and NO official decision before finalization
    viewer_view = client.get(f"/repository/scans/{card['id']}", headers=viewer_headers).json()
    assert viewer_view["is_finalized"] is False
    assert viewer_view["official_decision"] is None
    assert viewer_view["status_message"] == PENDING_MESSAGE
    assert viewer_view["remarks"] == ""  # review working notes are not exposed
    assert viewer_view["can_finalize"] is False

    # once an official decides, the same user sees the final decision
    client.post(f"/repository/scans/{card['id']}/finalize", headers=auth_headers,
                json={"decision": "COMPLIANT", "remarks": "Verified against the physical package."})
    after = client.get(f"/repository/scans/{card['id']}", headers=viewer_headers).json()
    assert after["is_finalized"] is True and after["official_decision"] == "COMPLIANT"
    assert "Final decision recorded" in after["status_message"]


def test_repeat_scans_of_one_product_keep_a_compliance_history(client, auth_headers):
    first, second = _processed_inspection(client, auth_headers), _processed_inspection(client, auth_headers)
    for iid in (first, second):
        # give both scans the same identity so they belong to one product
        r = client.post(f"/inspections/{iid}/review/field", headers=auth_headers,
                        json={"action": "EDIT_FIELD", "field_name": "product_name",
                              "corrected_value": "Repository Test Product",
                              "reason": "test fixture identity"})
        assert r.status_code == 200, r.text

    card = _scan_for(client, auth_headers, second)
    assert card["product_id"], "a scan with a readable name is linked to a product"
    history = client.get(f"/repository/scans/{card['id']}", headers=auth_headers).json()["history"]
    assert len(history) >= 1, "the earlier scan of the same product is retained as history"

    product = client.get(f"/repository/products/{card['product_id']}", headers=auth_headers).json()
    assert product["product"]["scan_count"] >= 2
    assert len(product["scans"]) >= 2
    assert product["trend"], "the history exposes the score trend"
    # every history entry keeps its own score, verdict and status — nothing is overwritten
    for scan in product["scans"]:
        assert "compliance_score" in scan and "review_status" in scan
        assert scan["status_message"]


def test_repository_is_organization_scoped(client, auth_headers):
    _processed_inspection(client, auth_headers)
    entity = client.post(
        "/auth/login", json={"username": "entity", "password": "entity123", "include_token": True}
    ).json()
    entity_headers = {"Authorization": f"Bearer {entity['access_token']}"}

    # the regulated entity belongs to a different organization: it may read the repository, but the
    # inspector's scans are not in its scope at all (the filter is in the query, not the UI)
    entity_listing = client.get("/repository/scans", headers=entity_headers).json()
    assert entity_listing["total"] == 0
    assert entity_listing["items"] == []

    staff = client.get("/repository/scans", headers=auth_headers).json()
    assert staff["total"] >= 1

    # and an out-of-scope scan id is reported as not found rather than confirmed to exist
    card = staff["items"][0]
    assert client.get(f"/repository/scans/{card['id']}", headers=entity_headers).status_code == 404
    assert client.get(f"/repository/scans/{card['id']}", headers=auth_headers).status_code == 200


def test_backfill_is_admin_only_and_idempotent(client, auth_headers, admin_headers):
    _processed_inspection(client, auth_headers)
    assert client.post("/repository/backfill", headers=auth_headers).status_code == 403
    first = client.post("/repository/backfill", headers=admin_headers)
    assert first.status_code == 200
    second = client.post("/repository/backfill", headers=admin_headers)
    assert second.status_code == 200
    assert second.json()["created"] == 0, "a scan row is created once per inspection"


def test_repository_summary_matches_the_listing(client, auth_headers):
    _processed_inspection(client, auth_headers)
    listing = client.get("/repository/scans?page_size=1", headers=auth_headers).json()
    summary = client.get("/repository/summary", headers=auth_headers).json()
    assert summary["total_scans"] == listing["total"]
    assert summary["pending_finalization"] == listing["pending_finalization"]


def test_dashboard_exposes_the_finalization_queue(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    card = _scan_for(client, auth_headers, iid)
    stats = client.get("/dashboard/stats", headers=auth_headers).json()
    assert stats["product_scans"] >= 1
    assert stats["compliance_threshold"] == AI_COMPLIANT_THRESHOLD
    for row in stats["pending_finalization_queue"]:
        # a queue entry always names where to act and what the status is
        assert row["inspection_number"] and row["status_text"]
    if card["review_status"] != "FINALIZED":
        assert any(row["id"] == card["id"] for row in stats["pending_finalization_queue"])


def test_inspection_detail_carries_the_review_and_the_font_size_state(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    review = detail["compliance_review"]
    assert review["ai_verdict"] and review["threshold"]
    assert review["can_finalize"] is True
    assert detail["font_size"]["status"] in ("PASS", "FAIL", "UNCERTAIN", "NOT_APPLICABLE", "NOT_EVALUATED")
    assert detail["font_size"]["detail"] is not None
    assert detail["calibration"]["packaging_form"] == "normal"


def test_html_report_distinguishes_ai_and_official_verdicts(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    html = client.get(f"/reports/html/{iid}", headers=auth_headers).text
    assert "AI preliminary verdict" in html
    assert "Official final decision" in html
    assert "Automated compliance review" in html
    assert "Rule 7 letter-height measurement" in html
    assert "pdf-table" in html  # the wrapping table contract is intact


def test_grocery_and_reports_pages_still_work(client, auth_headers):
    """Regression guard: the new repository wiring must not break the existing pages' endpoints."""
    assert client.get("/products", headers=auth_headers).status_code == 200
    assert client.get("/violations", headers=auth_headers).status_code == 200
    assert client.get("/reports", headers=auth_headers).status_code == 200
    assert client.get("/rules", headers=auth_headers).status_code == 200
    assert client.get("/audit", headers=auth_headers).status_code == 403  # admin-only, unchanged
    assert client.get("/grocery", headers=auth_headers).status_code == 200
