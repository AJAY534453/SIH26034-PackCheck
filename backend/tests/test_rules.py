"""Rule engine + decision logic tests."""
from __future__ import annotations

from backend.rules.decision import decide_inspection
from backend.rules.engine import RuleOutcome


def _outcome(status: str, critical: bool = False, rule_number: str = "6(1)(x)", check_type: str = "declaration_present") -> RuleOutcome:
    return RuleOutcome(
        rule_id="T", rule_version_id=1, rule_number=rule_number, title="t", requirement="r",
        status=status, reason="reason", observed="obs", expected="exp",
        confidence=0.8, critical=critical, check_type=check_type,
    )


def test_compliant_when_all_pass():
    decision, summary = decide_inspection([_outcome("PASS"), _outcome("PASS", rule_number="9(1)")], "DETECTED", [])
    assert decision == "COMPLIANT"
    assert "AI-assisted" in summary


def test_non_compliant_only_on_critical_fail():
    decision, _ = decide_inspection([_outcome("PASS"), _outcome("FAIL", critical=True, rule_number="6(1)(e)")], "DETECTED", [])
    assert decision == "NON_COMPLIANT"


def test_non_critical_fail_is_still_non_compliant():
    """Priority 6 policy: any applicable rule that FAILed with sufficient evidence →
    NON_COMPLIANT (the engine only emits FAIL from positive/human-confirmed evidence;
    criticality affects violation severity, not the decision)."""
    decision, summary = decide_inspection([_outcome("PASS"), _outcome("FAIL", critical=False)], "DETECTED", [])
    assert decision == "NON_COMPLIANT"
    assert "failed with sufficient evidence" in summary.lower()


def test_uncertain_rule_forces_review():
    decision, _ = decide_inspection([_outcome("PASS"), _outcome("UNCERTAIN")], "DETECTED", [])
    assert decision == "NEEDS_MANUAL_REVIEW"


def test_conflicting_field_forces_review():
    decision, summary = decide_inspection([_outcome("PASS")], "DETECTED", ["mrp"])
    assert decision == "NEEDS_MANUAL_REVIEW"
    assert "mrp" in summary


def test_uncertain_classification_blocks_compliant():
    decision, summary = decide_inspection([_outcome("PASS")], "UNCERTAIN", [])
    assert decision == "NEEDS_MANUAL_REVIEW"
    assert "classification" in summary.lower()


def test_manual_only_rules_do_not_block_compliant():
    outcomes = [_outcome("PASS"), _outcome("UNCERTAIN", check_type="manual_only", rule_number="5")]
    decision, summary = decide_inspection(outcomes, "DETECTED", [])
    assert decision == "COMPLIANT"
    assert "manual" in summary.lower()


def test_not_applicable_ignored():
    decision, _ = decide_inspection([_outcome("NOT_APPLICABLE")], "DETECTED", [])
    assert decision == "NEEDS_MANUAL_REVIEW"  # no applicable rules evaluated at all
