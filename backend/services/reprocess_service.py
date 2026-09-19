"""Reprocessing and migration: bring existing inspections onto the corrected pipeline.

Why this exists
---------------
An inspection analysed by an older engine keeps that analysis forever unless it is deliberately
regenerated. This service regenerates it **in place**: the same inspection record is updated (never
duplicated), a full before/after snapshot is written to :class:`InspectionRevision`, and the event
is recorded in the append-only audit log with the actor, the reason and the engine/rule stamp.

What is preserved
-----------------
* **Human decisions.** Manually corrected fields win over re-extraction (the pipeline keeps them),
  a recorded official finalization is not discarded, and a reviewer's CONFIRM/DISMISS/RESOLVE on a
  violation survives re-evaluation of the same rule.
* **The previous result.** It is recoverable from the revision row; the active screens show the
  corrected result.
* **Identity.** ``ProductScan.inspection_id`` is unique and ``refresh_scan`` is idempotent, so a
  product is deduplicated on its identity key and one inspection never becomes two records.

Bulk runs are executed by a single background worker (one migration at a time) so a large migration
does not block the request thread and the caller can poll progress and per-record outcomes.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend import audit
from backend.database import SessionLocal
from backend.models import (
    Classification,
    Evidence,
    ExtractedField,
    Inspection,
    InspectionImage,
    InspectionRevision,
    ProductScan,
    RuleEvaluation,
    Violation,
)
from backend.models.inspection_revision import REVISION_KIND_MIGRATION, REVISION_KIND_REPROCESS
from backend.services.inspection_service import process_inspection
from backend.version import ENGINE_VERSION, engine_stamp

#: Statuses whose active result was produced by a pipeline run and can therefore be regenerated.
#: CREATED (nothing ran yet) and PROCESSING (a run is in flight) are deliberately excluded.
CANDIDATE_STATUSES = ("AWAITING_REVIEW", "COMPLETED", "FAILED")

MAX_BATCH = 500


class MigrationInProgress(RuntimeError):
    """Raised when a bulk migration is already running (one worker at a time)."""

    def __init__(self, job_id: str) -> None:
        super().__init__("A migration is already running")
        self.job_id = job_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _label(field_name: str) -> str:
    return field_name.replace("_", " ")


# --------------------------------------------------------------------------- snapshots


def snapshot(db: Session, inspection: Inspection) -> dict:
    """Read the persisted result of an inspection as a comparable, storable snapshot."""
    fields = db.query(ExtractedField).filter(ExtractedField.inspection_id == inspection.id).all()
    rules = db.query(RuleEvaluation).filter(RuleEvaluation.inspection_id == inspection.id).all()
    violations = db.query(Violation).filter(Violation.inspection_id == inspection.id).all()
    images = db.query(InspectionImage).filter(InspectionImage.inspection_id == inspection.id).count()
    evidence = db.query(Evidence).filter(Evidence.inspection_id == inspection.id).count()
    scan = db.query(ProductScan).filter(ProductScan.inspection_id == inspection.id).first()
    cls = (
        db.query(Classification)
        .filter(Classification.inspection_id == inspection.id)
        .order_by(Classification.id.desc())
        .first()
    )
    return {
        "captured_at": _now(),
        "inspection": {
            "status": inspection.status,
            "final_decision": inspection.final_decision or "",
            "official_decision": inspection.official_decision or "",
            "category": inspection.category or "",
            "category_state": inspection.category_state or "",
            "category_confidence": round(float(inspection.category_confidence or 0.0), 3),
            "summary": inspection.summary or "",
            "processed_at": str(inspection.processed_at or ""),
            "pipeline_version": inspection.pipeline_version or "",
            "quality": inspection.overall_quality or "",
            "quality_score": round(float(inspection.overall_quality_score or 0.0), 1),
        },
        "classification": {
            "category": (cls.category if cls else inspection.category) or "",
            "state": (cls.state if cls else inspection.category_state) or "",
            "confidence": round(float(cls.confidence if cls else inspection.category_confidence or 0.0), 3),
            "signals": (cls.signals[:500] if cls and cls.signals else ""),
        },
        "fields": {
            f.field_name: {
                "value": f.display_value or "",
                "state": f.state or "",
                "confidence": round(float(f.confidence or 0.0), 3),
                "raw_value": f.raw_value or "",
                "uncertainty_reason": f.uncertainty_reason or "",
                "manually_corrected": bool(f.manually_corrected),
                "source_image_id": f.source_image_id,
            }
            for f in fields
        },
        "rules": [
            {
                "rule_number": r.rule_number or "",
                "title": r.title or "",
                "status": r.status or "",
                "reason": r.reason or "",
                "observed": r.observed or "",
                "expected": r.expected or "",
                "confidence": round(float(r.confidence or 0.0), 3),
                "critical": bool(r.critical),
                "weight": float(r.weight or 0.0),
                "applicability_kind": r.applicability_kind or "",
            }
            for r in rules
        ],
        "violations": [
            {
                "rule_number": v.rule_number or "",
                "title": v.title or "",
                "status": v.status or "",
                "severity": v.severity or "",
            }
            for v in violations
        ],
        "scan": (
            {
                "id": scan.id,
                "product_id": scan.product_id,
                "compliance_score": round(float(scan.compliance_score or 0.0), 2),
                "coverage_score": round(float(scan.coverage_score or 0.0), 2),
                "coverage_floor": round(float(scan.coverage_floor or 0.0), 2),
                "ai_verdict": scan.ai_verdict or "",
                "ai_confidence": round(float(scan.ai_confidence or 0.0), 3),
                "review_status": scan.review_status or "",
                "official_decision": scan.official_decision or "",
                "threshold": round(float(scan.threshold or 0.0), 2),
                "product_name": scan.product_name or "",
                "brand": scan.brand or "",
                "category": scan.category or "",
                "manufacturer": scan.manufacturer or "",
                "net_quantity": scan.net_quantity or "",
                "mrp": scan.mrp or "",
            }
            if scan is not None
            else None
        ),
        "evidence": {"images": images, "crops": evidence},
    }


# --------------------------------------------------------------------------- diff


def _field_change_rows(before: dict, after: dict) -> list[dict]:
    bf = before.get("fields", {})
    af = after.get("fields", {})
    rows: list[dict] = []
    for name in sorted(set(bf) | set(af)):
        b = bf.get(name, {})
        a = af.get(name, {})
        bv = (b.get("value") or "").strip()
        av = (a.get("value") or "").strip()
        bs = b.get("state") or ""
        as_ = a.get("state") or ""
        if bv == av and bs == as_:
            continue
        if not bv and av:
            kind = "newly_detected"
        elif bv and not av:
            kind = "no_longer_detected"
        elif bv != av:
            kind = "value_changed"
        else:
            kind = "state_changed"
        rows.append(
            {
                "field_name": name,
                "label": _label(name),
                "kind": kind,
                "before_value": bv,
                "after_value": av,
                "before_state": bs,
                "after_state": as_,
                "manually_corrected": bool(a.get("manually_corrected")),
                "after_uncertainty_reason": a.get("uncertainty_reason") or "",
            }
        )
    return rows


def _rule_change_rows(before: dict, after: dict) -> list[dict]:
    bmap = {r["rule_number"]: r for r in before.get("rules", []) if r.get("rule_number")}
    amap = {r["rule_number"]: r for r in after.get("rules", []) if r.get("rule_number")}
    rows: list[dict] = []
    for number in sorted(set(bmap) | set(amap)):
        b = bmap.get(number, {})
        a = amap.get(number, {})
        if (b.get("status") or "") == (a.get("status") or "") and (b.get("observed") or "") == (
            a.get("observed") or ""
        ):
            continue
        rows.append(
            {
                "rule_number": number,
                "title": a.get("title") or b.get("title") or "",
                "before_status": b.get("status") or "NOT_EVALUATED",
                "after_status": a.get("status") or "NOT_EVALUATED",
                "before_observed": b.get("observed") or "",
                "after_observed": a.get("observed") or "",
                "critical": bool(a.get("critical") or b.get("critical")),
            }
        )
    return rows


def diff(before: dict, after: dict) -> dict:
    """Structured BEFORE vs AFTER comparison of two snapshots of the same inspection."""
    fields = _field_change_rows(before, after)
    rules = _rule_change_rows(before, after)
    bscan = before.get("scan") or {}
    ascan = after.get("scan") or {}
    bcls = before.get("classification") or {}
    acls = after.get("classification") or {}
    bviol = before.get("violations", [])
    aviol = after.get("violations", [])

    def _open(violations: list[dict]) -> set[str]:
        return {v["rule_number"] for v in violations if v.get("status") in ("OPEN", "NEEDS_REVIEW")}

    open_before = _open(bviol)
    open_after = _open(aviol)

    def _unresolved(snap: dict) -> int:
        return sum(
            1
            for f in (snap.get("fields") or {}).values()
            if f.get("state") in ("MISSING", "UNCERTAIN", "CONFLICTING")
        )

    score_before = float(bscan.get("compliance_score") or 0.0) if bscan else None
    score_after = float(ascan.get("compliance_score") or 0.0) if ascan else None
    coverage_before = float(bscan.get("coverage_score") or 0.0) if bscan else None
    coverage_after = float(ascan.get("coverage_score") or 0.0) if ascan else None

    changed = {
        "fields_changed": len(fields),
        "rules_changed": len(rules),
        "score_changed": score_before != score_after,
        "coverage_changed": coverage_before != coverage_after,
        "verdict_changed": (bscan.get("ai_verdict") or "") != (ascan.get("ai_verdict") or ""),
        "review_status_changed": (bscan.get("review_status") or "")
        != (ascan.get("review_status") or ""),
        "classification_changed": (bcls.get("category") or "") != (acls.get("category") or ""),
    }
    changed["any"] = any(
        changed[k]
        for k in (
            "fields_changed",
            "rules_changed",
            "score_changed",
            "coverage_changed",
            "verdict_changed",
            "review_status_changed",
            "classification_changed",
        )
    )

    # A short, human-readable headline for lists and chat answers — built from the diff, never
    # from a template that could claim a change that did not happen.
    headline: list[str] = []
    newly = [f"{f['label']}: not detected → {f['after_value'] or f['after_state']}" for f in fields if f["kind"] == "newly_detected"]
    lost = [f"{f['label']}: {f['before_value'] or f['before_state']} → not detected" for f in fields if f["kind"] == "no_longer_detected"]
    other_fields = [
        f"{f['label']}: {f['before_value'] or f['before_state']} → {f['after_value'] or f['after_state']}"
        for f in fields
        if f["kind"] in ("value_changed", "state_changed")
    ]
    if changed["classification_changed"]:
        headline.append(f"category: {bcls.get('category') or '—'} → {acls.get('category') or '—'}")
    headline.extend(newly[:6])
    headline.extend(lost[:4])
    headline.extend(other_fields[:6])
    if changed["score_changed"] and score_before is not None and score_after is not None:
        headline.append(f"compliance: {score_before}% → {score_after}%")
    if changed["coverage_changed"] and coverage_before is not None and coverage_after is not None:
        headline.append(f"evidence coverage: {coverage_before}% → {coverage_after}%")
    if changed["verdict_changed"]:
        headline.append(
            f"AI verdict: {bscan.get('ai_verdict') or '—'} → {ascan.get('ai_verdict') or '—'}"
        )

    return {
        "changed": changed,
        "headline": headline,
        "fields": fields,
        "rules": rules,
        "classification": {
            "before": bcls.get("category") or "",
            "before_state": bcls.get("state") or "",
            "after": acls.get("category") or "",
            "after_state": acls.get("state") or "",
        },
        "score": {"before": score_before, "after": score_after},
        "coverage": {"before": coverage_before, "after": coverage_after},
        "verdict": {
            "before": bscan.get("ai_verdict") or "",
            "after": ascan.get("ai_verdict") or "",
        },
        "review_status": {
            "before": bscan.get("review_status") or "",
            "after": ascan.get("review_status") or "",
        },
        "official_decision": {
            "before": bscan.get("official_decision") or before["inspection"].get("official_decision") or "",
            "after": ascan.get("official_decision") or after["inspection"].get("official_decision") or "",
        },
        "violations": {
            "before_total": len(bviol),
            "after_total": len(aviol),
            "newly_flagged": sorted(open_after - open_before),
            "no_longer_flagged": sorted(open_before - open_after),
        },
        "unresolved": {"before": _unresolved(before), "after": _unresolved(after)},
    }


# --------------------------------------------------------------------------- single reprocess


def reprocess_inspection(
    db: Session,
    inspection: Inspection,
    *,
    actor: str,
    reason: str,
    kind: str = REVISION_KIND_REPROCESS,
    refresh_ocr: bool = False,
) -> dict:
    """Re-run the corrected pipeline over one existing inspection, in place.

    The inspection row is UPDATED (never duplicated). The result it replaced is stored on an
    :class:`InspectionRevision` row before the run, and the run itself is written to the audit log.
    """
    before = snapshot(db, inspection)
    stamp = engine_stamp(db)
    revision_no = (
        int(
            db.query(func.max(InspectionRevision.revision_no))
            .filter(InspectionRevision.inspection_id == inspection.id)
            .scalar()
            or 0
        )
        + 1
    )

    audit.log_action(
        actor,
        "inspection_reprocess_started",
        inspection.inspection_number,
        before=f"{before['inspection']['final_decision'] or '—'} / "
        f"{(before.get('scan') or {}).get('compliance_score', '—')}%",
        reason=reason or "reprocess",
    )

    error = ""
    try:
        process_inspection(db, inspection.id, refresh_ocr=refresh_ocr)
    except Exception as exc:  # process_inspection degrades internally; this is a last-resort guard
        db.rollback()
        error = f"{type(exc).__name__}: {exc}"[:300]

    db.refresh(inspection)
    after = snapshot(db, inspection)
    changes = diff(before, after)
    ascan = after.get("scan") or {}
    bscan = before.get("scan") or {}

    revision = InspectionRevision(
        inspection_id=inspection.id,
        revision_no=revision_no,
        kind=kind,
        actor=actor,
        reason=reason or "",
        engine_version=ENGINE_VERSION,
        rule_fingerprint=stamp["rule_fingerprint"],
        before_json=json.dumps(before, ensure_ascii=False),
        after_json=json.dumps(after, ensure_ascii=False),
        changes_json=json.dumps(changes, ensure_ascii=False),
        fields_changed=changes["changed"]["fields_changed"],
        rules_changed=changes["changed"]["rules_changed"],
        score_before=bscan.get("compliance_score"),
        score_after=ascan.get("compliance_score"),
        coverage_before=bscan.get("coverage_score"),
        coverage_after=ascan.get("coverage_score"),
        verdict_before=bscan.get("ai_verdict") or "",
        verdict_after=ascan.get("ai_verdict") or "",
        review_status_before=bscan.get("review_status") or "",
        review_status_after=ascan.get("review_status") or "",
    )
    db.add(revision)
    inspection.pipeline_version = ENGINE_VERSION
    inspection.reprocess_count = int(inspection.reprocess_count or 0) + 1
    inspection.last_reprocessed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(revision)

    audit.log_action(
        actor,
        "inspection_reprocessed",
        inspection.inspection_number,
        before=f"{changes['changed']['fields_changed']} field(s), {changes['changed']['rules_changed']} rule result(s) changed"
        f"{f' (engine error: {error})' if error else ''}",
        after="; ".join(changes["headline"][:6]) or "no material change",
        reason=reason or f"reprocessed under {ENGINE_VERSION}",
    )

    return {
        "ok": not error,
        "error": error,
        "inspection_id": inspection.id,
        "inspection_number": inspection.inspection_number,
        "revision_id": revision.id,
        "revision_no": revision_no,
        "engine_version": ENGINE_VERSION,
        "rule_fingerprint": stamp["rule_fingerprint"],
        "before": before,
        "after": after,
        "changes": changes,
    }


# --------------------------------------------------------------------------- legacy inventory


def _candidate_query(db: Session):
    return (
        db.query(Inspection)
        .filter(Inspection.status.in_(CANDIDATE_STATUSES))
        .filter(Inspection.id.in_(db.query(InspectionImage.inspection_id)))
    )


def legacy_preview(db: Session, *, limit: int = MAX_BATCH) -> dict:
    """What a migration would touch: counts + the oldest records still on an older engine."""
    stamp = engine_stamp(db)
    total_repository = db.query(Inspection).count()
    candidates = _candidate_query(db)
    candidate_total = candidates.count()
    legacy_query = candidates.filter(
        or_(Inspection.pipeline_version.is_(None), Inspection.pipeline_version != ENGINE_VERSION)
    )
    legacy_total = legacy_query.count()

    legacy_rows = legacy_query.order_by(Inspection.id).limit(limit).all()
    scans = {
        s.inspection_id: s
        for s in db.query(ProductScan)
        .filter(ProductScan.inspection_id.in_([i.id for i in legacy_rows] or [0]))
        .all()
    }
    items = []
    for insp in legacy_rows:
        scan = scans.get(insp.id)
        items.append(
            {
                "inspection_id": insp.id,
                "inspection_number": insp.inspection_number,
                "product": (scan.product_name if scan else "") or "",
                "brand": (scan.brand if scan else "") or "",
                "status": insp.status,
                "pipeline_version": insp.pipeline_version or "",
                "processed_at": str(insp.processed_at or ""),
                "score": round(float(scan.compliance_score), 2) if scan else None,
                "coverage": round(float(scan.coverage_score), 2) if scan else None,
                "verdict": (scan.ai_verdict if scan else "") or "",
                "review_status": (scan.review_status if scan else "") or "",
                "official_decision": (scan.official_decision if scan else "") or "",
                "reprocess_count": int(insp.reprocess_count or 0),
            }
        )

    return {
        **stamp,
        "total_inspections": total_repository,
        "reprocessable_total": candidate_total,
        "legacy_count": legacy_total,
        "current_count": candidate_total - legacy_total,
        "items": items,
        "max_batch": MAX_BATCH,
    }


def revisions_for(db: Session, inspection_id: int, *, limit: int = 20) -> list[dict]:
    rows = (
        db.query(InspectionRevision)
        .filter(InspectionRevision.inspection_id == inspection_id)
        .order_by(InspectionRevision.revision_no.desc())
        .limit(limit)
        .all()
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "revision_no": r.revision_no,
                "kind": r.kind,
                "actor": r.actor,
                "reason": r.reason,
                "engine_version": r.engine_version,
                "rule_fingerprint": r.rule_fingerprint,
                "created_at": str(r.created_at or ""),
                "fields_changed": r.fields_changed,
                "rules_changed": r.rules_changed,
                "score_before": r.score_before,
                "score_after": r.score_after,
                "coverage_before": r.coverage_before,
                "coverage_after": r.coverage_after,
                "verdict_before": r.verdict_before,
                "verdict_after": r.verdict_after,
                "review_status_before": r.review_status_before,
                "review_status_after": r.review_status_after,
                "before": _load(r.before_json),
                "after": _load(r.after_json),
                "changes": _load(r.changes_json),
            }
        )
    return out


def _load(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


# --------------------------------------------------------------------------- bulk migration


_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_MAX_JOB_RESULTS = MAX_BATCH


def _compact_result(result: dict) -> dict:
    changes = result["changes"]
    return {
        "inspection_id": result["inspection_id"],
        "inspection_number": result["inspection_number"],
        "ok": result["ok"],
        "error": result["error"],
        "fields_changed": changes["changed"]["fields_changed"],
        "rules_changed": changes["changed"]["rules_changed"],
        "score_before": changes["score"]["before"],
        "score_after": changes["score"]["after"],
        "verdict_before": changes["verdict"]["before"],
        "verdict_after": changes["verdict"]["after"],
        "headline": changes["headline"][:4],
    }


def _run_migration(job_id: str, actor: str, reason: str, ids: list[int] | None, limit: int, refresh_ocr: bool) -> None:
    db = SessionLocal()
    job = _JOBS[job_id]
    try:
        job["state"] = "RUNNING"
        query = _candidate_query(db)
        if ids:
            query = query.filter(Inspection.id.in_(ids))
        else:
            # A plain "migrate everything" run only touches records still on an older engine, so
            # re-running it is cheap and never churns records that are already current.
            query = query.filter(
                or_(Inspection.pipeline_version.is_(None), Inspection.pipeline_version != ENGINE_VERSION)
            )
        targets = [i.id for i in query.order_by(Inspection.id).limit(limit).all()]
        job["total"] = len(targets)
        for inspection_id in targets:
            work = SessionLocal()
            try:
                inspection = work.query(Inspection).filter(Inspection.id == inspection_id).first()
                if inspection is None:
                    job["done"] += 1
                    continue
                job["current"] = inspection.inspection_number
                result = reprocess_inspection(
                    work,
                    inspection,
                    actor=actor,
                    reason=reason,
                    kind=REVISION_KIND_MIGRATION,
                    refresh_ocr=refresh_ocr,
                )
                job["succeeded"] += 1
                if len(job["results"]) < _MAX_JOB_RESULTS:
                    job["results"].append(_compact_result(result))
            except Exception as exc:  # one bad record must never abort the migration
                work.rollback()
                job["failed"] += 1
                if len(job["results"]) < _MAX_JOB_RESULTS:
                    job["results"].append(
                        {
                            "inspection_id": inspection_id,
                            "inspection_number": "",
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}"[:300],
                            "fields_changed": 0,
                            "rules_changed": 0,
                            "score_before": None,
                            "score_after": None,
                            "verdict_before": "",
                            "verdict_after": "",
                            "headline": [],
                        }
                    )
                audit.log_action(
                    actor,
                    "inspection_reprocess_failed",
                    str(inspection_id),
                    reason=f"{type(exc).__name__}: {exc}"[:200],
                )
            finally:
                work.close()
                job["done"] += 1
        job["current"] = ""
        job["state"] = "DONE"
    except Exception as exc:
        job["state"] = "FAILED"
        job["error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        db.close()
        job["finished_at"] = _now()


def start_migration(
    *,
    actor: str,
    reason: str,
    ids: list[int] | None = None,
    limit: int | None = None,
    refresh_ocr: bool = False,
) -> dict:
    """Start a bulk reprocessing run. Returns the job immediately; poll it for progress."""
    limited = max(1, min(int(limit or MAX_BATCH), MAX_BATCH))
    with _JOBS_LOCK:
        active = next((j for j in _JOBS.values() if j["state"] in ("QUEUED", "RUNNING")), None)
        if active is not None:
            raise MigrationInProgress(active["id"])
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "id": job_id,
            "state": "QUEUED",
            "actor": actor,
            "reason": reason or "",
            "refresh_ocr": refresh_ocr,
            "started_at": _now(),
            "finished_at": "",
            "total": 0,
            "done": 0,
            "succeeded": 0,
            "failed": 0,
            "current": "",
            "results": [],
            "error": "",
            "engine_version": ENGINE_VERSION,
            "limit": limited,
        }
    threading.Thread(
        target=_run_migration,
        args=(job_id, actor, reason, list(ids) if ids else None, limited, refresh_ocr),
        name=f"migration-{job_id}",
        daemon=True,
    ).start()
    return migration_job(job_id) or {}


def migration_job(job_id: str) -> dict | None:
    job = _JOBS.get(job_id)
    return dict(job) if job else None


def recent_jobs(*, limit: int = 10) -> list[dict]:
    jobs = sorted(_JOBS.values(), key=lambda j: j["started_at"], reverse=True)[:limit]
    return [dict(j) for j in jobs]
