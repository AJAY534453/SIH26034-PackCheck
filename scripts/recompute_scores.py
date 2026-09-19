"""Re-derive stored repository scores from the stored rule evaluations.

The compliance percentage and the evidence-coverage figure are computed from the ``rule_evaluations``
already on disk — nothing is re-OCR'd and no legal outcome is re-decided. This script exists because
the scoring model is versioned in DATA: when the declared weights in
``backend/rules/definitions/*.json`` change, or when the compliance/coverage split is applied to
history, the stored numbers are refreshed deliberately and visibly instead of drifting apart from
the rule library.

A recorded human finalization is never discarded (``repository_service.refresh_scan`` preserves it).

Usage::

    .venv/Scripts/python scripts/recompute_scores.py                  # dry run: show the changes
    .venv/Scripts/python scripts/recompute_scores.py --apply          # write the new numbers
    .venv/Scripts/python scripts/recompute_scores.py --apply --inspection 56
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.database import SessionLocal  # noqa: E402
from backend.models import Inspection, ProductScan  # noqa: E402
from backend.rules.registry import seed_rules  # noqa: E402
from backend.services import compliance_service, repository_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the recomputed values")
    parser.add_argument("--inspection", type=int, default=None, help="only this inspection id")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Seed first: the declared weights/criticality must be current before anything is scored.
        stats = seed_rules(db)
        print(
            f"rule library: {stats['created']} created, {stats['versioned']} versioned, "
            f"{stats['unchanged']} unchanged"
        )
        print(
            f"scoring model: threshold {compliance_service.AI_COMPLIANT_THRESHOLD:g}% compliance, "
            f"coverage floor {compliance_service.COVERAGE_FLOOR:g}%\n"
        )

        query = db.query(ProductScan).order_by(ProductScan.id)
        if args.inspection is not None:
            query = query.filter(ProductScan.inspection_id == args.inspection)
        scans = query.all()
        if not scans:
            print("no repository scans to recompute")
            return 0

        changed = 0
        for scan in scans:
            inspection = db.get(Inspection, scan.inspection_id)
            if inspection is None:
                print(f"scan {scan.id}: inspection {scan.inspection_id} is gone — skipped")
                continue
            review = compliance_service.build_ai_review(db, inspection)
            before = (scan.compliance_score, scan.coverage_score, scan.ai_verdict)
            after = (review["score"], review["coverage"], review["verdict"])
            if before != after:
                changed += 1
                print(
                    f"scan {scan.id} ({scan.scan_number or scan.inspection_id}): "
                    f"compliance {before[0]}% → {after[0]}%, "
                    f"coverage {before[1]}% → {after[1]}%, "
                    f"verdict {before[2] or '-'} → {after[2]}"
                )
                if args.apply:
                    repository_service.refresh_scan(db, inspection)

        print(f"\n{len(scans)} scan(s) inspected, {changed} would change")
        if args.apply:
            print("applied — the repository now reflects the current rule weights and scoring model")
        elif changed:
            print("dry run only: re-run with --apply to write these values")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
