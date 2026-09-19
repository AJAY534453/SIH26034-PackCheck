"""Rule registry: loads versioned JSON rule definitions into the DB.

Idempotent: re-seeding an unchanged rule does nothing; a changed definition creates a NEW
version — historical evaluations keep the version they were evaluated against.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from backend.models import Rule, RuleVersion

DEFINITIONS_DIR = Path(__file__).resolve().parent / "definitions"


def _load_json_files() -> list[dict]:
    docs: list[dict] = []
    for path in sorted(DEFINITIONS_DIR.glob("*.json")):
        try:
            docs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return docs


def seed_rules(db: Session) -> dict:
    """Seed rules from definitions. Returns stats {created, versioned, unchanged}."""
    stats = {"created": 0, "versioned": 0, "unchanged": 0}
    for doc in _load_json_files():
        for r in doc.get("rules", []) + doc.get("manual_only_rules", []):
            existing = db.query(Rule).filter(Rule.rule_id == r["rule_id"]).first()
            if existing is None:
                rule = Rule(
                    rule_id=r["rule_id"],
                    rule_number=r.get("rule_number", ""),
                    title=r.get("title", ""),
                    description=r.get("description", ""),
                    source_reference=r.get("source_reference", ""),
                    current_version=1,
                    applicability="MANUAL_ONLY" if r.get("check_type") == "manual_only" else "MANDATORY",
                    status="ACTIVE",
                    weight=_as_weight(r.get("weight")),
                    critical=_as_bool(r.get("critical")),
                )
                db.add(rule)
                db.flush()
                db.add(
                    RuleVersion(
                        rule_id_fk=rule.id,
                        version=1,
                        title=r.get("title", ""),
                        description=r.get("description", ""),
                        requirement=r.get("requirement", ""),
                        source_reference=r.get("source_reference", ""),
                        amendment=r.get("amendment", ""),
                        effective_from=_parse_date(r.get("effective_from")),
                        check_type=r.get("check_type", ""),
                        params=r.get("params", {}),
                        applicability=r.get("applicability", {}),
                        weight=_as_weight(r.get("weight")),
                        critical=_as_bool(r.get("critical")),
                    )
                )
                stats["created"] += 1
                continue
            # changed definition -> new version
            current = (
                db.query(RuleVersion)
                .filter(RuleVersion.rule_id_fk == existing.id, RuleVersion.version == existing.current_version)
                .first()
            )
            fingerprint = json.dumps(
                {
                    "requirement": r.get("requirement", ""),
                    "check_type": r.get("check_type", ""),
                    "params": r.get("params", {}),
                    "applicability": r.get("applicability", {}),
                    # Scoring data is part of the version identity: changing a weight must create a
                    # new version, never silently re-score history.
                    "weight": _as_weight(r.get("weight")),
                    "critical": _as_bool(r.get("critical")),
                },
                sort_keys=True,
            )
            current_fingerprint = json.dumps(
                {
                    "requirement": current.requirement if current else "",
                    "check_type": current.check_type if current else "",
                    "params": current.params if current else {},
                    "applicability": current.applicability if current else {},
                    "weight": _as_weight(getattr(current, "weight", None)) if current else None,
                    "critical": _as_bool(getattr(current, "critical", None)) if current else None,
                },
                sort_keys=True,
            )
            if fingerprint != current_fingerprint:
                new_version = existing.current_version + 1
                db.add(
                    RuleVersion(
                        rule_id_fk=existing.id,
                        version=new_version,
                        title=r.get("title", existing.title),
                        description=r.get("description", existing.description),
                        requirement=r.get("requirement", ""),
                        source_reference=r.get("source_reference", existing.source_reference),
                        amendment=r.get("amendment", "updated definition"),
                        effective_from=_parse_date(r.get("effective_from")),
                        check_type=r.get("check_type", ""),
                        params=r.get("params", {}),
                        applicability=r.get("applicability", {}),
                        weight=_as_weight(r.get("weight")),
                        critical=_as_bool(r.get("critical")),
                    )
                )
                existing.current_version = new_version
                existing.title = r.get("title", existing.title)
                existing.description = r.get("description", existing.description)
                existing.weight = _as_weight(r.get("weight"))
                existing.critical = _as_bool(r.get("critical"))
                stats["versioned"] += 1
            else:
                stats["unchanged"] += 1
    db.commit()
    return stats


def _as_weight(value) -> float | None:
    """Declared weight, or None when the rule does not declare one (engine default then applies)."""
    if value is None:
        return None
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return None
    return weight if weight > 0 else None


def _as_bool(value) -> bool | None:
    """Declared criticality, or None when the rule does not declare one."""
    if value is None:
        return None
    return bool(value)


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def get_active_versions(db: Session) -> dict[str, RuleVersion]:
    """Map rule_id -> active RuleVersion."""
    out: dict[str, RuleVersion] = {}
    for rule in db.query(Rule).filter(Rule.status == "ACTIVE").all():
        rv = (
            db.query(RuleVersion)
            .filter(RuleVersion.rule_id_fk == rule.id, RuleVersion.version == rule.current_version)
            .first()
        )
        if rv:
            out[rule.rule_id] = rv
    return out
