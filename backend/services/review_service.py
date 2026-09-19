"""Review service: human-in-the-loop actions. Original AI output is never erased."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from backend import audit
from backend.models import ExtractedField, Inspection, ReviewAction, RuleEvaluation
from backend.models.enums import FieldState


def review_field(
    db: Session,
    inspection: Inspection,
    field_name: str,
    action: str,  # ACCEPT | EDIT_FIELD | ADD_FIELD | REJECT | CONFIRM_ABSENT
    corrected_value: str | None,
    reviewer: str,
    reason: str = "",
) -> dict:
    """Apply a review action to one field. Returns updated field info."""
    field = (
        db.query(ExtractedField)
        .filter(ExtractedField.inspection_id == inspection.id, ExtractedField.field_name == field_name)
        .first()
    )
    original_value = field.display_value if field else ""
    corrected = (corrected_value or "").strip()

    if action == "ACCEPT":
        if field is None:
            raise ValueError(f"Field '{field_name}' not found")
        db.add(ReviewAction(inspection_id=inspection.id, field_id=field.id, action="ACCEPT",
                            reviewer=reviewer, original_value=original_value, reason=reason))
        audit.log_action(reviewer, "field_accepted", inspection.inspection_number,
                         before=original_value, after=original_value, reason=reason)

    elif action == "EDIT_FIELD":
        if field is None or not corrected:
            raise ValueError("Field not found or empty corrected value")
        # keep original AI row untouched in field_candidates; update the field with full provenance
        field.normalized_value = json.dumps({"value": corrected, "display": corrected, "manual": True}, ensure_ascii=False)
        field.display_value = corrected
        field.state = FieldState.MANUALLY_CORRECTED.value
        field.manually_corrected = True
        field.confidence = 1.0
        field.source = "manual"
        field.uncertainty_reason = ""
        field.conflict_status = "NONE"
        db.add(ReviewAction(inspection_id=inspection.id, field_id=field.id, action="EDIT_FIELD",
                            reviewer=reviewer, original_value=original_value, corrected_value=corrected, reason=reason))
        audit.log_action(reviewer, "field_corrected", inspection.inspection_number,
                         before=original_value, after=corrected, reason=reason)

    elif action == "ADD_FIELD":
        if not corrected:
            raise ValueError("Empty value for added field")
        if field is None:
            field = ExtractedField(
                inspection_id=inspection.id,
                field_name=field_name,
                state=FieldState.MANUALLY_CORRECTED.value,
                display_value=corrected,
                normalized_value=json.dumps({"value": corrected, "display": corrected, "manual": True}, ensure_ascii=False),
                confidence=1.0,
                source="manual",
                manually_corrected=True,
            )
            db.add(field)
            db.flush()
        else:
            return review_field(db, inspection, field_name, "EDIT_FIELD", corrected, reviewer, reason)
        db.add(ReviewAction(inspection_id=inspection.id, field_id=field.id, action="ADD_FIELD",
                            reviewer=reviewer, corrected_value=corrected, reason=reason))
        audit.log_action(reviewer, "field_added", inspection.inspection_number, after=corrected, reason=reason)

    elif action == "CONFIRM_ABSENT":
        """Inspector confirms the declaration is genuinely absent from the package.

        This is the ONLY path by which missing-evidence turns into a legal FAIL: a human
        decision, recorded with reason. The field state becomes HUMAN_CONFIRMED_ABSENT and
        applicable rules are re-evaluated deterministically.
        """
        if field is None:
            # create the row so the confirmed state is explicit and queryable
            field = ExtractedField(
                inspection_id=inspection.id,
                field_name=field_name,
                state=FieldState.HUMAN_CONFIRMED_ABSENT.value,
                display_value="",
                confidence=1.0,
                source="manual",
                manually_corrected=True,
                uncertainty_reason="",
            )
            db.add(field)
            db.flush()
        else:
            field.state = FieldState.HUMAN_CONFIRMED_ABSENT.value
            field.manually_corrected = True
            field.confidence = 1.0
            field.source = "manual"
            field.uncertainty_reason = ""
        db.add(ReviewAction(inspection_id=inspection.id, field_id=field.id, action="CONFIRM_ABSENT",
                            reviewer=reviewer, original_value=original_value, reason=reason))
        audit.log_action(reviewer, "absence_confirmed", inspection.inspection_number,
                         before=original_value, after="HUMAN_CONFIRMED_ABSENT",
                         reason=reason or "inspector confirmed declaration absent from package")
        db.commit()
        _reevaluate_after_manual_change(db, inspection)
        db.refresh(field)
        return {"field_name": field.field_name, "state": field.state, "display_value": field.display_value}

    elif action == "REJECT":
        if field is None:
            raise ValueError(f"Field '{field_name}' not found")
        field.state = FieldState.MISSING.value
        field.confidence = 0.0
        db.add(ReviewAction(inspection_id=inspection.id, field_id=field.id, action="REJECT",
                            reviewer=reviewer, original_value=original_value, reason=reason))
        audit.log_action(reviewer, "field_rejected", inspection.inspection_number,
                         before=original_value, reason=reason)
    else:
        raise ValueError(f"Unknown review action '{action}'")

    db.commit()
    # A value the inspector CORRECTED, ADDED or REJECTED changes what the rules must be evaluated
    # against. Re-running the deterministic evaluation keeps the rule outcomes, the automated
    # compliance review and the repository score in step with the current evidence — a stale
    # PASS/FAIL from before the correction would otherwise be presented as the current result.
    if action in ("EDIT_FIELD", "ADD_FIELD", "REJECT"):
        _reevaluate_after_manual_change(db, inspection)
        db.refresh(field)
    return {"field_name": field.field_name, "state": field.state, "display_value": field.display_value}


def _reevaluate_after_manual_change(db: Session, inspection: Inspection) -> None:
    """Re-run deterministic rule evaluation + decision after a human state change.

    Manual fields are preserved; context is rebuilt from stored inspection state so the
    re-evaluation uses the same applicability logic as the pipeline.
    """
    from backend.rules.applicability import evaluate_applicability  # noqa: F401 (used via helper)
    from backend.rules.registry import get_active_versions
    from backend.rules.placement import build_panel_evidence
    from backend.services.font_service import build_font_measurement
    from backend.services.inspection_service import evaluate_and_persist_rules
    from backend.rules.decision import decide_inspection
    from backend.models import InspectionImage

    rows = (
        db.query(ExtractedField)
        .filter(ExtractedField.inspection_id == inspection.id)
        .all()
    )
    field_rows = {r.field_name: r for r in rows}
    # The physical measurement (scale calibration + panel area) is part of the package context, so
    # it is rebuilt here: a calibration recorded AFTER processing must take effect on re-evaluation.
    images = (
        db.query(InspectionImage)
        .filter(InspectionImage.inspection_id == inspection.id)
        .order_by(InspectionImage.id)
        .all()
    )
    font_params = next(
        (rv.params or {} for rv in get_active_versions(db).values() if rv.check_type == "font_size"),
        {},
    )
    context = {
        "category": inspection.category,
        "category_state": inspection.category_state,
        "overall_quality": inspection.overall_quality or "POOR",
        "image_count": len(images),
        "packaging": "retail_package",
        "is_import_evidence": "imported" in (inspection.summary or "").lower(),
        "font_measurement": build_font_measurement(
            inspection, field_rows, images, declaration_fields=font_params.get("declaration_fields")
        ),
        "panel_evidence": build_panel_evidence(images, field_rows),
    }
    evaluate_and_persist_rules(db, inspection, field_rows, context)
    conflicting = [
        r.field_name for r in rows if r.conflict_status == "CONFLICTING"
    ]
    decision, summary = decide_inspection_from_rows(db, inspection, conflicting)
    inspection.final_decision = decision
    inspection.summary = summary
    db.commit()
    # The compliance review, score and repository entry are derived from the persisted rule rows,
    # so they must be recomputed whenever a human review changes those rows.
    from backend.services.repository_service import refresh_scan

    refresh_scan(db, inspection)


def decide_inspection_from_rows(db: Session, inspection: Inspection, conflicting: list[str]):
    """Decide from persisted RuleEvaluation rows (used after manual review actions).

    Human-resolved violations are honoured: a rule whose violation the reviewer DISMISSED or
    RESOLVED no longer drives a NON_COMPLIANT decision (the human determination is recorded,
    audited and named in the summary). CONFIRMED violations continue to drive NON_COMPLIANT —
    confirmation is the reviewer agreeing with the finding.
    """
    from backend.models.enums import ViolationStatus
    from backend.models import Violation
    from backend.rules.decision import decide_inspection
    from backend.rules.engine import RuleOutcome

    evals = (
        db.query(RuleEvaluation)
        .filter(RuleEvaluation.inspection_id == inspection.id)
        .all()
    )
    human_resolved = {
        v.rule_number
        for v in db.query(Violation).filter(Violation.inspection_id == inspection.id).all()
        if v.status in (ViolationStatus.DISMISSED.value, ViolationStatus.RESOLVED.value)
    }
    outcomes = [
        RuleOutcome(
            rule_id="", rule_version_id=e.rule_version_id, rule_number=e.rule_number,
            title=e.title, requirement=e.expected, status=e.status, reason=e.reason,
            observed=e.observed, expected=e.expected, confidence=e.confidence,
            critical=e.critical,
            # The check implementation is stored on the row; deriving it from the rule NUMBER was
            # wrong for every rule added after the original set (e.g. the Rule 7 font-size check).
            check_type=(e.check_type or "declaration_present"),
        )
        for e in evals
        if e.status != "NOT_APPLICABLE" and e.rule_number not in human_resolved
    ]
    decision, summary = decide_inspection(outcomes, inspection.category_state, conflicting)
    if human_resolved:
        summary = (
            summary
            + " Human reviewer dismissed/resolved violation(s) for rule(s): "
            + ", ".join(sorted(human_resolved))
            + " — excluded from the automated decision (see review history and audit trail)."
        )
    return decision, summary


def review_violation(
    db: Session,
    inspection: Inspection,
    violation_id: int,
    action: str,  # CONFIRM | DISMISS | RESOLVE
    reviewer: str,
    reason: str = "",
) -> dict:
    """Apply a human decision to a violation. Fully audited; re-decides the inspection.

    CONFIRM  — reviewer agrees the non-compliance is real; decision stays NON_COMPLIANT.
    DISMISS  — reviewer determines it is not a violation; REQUIRES a recorded reason and no
               longer drives the automated decision (never a silent override).
    RESOLVE  — the issue was rectified / addressed; also excluded from the decision.
    """
    from backend.models.enums import ViolationStatus
    from backend.models import Violation

    action = (action or "").upper().strip()
    mapping = {
        "CONFIRM": ViolationStatus.CONFIRMED.value,
        "DISMISS": ViolationStatus.DISMISSED.value,
        "RESOLVE": ViolationStatus.RESOLVED.value,
    }
    if action not in mapping:
        raise ValueError(f"Unknown violation action '{action}'")
    if action in ("DISMISS", "RESOLVE") and not (reason or "").strip():
        raise ValueError("A reason is required to dismiss or resolve a violation")

    violation = (
        db.query(Violation)
        .filter(Violation.id == violation_id, Violation.inspection_id == inspection.id)
        .first()
    )
    if violation is None:
        raise ValueError("Violation not found for this inspection")

    previous = violation.status
    violation.status = mapping[action]
    db.add(
        ReviewAction(
            inspection_id=inspection.id,
            action=f"VIOLATION_{action}",
            reviewer=reviewer,
            original_value=f"{violation.rule_number} {previous}",
            corrected_value=mapping[action],
            reason=reason or "",
        )
    )
    db.commit()
    audit.log_action(
        reviewer,
        f"violation_{action.lower()}",
        inspection.inspection_number,
        before=previous,
        after=violation.status,
        reason=f"rule {violation.rule_number}: {reason}"[:300],
    )

    rule_number = violation.rule_number
    decision_status = violation.status

    # Re-run the deterministic evaluation so the final decision reflects the human call.
    # Re-evaluation deletes and recreates violation rows, so the in-session instance above is
    # now detached — re-query the current row for this rule to report the live state.
    _reevaluate_after_manual_change(db, inspection)
    current = (
        db.query(Violation)
        .filter(Violation.inspection_id == inspection.id, Violation.rule_number == rule_number)
        .first()
    )
    return {
        "violation_id": current.id if current is not None else violation_id,
        "rule_number": rule_number,
        "status": current.status if current is not None else decision_status,
        "decision": inspection.final_decision,
    }


def add_review_note(db: Session, inspection: Inspection, reviewer: str, note: str) -> None:
    db.add(ReviewAction(inspection_id=inspection.id, action="NOTE", reviewer=reviewer, reason=note))
    db.commit()
    audit.log_action(reviewer, "inspection_reviewed", inspection.inspection_number, after=note[:200])


def complete_review(db: Session, inspection: Inspection, reviewer: str, final_decision: str, reason: str = "") -> None:
    """Finalize the inspection with a human decision (the human's call, recorded as such)."""
    from backend.models.enums import FinalDecision

    decision = FinalDecision(final_decision)
    previous = inspection.final_decision
    inspection.final_decision = decision.value
    inspection.status = "COMPLETED"
    from backend.models.user import utcnow

    inspection.completed_at = utcnow()
    if reason:
        inspection.summary = reason
    # The official decision is also recorded on the repository scan, so the AI preliminary verdict
    # and the human final decision are never confused in the repository, the UI or a report.
    inspection.official_decision = decision.value
    inspection.finalized_by = reviewer
    inspection.finalized_at = inspection.completed_at
    if reason:
        inspection.finalization_remarks = reason
    db.commit()
    from backend.models import ProductScan
    from backend.models.product_scan import STATUS_FINALIZED

    scan = db.query(ProductScan).filter(ProductScan.inspection_id == inspection.id).first()
    if scan is not None:
        scan.official_decision = decision.value
        scan.review_status = STATUS_FINALIZED
        scan.finalized_by = reviewer
        scan.finalized_at = inspection.completed_at
        if reason:
            scan.remarks = reason
        db.commit()
    audit.log_action(reviewer, "inspection_reviewed", inspection.inspection_number,
                     before=previous, after=decision.value, reason="final decision by human reviewer")
