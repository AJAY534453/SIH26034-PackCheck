"""Automated AI compliance review — score, verdict, threshold and human-finalization rules.

The review is produced automatically for every processed scan. It is deterministic and
evidence-bound: every per-requirement check quotes the field/measurement it rests on and the legal
reference of the rule version that was evaluated, so the verdict is traceable to the evidence and
the law rather than to a language model's opinion.

Scoring (documented, single place — change it here and nothing else needs to move)

    TWO numbers are reported, because one number cannot honestly say both things at once:

    compliance %          how well the requirements that COULD be decided are met.
                          PASS = 1.0 credit, FAIL = 0.0 credit, over decided checks only:
                          100 × Σ(w × credit) / Σ(w of decided checks).
                          An unverified requirement earns nothing here (there is no half credit
                          for "we could not read it") — but it is not counted as a violation
                          either. No decided checks at all ⇒ 0% and never a pass-oriented verdict.
    evidence coverage %   how much of the applicable, machine-checkable rule set the evidence
                          actually allowed us to decide: 100 × Σ(w decided) / Σ(w applicable).
                          This is where an unreadable label shows up, as itself.

    Weight `w` is DECLARED PER RULE in backend/rules/definitions/*.json (seeded onto the rule
    version) and pinned onto each evaluation at write time, so editing a rule's weight never
    retroactively moves a stored scan's percentage. Manual-only checks are never scored. A
    NOT_APPLICABLE outcome leaves the maths only when its kind is INAPPLICABLE (out of scope by
    law/packaging/confirmed category); one that is NOT_APPLICABLE because evidence was missing
    counts as UNVERIFIED, so it can never silently raise a percentage.

Decision logic (per the application's defined rules, threshold and floor configurable)
    any FAIL                                       → NON_COMPLIANT (flagged for official finalization)
    no FAIL, nothing decided                       → NEEDS_MANUAL_REVIEW
    no FAIL and compliance ≥ threshold
              and coverage ≥ floor                 → COMPLIANT (AI preliminary pass-oriented verdict)
    otherwise                                      → NEEDS_MANUAL_REVIEW (official finalization)

The AI verdict is ALWAYS preliminary. Only an authorized human finalization writes an official
decision (see ``backend.services.repository_service.finalize_scan``).
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import ExtractedField, Inspection, Rule, RuleEvaluation, Violation

# Threshold above which the AI may issue a pass-oriented preliminary verdict.
AI_COMPLIANT_THRESHOLD = float(getattr(settings, "AI_COMPLIANCE_THRESHOLD", 85.0))

# Credits are granted only for DECIDED outcomes. UNCERTAIN is deliberately absent: an
# unverified requirement is neither proof of compliance nor a violation, so it moves the
# evidence-coverage figure instead of paying half credit for unread evidence.
CREDIT = {"PASS": 1.0, "FAIL": 0.0}
# Manual-only checks can never be automated, so they neither help nor hurt the score.
EXCLUDED_CHECK_TYPES = {"manual_only"}

VERDICT_COMPLIANT = "COMPLIANT"
VERDICT_NON_COMPLIANT = "NON_COMPLIANT"
VERDICT_NEEDS_REVIEW = "NEEDS_MANUAL_REVIEW"

# Fallback weight when a rule does not declare one. The rule definition owns the real value.
CRITICAL_WEIGHT = 2.0
DEFAULT_WEIGHT = 1.0

# Coverage floor an AI pass-oriented verdict additionally has to clear.
COVERAGE_FLOOR = float(getattr(settings, "AI_COVERAGE_FLOOR", 80.0))

METHOD_TEXT = (
    "compliance % = 100 × Σ(weight × credit) / Σ(weight) over DECIDED checks only "
    "(PASS=1.0, FAIL=0.0); coverage % = 100 × Σ(weight of decided) / Σ(weight of applicable, "
    "automatically checkable) — how much of the rule set the evidence allowed us to decide. "
    "Weights are declared per rule in the rule definitions and pinned per evaluation. "
    "Manual-only checks are never scored; a requirement excluded as legally inapplicable stays "
    "out of the maths, while one skipped for want of evidence counts as unverified."
)


def effective_kind(evaluation: RuleEvaluation) -> str:
    """APPLIES / INAPPLICABLE / EVIDENCE_MISSING for a stored evaluation.

    Evaluations written before applicability kinds existed carry an empty value, so the stored
    reason is used to recover the kind. Assuming every legacy exclusion was a legal exemption is
    exactly the silent score inflation this model exists to prevent.
    """
    kind = (getattr(evaluation, "applicability_kind", "") or "").upper()
    if kind:
        return kind
    reason = (getattr(evaluation, "reason", "") or "").lower()
    if "no import evidence" in reason or "cannot be established" in reason:
        return "EVIDENCE_MISSING"
    return "INAPPLICABLE"


def _weight(evaluation: RuleEvaluation) -> float:
    """Weight pinned on the evaluation; falls back to the criticality-derived default."""
    declared = getattr(evaluation, "weight", None)
    if declared is not None:
        try:
            value = float(declared)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return CRITICAL_WEIGHT if evaluation.critical else DEFAULT_WEIGHT


def score_evaluations(evaluations: list[RuleEvaluation]) -> dict:
    """Compliance percentage (decided checks) + evidence coverage (applicable rule set).

    Returns the full basis, not just a number: what was counted with its weight and credit, what
    could not be verified, and what was legitimately excluded and why. Everything the UI shows
    comes from here, so the display can never drift from the maths.
    """
    decided_weight = 0.0
    earned = 0.0
    applicable_weight = 0.0
    counted: list[dict] = []
    unverified: list[dict] = []
    excluded: list[dict] = []
    for e in evaluations:
        status = (e.status or "").upper()
        rule_number = getattr(e, "rule_number", "") or ""
        weight = _weight(e)
        check_type = getattr(e, "check_type", "") or ""
        reason = getattr(e, "reason", "") or ""

        if check_type in EXCLUDED_CHECK_TYPES:
            # Manual-only first: such a rule may also be NOT_APPLICABLE, and "manual verification
            # only" is the honest reason — never a legal exemption it did not claim.
            excluded.append(
                {"rule_number": rule_number, "reason": "manual verification only", "kind": "MANUAL_ONLY"}
            )
            continue

        if status == "NOT_APPLICABLE":
            kind = effective_kind(e)
            entry = {"rule_number": rule_number, "reason": reason or "not applicable", "kind": kind}
            if kind == "EVIDENCE_MISSING":
                # In scope in law, unestablished in evidence → unverified, never a free pass.
                unverified.append(
                    {
                        "rule_number": rule_number,
                        "reason": reason or "no evidence established whether this applies",
                        "kind": "EVIDENCE_MISSING",
                    }
                )
                applicable_weight += weight
            else:
                excluded.append(entry)
            continue

        applicable_weight += weight
        if status in CREDIT:
            credit = CREDIT[status]
            decided_weight += weight
            earned += weight * credit
            counted.append(
                {
                    "rule_number": rule_number,
                    "status": status,
                    "weight": weight,
                    "credit": credit,
                    "contribution": round(weight * credit, 2),
                }
            )
        else:
            # UNCERTAIN — or any other non-decided status: unverified, reported as coverage loss.
            unverified.append(
                {"rule_number": rule_number, "reason": reason or "not verified from evidence", "kind": status or "UNCERTAIN"}
            )

    score = round(100.0 * earned / decided_weight, 1) if decided_weight else 0.0
    coverage = round(100.0 * decided_weight / applicable_weight, 1) if applicable_weight else 0.0
    return {
        "score": score,
        "coverage": coverage,
        "counted": counted,
        "unverified": unverified,
        "excluded": excluded,
        "decided_weight": round(decided_weight, 2),
        "applicable_weight": round(applicable_weight, 2),
        "numerator_credit": round(earned, 2),
        "denominator_weight": round(decided_weight, 2),
        "nothing_decided": decided_weight <= 0,
        "threshold": AI_COMPLIANT_THRESHOLD,
        "coverage_floor": COVERAGE_FLOOR,
    }


def evaluate_verdict(
    score: float, coverage: float, has_fail: bool, nothing_decided: bool
) -> tuple[str, str]:
    """The one place the preliminary verdict is decided, so the gate cannot drift.

    Req 6: ≥ threshold ⇒ pass-oriented. Below it — or with a failure, or with nothing decidable,
    or with too little of the rule set actually verified — the result is flagged for official
    finalization. A confirmed FAIL always forces NON_COMPLIANT.
    """
    if has_fail:
        return VERDICT_NON_COMPLIANT, "a failed requirement"
    if nothing_decided:
        return VERDICT_NEEDS_REVIEW, "no requirement could be decided from the evidence"
    if score < AI_COMPLIANT_THRESHOLD:
        return VERDICT_NEEDS_REVIEW, f"compliance {score}% is below the {AI_COMPLIANT_THRESHOLD:g}% threshold"
    if coverage < COVERAGE_FLOOR:
        return (
            VERDICT_NEEDS_REVIEW,
            f"only {coverage}% of the applicable requirements could be verified "
            f"(floor {COVERAGE_FLOOR:g}%)",
        )
    return VERDICT_COMPLIANT, ""


def _detail(evaluation: RuleEvaluation) -> dict:
    try:
        value = json.loads(evaluation.detail or "{}")
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _source_reference(db: Session, rule_number: str) -> str:
    """Legal reference for a rule number, resolved from the rule library (never invented)."""
    for rule in db.query(Rule).all():
        if rule.rule_number == rule_number:
            return rule.source_reference or ""
    return ""


def build_checks(db: Session, inspection: Inspection) -> list[dict]:
    """One entry per requirement evaluated, with its legal reference and evidence summary."""
    evaluations = (
        db.query(RuleEvaluation)
        .filter(RuleEvaluation.inspection_id == inspection.id)
        .order_by(RuleEvaluation.rule_number)
        .all()
    )
    checks: list[dict] = []
    for e in evaluations:
        detail = _detail(e)
        status = (e.status or "").upper()
        checks.append(
            {
                "rule_number": e.rule_number,
                "title": e.title,
                "check_type": e.check_type,
                "status": e.status,
                "critical": bool(e.critical),
                # Per-check scoring basis, exposed so the UI never has to re-derive it.
                "weight": _weight(e),
                "applicability_kind": effective_kind(e) if status == "NOT_APPLICABLE" else "APPLIES",
                "counted_in_score": status in CREDIT
                and (e.check_type or "") not in EXCLUDED_CHECK_TYPES,
                "requirement": e.expected or "",
                "observed": e.observed or "",
                "explanation": e.reason or "",
                "confidence": round(float(e.confidence or 0.0), 3),
                "legal_reference": _source_reference(db, e.rule_number),
                # Measured checks (e.g. Rule 7 font size) expose the numbers behind the status.
                "required_value": detail.get("required_mm"),
                "required_reference": detail.get("required_reference"),
                "detected_value": detail.get("detected_mm"),
                "detected_field": detail.get("detected_field"),
                "satisfied": detail.get("satisfied"),
                "detail": detail,
            }
        )
    return checks


def build_ai_review(db: Session, inspection: Inspection) -> dict:
    """The complete automated preliminary review for one inspection."""
    evaluations = (
        db.query(RuleEvaluation).filter(RuleEvaluation.inspection_id == inspection.id).all()
    )
    scoring = score_evaluations(evaluations)
    checks = build_checks(db, inspection)
    violations = (
        db.query(Violation).filter(Violation.inspection_id == inspection.id).all()
    )
    violation_rows = [
        {
            "id": v.id,
            "rule_number": v.rule_number,
            "title": v.title,
            "severity": v.severity,
            "status": v.status,
            "description": v.description,
            "observed": v.observed,
            "expected": v.expected,
            "confidence": round(float(v.confidence or 0.0), 3),
            "legal_reference": _source_reference(db, v.rule_number),
        }
        for v in violations
    ]
    fails = [c for c in checks if c["status"] == "FAIL"]
    unverified = [c for c in checks if c["status"] == "UNCERTAIN" and c["check_type"] != "manual_only"]
    for entry in scoring["unverified"]:
        # A requirement skipped for want of evidence is unverified too, and must be named as such.
        if entry.get("kind") == "EVIDENCE_MISSING":
            unverified.append(
                {
                    "rule_number": entry["rule_number"],
                    "status": "NOT_APPLICABLE (no evidence)",
                    "check_type": "applicability",
                }
            )
    passed = [c for c in checks if c["status"] == "PASS"]
    score = scoring["score"]
    coverage = scoring["coverage"]
    nothing_decided = bool(scoring["nothing_decided"])
    verdict, shortfall = evaluate_verdict(score, coverage, bool(fails), nothing_decided)
    unverified_numbers = ", ".join(str(c["rule_number"]) for c in unverified)

    if verdict == VERDICT_NON_COMPLIANT:
        summary = (
            f"{len(fails)} requirement(s) failed with sufficient evidence: "
            + ", ".join(c["rule_number"] for c in fails)
            + f". Compliance {score}% over {len(passed)} passed requirement(s), evidence coverage "
            f"{coverage}%. The preliminary verdict is non-compliance; an authorized officer must "
            "finalize it."
        )
        action = (
            "Officer finalization required: review the failed requirement(s), the supporting "
            "evidence and any human-confirmed absence, then record the official decision."
        )
    elif verdict == VERDICT_COMPLIANT:
        summary = (
            f"Compliance {score}% (threshold {AI_COMPLIANT_THRESHOLD:g}%) with {len(passed)} "
            f"requirement(s) passed, no failed requirement, and {coverage}% of the applicable "
            "requirements verified from the evidence. This is an AI preliminary pass-oriented verdict."
        )
        action = (
            "AI preliminary pass. Official finalization is still recorded by an authorized officer."
            if not unverified
            else "AI preliminary pass, but "
            + unverified_numbers
            + " could not be verified from the supplied images and remain for confirmation."
        )
    else:
        summary = (
            f"Flagged for official finalization: {shortfall}. Compliance {score}% over the "
            f"requirements that could be decided, evidence coverage {coverage}% "
            f"({scoring['decided_weight']} of {scoring['applicable_weight']} weighted requirements "
            "decided)."
            + (f" Not verified: {unverified_numbers}." if unverified else "")
        )
        action = (
            "Manual finalization required by an authorized official: review the evidence for the "
            "unverified requirement(s) and record the official decision."
        )

    confidences = [c["confidence"] for c in checks if c["status"] != "NOT_APPLICABLE"]
    confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0

    legal_refs = sorted({c["legal_reference"] for c in checks if c["legal_reference"]})
    return {
        "score": score,
        "coverage": coverage,
        "verdict": verdict,
        "threshold": AI_COMPLIANT_THRESHOLD,
        "coverage_floor": COVERAGE_FLOOR,
        "confidence": confidence,
        "summary": summary,
        "recommended_action": action,
        "checks": checks,
        "violations": violation_rows,
        "legal_references": legal_refs,
        "scoring": {
            "method": METHOD_TEXT,
            "score": score,
            "coverage": coverage,
            "threshold": AI_COMPLIANT_THRESHOLD,
            "coverage_floor": COVERAGE_FLOOR,
            "decided_weight": scoring["decided_weight"],
            "applicable_weight": scoring["applicable_weight"],
            "numerator_credit": scoring["numerator_credit"],
            "nothing_decided": nothing_decided,
            "counted": scoring["counted"],
            "unverified": scoring["unverified"],
            "excluded": scoring["excluded"],
            "threshold": AI_COMPLIANT_THRESHOLD,
            "counted": scoring["counted"],
            "excluded": scoring["excluded"],
        },
        "counts": {
            "pass": len(passed),
            "fail": len(fails),
            "uncertain": len(
                [c for c in checks if c["status"] == "UNCERTAIN" and c["check_type"] != "manual_only"]
            ),
            "evidence_missing": len(
                [u for u in scoring["unverified"] if u.get("kind") == "EVIDENCE_MISSING"]
            ),
            "not_applicable": len([c for c in checks if c["status"] == "NOT_APPLICABLE"]),
            "manual_only": len([c for c in checks if c["check_type"] == "manual_only"]),
        },
    }


def identity_fields(db: Session, inspection: Inspection) -> dict:
    """Product identity snapshot used by the repository (name, brand, category, …)."""
    rows = (
        db.query(ExtractedField).filter(ExtractedField.inspection_id == inspection.id).all()
    )
    by_name = {r.field_name: r for r in rows}

    def value(name: str) -> str:
        row = by_name.get(name)
        if row is None or row.state == "HUMAN_CONFIRMED_ABSENT":
            return ""
        return (row.display_value or "").strip()

    return {
        "product_name": value("product_name") or value("common_name"),
        "brand": value("brand"),
        "manufacturer": value("manufacturer") or value("packer") or value("importer") or value("marketer"),
        "net_quantity": value("net_quantity"),
        "mrp": value("mrp"),
        "batch_lot": value("batch_lot"),
        "category": inspection.category or value("category") or "",
        "category_state": inspection.category_state or "UNCERTAIN",
    }
