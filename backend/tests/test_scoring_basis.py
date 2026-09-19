"""The scoring basis: what a compliance percentage means, and what it must never mean.

Locks the model the UI explains, so a future edit cannot quietly reintroduce the problems this
replaced:

* compliance is measured over DECIDED checks only — unread evidence earns no half credit;
* evidence coverage is reported separately, and is what a thinly-evidenced scan loses;
* weights and criticality are read from the rule data, not from code;
* being skipped for want of evidence is NOT a legal exemption — it counts as unverified;
* a preliminary pass needs no failure, the compliance threshold AND the coverage floor;
* history is pinned: a stored evaluation's weight is not re-read from the rule library.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.rules.registry import seed_rules
from backend.services.compliance_service import (
    AI_COMPLIANT_THRESHOLD,
    COVERAGE_FLOOR,
    effective_kind,
    evaluate_verdict,
    score_evaluations,
)
from backend.rules.applicability import KIND_EVIDENCE_MISSING, KIND_INAPPLICABLE


def _ev(
    rule_number: str,
    status: str,
    *,
    critical: bool | None = False,
    weight: float | None = None,
    check_type: str = "declaration_present",
    kind: str = "",
    reason: str = "",
) -> SimpleNamespace:
    """A stored rule evaluation (only the columns the scoring layer reads)."""
    return SimpleNamespace(
        rule_number=rule_number,
        status=status,
        critical=critical,
        weight=weight,
        check_type=check_type,
        applicability_kind=kind,
        reason=reason,
    )


# --------------------------------------------------------------- compliance vs coverage

def test_compliance_is_measured_over_decided_checks_only():
    rows = [
        _ev("6(1)(a)", "PASS", weight=1.0),
        _ev("6(1)(c)", "FAIL", weight=2.0, critical=True),
    ]
    result = score_evaluations(rows)
    # 1×1 of 3 decided weight = 33.3% compliance, and the whole rule set was decided.
    assert result["score"] == 33.3
    assert result["coverage"] == 100.0
    assert result["decided_weight"] == 3.0


def test_unverified_earns_no_credit_and_shows_up_as_coverage_loss():
    rows = [_ev("6(1)(a)", "PASS", weight=1.0), _ev("6(1)(b)", "UNCERTAIN", weight=1.0)]
    result = score_evaluations(rows)
    assert result["score"] == 100.0  # nothing decided was violated
    assert result["coverage"] == 50.0  # half the applicable set could not be verified
    assert [u["rule_number"] for u in result["unverified"]] == ["6(1)(b)"]
    assert [c["rule_number"] for c in result["counted"]] == ["6(1)(a)"]


def test_the_two_numbers_distinguish_a_failure_from_unreadable_evidence():
    """The whole point of the split: these two scans must not look alike."""
    one_failure = score_evaluations(
        [_ev("6(1)(a)", "PASS", weight=9.0), _ev("6(1)e", "FAIL", weight=1.0)]
    )
    unreadable = score_evaluations(
        [_ev("6(1)(a)", "PASS", weight=9.0), _ev("6(1)e", "UNCERTAIN", weight=1.0)]
    )
    assert one_failure["score"] == 90.0 and one_failure["coverage"] == 100.0
    assert unreadable["score"] == 100.0 and unreadable["coverage"] == 90.0


def test_nothing_decided_is_never_treated_as_success():
    result = score_evaluations([_ev("6(1)(a)", "UNCERTAIN", weight=1.0)])
    assert result["score"] == 0.0
    assert result["coverage"] == 0.0
    assert result["nothing_decided"] is True
    assert evaluate_verdict(result["score"], result["coverage"], False, True)[0] == "NEEDS_MANUAL_REVIEW"


def test_a_scan_with_no_evaluations_at_all_does_not_divide_by_zero():
    result = score_evaluations([])
    assert result["score"] == 0.0 and result["coverage"] == 0.0 and result["nothing_decided"] is True


# --------------------------------------------------------------- weights come from rule data

def test_weight_is_read_from_the_rule_data_not_from_code():
    # A rule that declares 3.0 must outweigh the critical-derived default of 2.0.
    rows = [_ev("9(1)", "PASS", weight=3.0), _ev("6(1)(a)", "FAIL", weight=1.0)]
    result = score_evaluations(rows)
    assert result["score"] == 75.0
    assert {c["rule_number"]: c["weight"] for c in result["counted"]} == {"9(1)": 3.0, "6(1)(a)": 1.0}


def test_criticality_is_the_fallback_when_a_rule_declares_no_weight():
    rows = [_ev("6(1)(c)", "PASS", critical=True, weight=None), _ev("6(1)(d)", "PASS", weight=None)]
    result = score_evaluations(rows)
    assert {c["rule_number"]: c["weight"] for c in result["counted"]} == {"6(1)(c)": 2.0, "6(1)(d)": 1.0}


def test_a_stored_weight_is_never_re_read_from_the_rule_library():
    """Pinning: the same rule now weighs 2.0, but this evaluation was scored at 1.0."""
    pinned = [_ev("6(1)(b)", "PASS", weight=1.0), _ev("6(1)(a)", "FAIL", weight=1.0)]
    assert score_evaluations(pinned)["score"] == 50.0


def test_rule_definitions_declare_their_weight_and_criticality(client):
    """The seeded library carries the declared data (net quantity and MRP are the heavy ones)."""
    from backend.database import SessionLocal
    from backend.models import Rule, RuleVersion

    db = SessionLocal()
    try:
        assert seed_rules(db)["created"] == 0, "the app already seeded the definitions"
        by_number = {}
        for rule in db.query(Rule).all():
            version = (
                db.query(RuleVersion)
                .filter(RuleVersion.rule_id_fk == rule.id, RuleVersion.version == rule.current_version)
                .first()
            )
            by_number[rule.rule_number] = version
        assert by_number["6(1)(c)"].weight == 2.0 and by_number["6(1)(c)"].critical is True
        assert by_number["6(1)(e)"].weight == 2.0 and by_number["6(1)(e)"].critical is True
        assert by_number["6(1)(a)"].weight == 1.0 and by_number["6(1)(a)"].critical is False
        assert by_number["7(2), 7(3)"].weight == 1.0
    finally:
        db.close()


# --------------------------------------------------------------- exclusions vs unverified

def test_manual_only_checks_are_never_scored():
    rows = [
        _ev("6(1)(a)", "PASS", weight=1.0),
        _ev("5", "UNCERTAIN", check_type="manual_only"),
        _ev("4", "NOT_APPLICABLE", check_type="manual_only"),
    ]
    result = score_evaluations(rows)
    assert result["score"] == 100.0 and result["coverage"] == 100.0
    assert {e["kind"] for e in result["excluded"]} == {"MANUAL_ONLY"}


def test_legally_inapplicable_rules_stay_out_of_the_maths():
    rows = [
        _ev("6(1)(a)", "PASS", weight=1.0),
        _ev("6(1)(g)", "NOT_APPLICABLE", kind=KIND_INAPPLICABLE, reason="rule applies to categories ['FOOD']"),
    ]
    result = score_evaluations(rows)
    assert result["coverage"] == 100.0
    assert [e["rule_number"] for e in result["excluded"]] == ["6(1)(g)"]
    assert result["unverified"] == []


def test_a_rule_skipped_for_want_of_evidence_counts_as_unverified():
    rows = [
        _ev("6(1)(a)", "PASS", weight=1.0),
        _ev("6(1)(h)", "NOT_APPLICABLE", kind=KIND_EVIDENCE_MISSING, reason="no import evidence in images"),
    ]
    result = score_evaluations(rows)
    # It must not vanish from the maths: coverage reflects that it was never established.
    assert result["coverage"] == 50.0
    assert [u["kind"] for u in result["unverified"]] == ["EVIDENCE_MISSING"]
    assert result["excluded"] == []


def test_legacy_rows_without_a_kind_are_classified_from_their_reason():
    import_rule = _ev("6(1)(h)", "NOT_APPLICABLE", reason="no import evidence in images; import-specific rule not applicable")
    scope_rule = _ev("6(1)(g)", "NOT_APPLICABLE", reason="rule applies to categories ['FOOD']; package classified as OTHER")
    assert effective_kind(import_rule) == KIND_EVIDENCE_MISSING
    # a category-scoped rule decided on a CONFIRMED category is a real legal exclusion
    assert effective_kind(scope_rule) == KIND_INAPPLICABLE


# --------------------------------------------------------------- the gate

def test_verdict_requires_no_failure_the_threshold_and_the_coverage_floor():
    assert evaluate_verdict(AI_COMPLIANT_THRESHOLD, COVERAGE_FLOOR, False, False)[0] == "COMPLIANT"
    assert evaluate_verdict(100.0, COVERAGE_FLOOR, True, False)[0] == "NON_COMPLIANT"
    assert evaluate_verdict(AI_COMPLIANT_THRESHOLD - 0.1, 100.0, False, False)[0] == "NEEDS_MANUAL_REVIEW"
    assert evaluate_verdict(100.0, COVERAGE_FLOOR - 0.1, False, False)[0] == "NEEDS_MANUAL_REVIEW"
    assert evaluate_verdict(0.0, 0.0, False, True)[0] == "NEEDS_MANUAL_REVIEW"


def test_the_shortfall_is_explained_in_words():
    _, compliance_short = evaluate_verdict(60.0, 100.0, False, False)
    _, coverage_short = evaluate_verdict(100.0, 40.0, False, False)
    _, failed = evaluate_verdict(100.0, 100.0, True, False)
    assert "below the" in compliance_short and "threshold" in compliance_short
    assert "could be verified" in coverage_short and "floor" in coverage_short
    assert failed == "a failed requirement"


def test_the_default_coverage_floor_is_reachable_by_a_well_evidenced_scan():
    """A floor nobody can clear would make the threshold rule meaningless.

    One unverifiable requirement out of a typical applicable rule set must still leave room for a
    preliminary pass; the floor is only there to block thinly-evidenced scans.
    """
    rows = [_ev(f"6(1){suffix}", "PASS", weight=1.0) for suffix in "abcdef"]
    rows.append(_ev("7(2), 7(3)", "UNCERTAIN", weight=1.0))  # font size needs calibration
    result = score_evaluations(rows)
    assert result["coverage"] > COVERAGE_FLOOR
    assert evaluate_verdict(result["score"], result["coverage"], False, result["nothing_decided"])[0] == "COMPLIANT"
