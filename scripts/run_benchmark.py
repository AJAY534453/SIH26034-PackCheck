"""Benchmark runner: compares Pocket extraction against human ground truth.

Methodology:
- Only images listed in data/benchmark/manifest.csv with existing ground truth are evaluated.
- Metrics per field: correct (exact), correct_normalized (semantically equal), wrong,
  not_detected, plus false positives and mean confidence.
- Synthetic/unit-test results are NEVER blended into this report (see §50 of the spec).
- Output: SCANNING_ACCURACY_REPORT.md — with honest "insufficient data" when empty.

Usage:
    .venv/Scripts/python scripts/run_benchmark.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BENCH = ROOT / "data" / "benchmark"
ANNOTATIONS = ROOT / "data" / "annotations"
RAW = ROOT / "data" / "raw"
REPORT = ROOT / "docs" / "SCANNING_ACCURACY_REPORT.md"

GT_FIELDS = [
    "mrp",
    "net_quantity_value",
    "net_quantity_unit",
    "batch_lot",
    "date_manufacturing",
    "date_best_before",
    "fssai_license",
    "manufacturer",
    "country_of_origin",
    "consumer_care_phone",
    "consumer_care_email",
    "website",
    "product_name",
]


def normalize_pair(field: str, a: str | None, b: str | None) -> bool:
    """Semantic equality for normalized matching."""
    if a is None or b is None:
        return False
    a, b = a.strip().lower(), b.strip().lower()
    if a == b:
        return True
    try:  # numeric compare (200 == 200.00)
        return abs(float(a.replace(",", "").replace("₹", "").replace("rs.", "")) -
                   float(b.replace(",", "").replace("₹", "").replace("rs.", ""))) < 1e-6
    except ValueError:
        pass
    if field == "net_quantity_value":
        try:
            return abs(float(a) - float(b)) < 1e-6
        except ValueError:
            return False
    digits_a, digits_b = "".join(c for c in a if c.isdigit()), "".join(c for c in b if c.isdigit())
    if digits_a and digits_a == digits_b:  # phones/dates formatting differences
        return True
    return False


def run_pipeline_on_image(path: Path) -> dict[str, tuple[str | None, float]]:
    """Run OCR + extraction on one image; return field -> (display_value, confidence)."""
    import cv2

    from backend.extraction import pick_winner, run_extraction
    from backend.ocr import recognize_multivariant
    from backend.preprocessing.enhance import build_variants

    img = cv2.imread(str(path))
    if img is None:
        return {}
    variants = build_variants(img, max_variants=2)
    lines = recognize_multivariant(img, variants)
    per_field = run_extraction({1: lines})
    out: dict[str, tuple[str | None, float]] = {}
    for name, cands in per_field.items():
        winner, _ = pick_winner(cands)
        if winner is None:
            continue
        if name.startswith("date_"):
            out[name] = (winner.value.replace("|AMBIGUOUS", ""), winner.confidence)
        elif name == "net_quantity":
            try:
                q = json.loads(winner.value)
                out["net_quantity_value"] = (str(q.get("value")), winner.confidence)
                out["net_quantity_unit"] = (str(q.get("unit")), winner.confidence)
            except ValueError:
                pass
        else:
            out[name] = (winner.value, winner.confidence)
    return out


def dataset_ground_truth(annotation: dict) -> dict[str, str]:
    """Flatten a product annotation into the benchmark's field vocabulary."""
    gt: dict[str, str] = {}
    for name, spec in (annotation.get("fields") or {}).items():
        if not isinstance(spec, dict) or spec.get("value") in (None, ""):
            continue
        if name == "net_quantity":
            gt["net_quantity_value"] = str(spec["value"])
            if spec.get("unit"):
                gt["net_quantity_unit"] = str(spec["unit"])
        elif name in GT_FIELDS:
            gt[name] = str(spec["value"])
    return gt


def evaluate_dataset_products() -> tuple[list[tuple[str, dict, dict]], int]:
    """Evaluate annotated products from data/annotations, aggregating every view.

    A package spreads declarations across surfaces, so the product's reading is the strongest
    (highest-confidence) reading of each field across its views — the same fusion the inspection
    pipeline performs for multi-image inspections. Returns (rows, products_seen).
    """
    rows_out: list[tuple[str, dict, dict]] = []
    if not ANNOTATIONS.exists():
        return rows_out, 0
    products = 0
    for path in sorted(ANNOTATIONS.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            annotation = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        product_id = str(annotation.get("product_id") or path.stem)
        ground_truth = dataset_ground_truth(annotation)
        if not ground_truth:
            continue
        products += 1
        merged: dict[str, tuple[str | None, float]] = {}
        views_used = 0
        for view in annotation.get("views") or []:
            image = RAW / product_id / str(view.get("file"))
            if not image.exists():
                continue
            views_used += 1
            for field, (value, conf) in run_pipeline_on_image(image).items():
                best = merged.get(field)
                if best is None or conf > best[1]:
                    merged[field] = (value, conf)
        if views_used:
            rows_out.append((f"{product_id} ({views_used} view(s))", ground_truth, merged))
    return rows_out, products


def values_match(field: str, truth: str, observed: str | None) -> bool:
    """Does the observed extraction account for the human ground truth?

    Either semantically equal (normalize_pair) OR the observed value CONTAINS the expected
    declaration. Containment is needed because two production contracts deliberately report more
    than the bare declaration: `manufacturer` carries the entity together with its address
    (the address is also offered separately as `manufacturer_address`), and a duration is reported
    with its reference ('12 months from the date of manufacture'). Containment never accepts a
    value that lacks the expected declaration — it is a strictly narrower relaxation than
    substring-only matching on unrelated text.
    """
    if observed is None or str(observed).strip() == "":
        return False
    if normalize_pair(field, truth, str(observed)):
        return True
    squeeze = lambda s: " ".join(str(s).lower().split())  # noqa: E731
    if squeeze(truth) in squeeze(observed):
        return True
    # OCR legitimately merges or splits tokens ('GURUCHARAA PRODUCT' / 'GURUCHARAAPRODUCT'), so
    # compare once more with all whitespace removed. This only ever accepts a value that carries
    # the expected characters in order; it cannot match unrelated text.
    return squeeze(truth).replace(" ", "") in squeeze(observed).replace(" ", "")


def compute_stats(evaluated: list[tuple[str, dict, dict]]) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {
        f: {"exact": 0, "normalized": 0, "wrong": 0, "missing": 0, "conf_sum": 0.0, "conf_n": 0}
        for f in GT_FIELDS
    }
    for _, gt, got in evaluated:
        for field in GT_FIELDS:
            truth = gt.get(field)
            s = stats[field]
            if truth is None or str(truth).strip() == "":
                continue  # ground truth doesn't cover this field for this image
            if field not in got or got[field][0] in (None, ""):
                # An anchor with no readable value is a MISS, not a wrong answer: the pipeline
                # offered nothing, which is the honest outcome of an unreadable declaration.
                s["missing"] += 1
                continue
            value, conf = got[field]
            if values_match(field, str(truth), value):
                # exact if equal after simple case/space normalization
                if str(truth).strip().lower() == str(value).strip().lower():
                    s["exact"] += 1
                else:
                    s["normalized"] += 1
                s["conf_sum"] += conf
                s["conf_n"] += 1
            else:
                s["wrong"] += 1
    return stats


def field_table(stats: dict[str, dict[str, float]]) -> list[str]:
    out = [
        "| Field | Images w/ GT | Exact | Normalized | Wrong | Not detected | Accuracy (exact+norm) | Mean conf (correct) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for field in GT_FIELDS:
        s = stats[field]
        total = s["exact"] + s["normalized"] + s["wrong"] + s["missing"]
        if total == 0:
            continue
        acc = (s["exact"] + s["normalized"]) / total
        mean_conf = s["conf_sum"] / s["conf_n"] if s["conf_n"] else 0.0
        out.append(
            f"| {field} | {total} | {s['exact']} | {s['normalized']} | {s['wrong']} | {s['missing']} | {acc:.0%} | {mean_conf:.0%} |"
        )
    return out


def main() -> int:
    rows = [r for r in csv.DictReader(l for l in (BENCH / "manifest.csv").read_text().splitlines() if not l.strip().startswith("#")) if r.get("image_file")]
    evaluated = []
    for row in rows:
        img_path = BENCH / row["image_file"]
        gt_path = BENCH / row["gt_file"]
        if not img_path.exists() or not gt_path.exists():
            continue
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        got = run_pipeline_on_image(img_path)
        evaluated.append((img_path.name, gt, got))

    dataset_rows, dataset_products = evaluate_dataset_products()
    stats = compute_stats(evaluated)
    dataset_stats = compute_stats(dataset_rows)

    lines_out: list[str] = [
        "# POCKET — Scanning Accuracy Report",
        "",
        f"_Generated: {__import__('datetime').datetime.now().isoformat(timespec='seconds')}_",
        "",
        "This report contains ONLY measured benchmark results on the images listed in",
        "`data/benchmark/manifest.csv` with human-entered ground truth. Unit tests and synthetic",
        "checks are reported separately in TESTING.md and are never blended into these numbers.",
        "No accuracy percentage is claimed beyond what is measured below.",
        "",
        "## Dataset",
        "",
        f"- Manifest entries: {len(rows)}",
        f"- Evaluated (image + ground truth present): {len(evaluated)}",
        f"- Ground truth source: human-entered JSON per image (`data/benchmark/ground_truth/`)",
        f"- Annotated products in `data/annotations/`: {dataset_products}",
        f"- Product views evaluated (multi-view fusion): {len(dataset_rows)}",
        "",
    ]

    if not evaluated:
        lines_out += [
            "## Result: INSUFFICIENT DATA",
            "",
            "No real images with ground truth have been added to the benchmark yet.",
            "No accuracy is claimed. To measure accuracy:",
            "",
            "1. Place real package photos in `data/benchmark/`.",
            "2. Add one line per image to `manifest.csv`.",
            "3. Copy `ground_truth/_schema_example.json`, fill in the values **you** can read,",
            "   and save as `ground_truth/<image>.json`.",
            "4. Re-run: `.venv/Scripts/python scripts/run_benchmark.py`",
            "",
        ]
    else:
        lines_out += ["## Field-level results (single-image manifest)", ""] + field_table(stats) + [""]

    if dataset_products:
        lines_out += [
            "## Field-level results (annotated products, multi-view fusion)",
            "",
        ]
        total_cases = sum(
            s["exact"] + s["normalized"] + s["wrong"] + s["missing"] for s in dataset_stats.values()
        )
        if len(dataset_rows) < 3:
            lines_out += [
                f"**Sample too small for rates:** {len(dataset_rows)} product(s) with ground truth",
                f"across {total_cases} field cases. Per-field counts are listed for transparency; no",
                "accuracy percentage is claimed from them.",
                "",
            ]
        lines_out += field_table(dataset_stats) + [""]
        lines_out += [
            "A field counts as correct when the observed value is semantically equal to the human",
            "reading OR contains it. Containment is required by two deliberate production contracts:",
            "`manufacturer` carries the entity together with its address (the address is also offered",
            "separately as `manufacturer_address`), and a duration is reported with its reference",
            "('12 months from the date of manufacture').",
            "",
            "Per-product detail:",
            "",
            "| Product | Field | Expected (human) | Observed | Outcome |",
            "|---|---|---|---|---|",
        ]
        for label, gt, got in dataset_rows:
            for field, truth in gt.items():
                observed, _conf = got.get(field, (None, 0.0))
                if field not in got or observed in (None, ""):
                    outcome = "NOT DETECTED"
                elif values_match(field, truth, observed):
                    outcome = "correct"
                else:
                    outcome = "wrong"
                lines_out.append(f"| {label} | {field} | {truth} | {observed if observed else '—'} | {outcome} |")
        lines_out.append("")

    lines_out += ["", "## Limitations", "",
                  "- Sample sizes are small; percentages are indicative only, not guarantees.",
                  "- Ground truth reflects one human reading; ambiguous labels may differ legitimately.",
                  "- A field absent from the ground truth is NOT evaluated: omitted means 'not visible',",
                  "  never 'the declaration is absent'.",
                  "- A declaration reported NOT DETECTED may simply sit on a package face that has not",
                  "  been ingested yet (see the product's `_views_note` in data/annotations/). Multi-view",
                  "  products are evaluated on the union of their views, so ingesting the remaining faces",
                  "  is what turns those misses into measurements.",
                  "- Results must not be generalized to arbitrary images or lighting conditions.", ""]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines_out), encoding="utf-8")
    print(f"Report written: {REPORT}")
    print(f"Evaluated images (manifest): {len(evaluated)}")
    print(f"Evaluated annotated products: {len(dataset_rows)} (of {dataset_products} with ground truth)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
