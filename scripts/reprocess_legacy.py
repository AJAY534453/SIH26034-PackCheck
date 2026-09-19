"""Regenerate existing inspections onto the current analysis engine (CLI).

Dry run by default: prints exactly what would change, per record. With ``--apply`` each record is
reprocessed IN PLACE — the previous result is kept on an ``inspection_revisions`` row, human
corrections and recorded official decisions are preserved, and the event is written to the audit
log. A product is deduplicated on its identity key, so one inspection never becomes two scans.

Examples
--------
    .venv/Scripts/python scripts/reprocess_legacy.py                     # dry run, show the diff
    .venv/Scripts/python scripts/reprocess_legacy.py --apply             # migrate every legacy record
    .venv/Scripts/python scripts/reprocess_legacy.py --apply --limit 10  # migrate the oldest 10
    .venv/Scripts/python scripts/reprocess_legacy.py --apply --ids 12,15
    .venv/Scripts/python scripts/reprocess_legacy.py --apply --refresh-ocr   # also re-read images
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import SessionLocal  # noqa: E402
from backend.models import Inspection  # noqa: E402
from backend.models.inspection_revision import REVISION_KIND_MIGRATION  # noqa: E402
from backend.services import reprocess_service  # noqa: E402
from backend.version import ENGINE_VERSION  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually reprocess (default: dry run)")
    parser.add_argument("--limit", type=int, default=reprocess_service.MAX_BATCH)
    parser.add_argument("--ids", default="", help="comma-separated inspection ids (bypasses the legacy filter)")
    parser.add_argument("--reason", default="engine upgrade: regenerate existing inspections")
    parser.add_argument("--refresh-ocr", action="store_true", help="also re-read the package images")
    parser.add_argument("--actor", default="migration-script")
    args = parser.parse_args()

    ids = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else None

    db = SessionLocal()
    try:
        preview = reprocess_service.legacy_preview(db, limit=args.limit)
        print(f"engine {preview['engine_version']} ({ENGINE_VERSION}) · rule set {preview['rule_fingerprint']}"
              f" · {preview['rule_count']} rule versions")
        print(f"inspections total {preview['total_inspections']} · reprocessable {preview['reprocessable_total']}"
              f" · legacy {preview['legacy_count']} · current {preview['current_count']}")

        targets = ids or [item["inspection_id"] for item in preview["items"]]
        targets = targets[: args.limit]
        if not targets:
            print("nothing to reprocess — every reprocessable inspection is on the current engine")
            return 0

        print(f"{'APPLYING' if args.apply else 'DRY RUN'} on {len(targets)} record(s)")
        field_kinds: Counter[str] = Counter()
        changed_fields: Counter[str] = Counter()
        score_deltas: list[float] = []
        classification_changes = 0
        rules_changed_total = 0
        failures = 0

        for inspection_id in targets:
            inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
            if inspection is None:
                continue
            if not args.apply:
                print(f"  #{inspection_id} {inspection.inspection_number} would be reprocessed"
                      f" (engine {inspection.pipeline_version or 'unstamped'})")
                continue
            result = reprocess_service.reprocess_inspection(
                db,
                inspection,
                actor=args.actor,
                reason=args.reason,
                kind=REVISION_KIND_MIGRATION,
                refresh_ocr=args.refresh_ocr,
            )
            changes = result["changes"]
            if not result["ok"]:
                failures += 1
            field_kinds["changed_fields"] += changes["changed"]["fields_changed"]
            rules_changed_total += changes["changed"]["rules_changed"]
            for row in changes["fields"]:
                field_kinds[row["kind"]] += 1
                changed_fields[row["field_name"]] += 1
            if changes["changed"]["classification_changed"]:
                classification_changes += 1
            if changes["score"]["before"] is not None and changes["score"]["after"] is not None:
                score_deltas.append(changes["score"]["after"] - changes["score"]["before"])
            print(
                f"  #{inspection_id} {result['inspection_number']}: "
                f"{changes['changed']['fields_changed']} field(s), {changes['changed']['rules_changed']} rule(s) — "
                + ("; ".join(changes["headline"][:3]) or "no material change")
            )

        if args.apply and targets:
            print("\n---------- summary ----------")
            print(f"records reprocessed: {len(targets) - failures} (failed: {failures})")
            print(f"declaration changes: {dict(field_kinds)}")
            if changed_fields:
                print("most-changed declarations: " + ", ".join(f"{k} ×{v}" for k, v in changed_fields.most_common(8)))
            print(f"rule results changed: {rules_changed_total} · classification changed: {classification_changes}")
            if score_deltas:
                up = sum(1 for d in score_deltas if d > 0.05)
                down = sum(1 for d in score_deltas if d < -0.05)
                print(f"compliance moved up on {up}, down on {down}, unchanged on {len(score_deltas) - up - down} scans")
        return 1 if failures else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
