"""Engine identity: which pipeline produced a result, and against which rule set.

The stamp is persisted on every processed inspection (``Inspection.pipeline_version``) and printed
into every reprocessing revision, so it is always answerable which engine and which rule versions
produced the ACTIVE result of a record, and whether a record still carries an older analysis.

This is deliberately *not* a software release number: it changes when the analysis behaviour
changes (extraction, classification, applicability, scoring, checks), which is exactly the event
that makes existing results worth regenerating.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

# Bump when analysis behaviour changes. Records carrying a different value are listed for
# reprocessing (they are never silently rewritten — a migration run is always explicit and audited).
ENGINE_VERSION = "2026.09-r5"
ENGINE_LABEL = (
    "evidence-precision pipeline: OCR word-boundary display repair, relationship-instruction "
    "guards, entity/address separation, PIN-vs-quantity guard, fused country-of-origin reading, "
    "placement analysis, split compliance/coverage scoring, rule 6(10) listing checks"
)


def rule_set_fingerprint(db: Session) -> str:
    """Short, stable digest of the rule versions currently active.

    Two inspections evaluated under the same rule set share the digest, so a later rule edit is
    visible as a different fingerprint instead of being inferred from timestamps.
    """
    from backend.rules.registry import get_active_versions

    versions = get_active_versions(db)
    payload = json.dumps(
        sorted(
            f"{rule_id}:{rv.version}:{rv.check_type}:{rv.weight}:{rv.critical}:{rv.effective_from}"
            for rule_id, rv in versions.items()
        ),
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def engine_stamp(db: Session) -> dict:
    """The full provenance of a pipeline run: engine version, rule fingerprint and rule count."""
    from backend.rules.registry import get_active_versions

    versions = get_active_versions(db)
    return {
        "engine_version": ENGINE_VERSION,
        "engine_label": ENGINE_LABEL,
        "rule_fingerprint": rule_set_fingerprint(db),
        "rule_count": len(versions),
    }
