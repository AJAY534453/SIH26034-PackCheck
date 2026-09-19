"""Inspection-level decision logic.

COMPLIANT: all applicable rules PASS and nothing requires manual attention.
NON_COMPLIANT: a critical applicable rule FAILED with sufficient evidence.
NEEDS_MANUAL_REVIEW: unresolved uncertainty anywhere — extraction, classification, or rules.

Never force uncertain cases into pass/fail.
"""
from __future__ import annotations

from backend.rules.applicability import KIND_EVIDENCE_MISSING
from backend.rules.engine import RuleOutcome


def decide_inspection(rule_outcomes: list[RuleOutcome], classification_state: str, conflicting_fields: list[str]) -> tuple[str, str]:
    """Return (final_decision, summary_reason)."""
    applicable = [o for o in rule_outcomes if o.status != "NOT_APPLICABLE"]
    failed = [o for o in applicable if o.status == "FAIL"]
    # Manual-only checks are permanently manual; they inform the reviewer but never block a decision.
    manual_only = [o for o in applicable if o.status == "UNCERTAIN" and o.check_type == "manual_only"]
    uncertain = [o for o in applicable if o.status == "UNCERTAIN" and o.check_type != "manual_only"]
    # NOT_APPLICABLE because no evidence established scope is NOT a legal exemption: the package may
    # well be in scope, so it must not be able to end in an unqualified COMPLIANT decision.
    scope_uncertain = [
        o for o in rule_outcomes
        if o.status == "NOT_APPLICABLE" and o.applicability_kind == KIND_EVIDENCE_MISSING
    ]

    reasons: list[str] = []

    if failed:
        # NON_COMPLIANT on ANY confirmed FAIL — the engine only emits FAIL with sufficient
        # evidence (positive evidence or human confirmation), so every FAIL is actionable.
        # Critical rules additionally raise severity (HIGH) on their violations.
        critical_failed = [o for o in failed if o.critical]
        titles = ", ".join(o.rule_number or o.title for o in failed)
        if critical_failed:
            reasons.append(
                f"Applicable critical requirement(s) failed with sufficient evidence: {titles}. "
                "This is a potential non-compliance pending human confirmation."
            )
        else:
            reasons.append(
                f"Applicable requirement(s) failed with sufficient evidence: {titles}. "
                "This is a potential non-compliance pending human confirmation."
            )
        return "NON_COMPLIANT", " ".join(reasons)

    if conflicting_fields:
        reasons.append(
            "Conflicting candidate values require human resolution before reliance: "
            + ", ".join(sorted(set(conflicting_fields)))
            + ". (The displayed value is the best-scored candidate; the alternative candidates are retained.)"
        )

    if classification_state in ("UNCERTAIN", "CONFLICTING"):
        reasons.append(
            f"Product classification is {classification_state}; category-specific requirements "
            "cannot be finalized automatically."
        )

    if uncertain:
        titles = ", ".join(o.rule_number or o.title for o in uncertain)
        reasons.append(f"Requirement(s) could not be verified from image evidence: {titles} — manual review required.")

    if scope_uncertain:
        titles = ", ".join(o.rule_number or o.title for o in scope_uncertain)
        reasons.append(
            f"Whether requirement(s) {titles} apply at all could not be established from image "
            "evidence (no evidence of the triggering condition) — treated as unverified, not as an "
            "exemption. Manual review required."
        )

    notes: list[str] = []
    if manual_only:
        titles = ", ".join(o.rule_number or o.title for o in manual_only)
        notes.append(
            f"Note: {titles} require physical/manual verification and were not evaluated automatically "
            "(this does not by itself require review of the automated checks)."
        )

    if reasons:
        return "NEEDS_MANUAL_REVIEW", " ".join(reasons + notes)

    if applicable:
        return "COMPLIANT", (
            "All applicable requirements evaluated from image evidence passed. This is an AI-assisted result; "
            "it does not replace physical inspection by an authorized Legal Metrology officer. "
            + " ".join(notes)
        ).strip()
    return "NEEDS_MANUAL_REVIEW", "No applicable rules could be evaluated from the available evidence."
