"""POCKET dataset tooling: validate, split, ingest, stats.

Dataset layout, annotation schema and split policy: see data/README.md.

Commands
--------
    python scripts/dataset.py stats
    python scripts/dataset.py validate [--strict]
    python scripts/dataset.py split [--seed 20260915] [--ratios 0.7,0.15,0.15]
    python scripts/dataset.py ingest <product_id> <view> <image> [<image> ...]

Nothing here downloads data, fabricates annotations or invents metrics. Real-image accuracy is
measured only by scripts/run_benchmark.py from human ground truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
ANNOTATIONS = DATA / "annotations"
SPLITS = DATA / "splits"
VALIDATION_REPORT = ROOT / "docs" / "DATASET_VALIDATION_REPORT.md"


def set_data_dir(path: str | Path) -> None:
    """Point the tooling at another dataset root (used by --data-dir and by tests)."""
    global DATA, RAW, ANNOTATIONS, SPLITS
    DATA = Path(path)
    RAW = DATA / "raw"
    ANNOTATIONS = DATA / "annotations"
    SPLITS = DATA / "splits"

VALID_VIEWS = {"front", "back", "left", "right", "side", "top", "bottom", "other"}
VALID_QUALITY = {"GOOD", "FAIR", "POOR", "UNUSABLE"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
# Field names POCKET can measure; kept in sync with the extraction field schema.
KNOWN_FIELDS = {
    "brand", "product_name", "common_name", "mrp", "unit_sale_price", "net_quantity",
    "batch_lot", "date_manufacturing", "date_packing", "date_import", "date_expiry",
    "date_best_before", "manufacturer", "manufacturer_address", "packer", "packer_address",
    "importer", "importer_address", "marketer", "country_of_origin", "fssai_license",
    "consumer_care_phone", "consumer_care_email", "website",
}


def _load_annotation(path: Path) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # unreadable / invalid JSON
        return None, f"invalid JSON: {exc}"
    if not isinstance(data, dict):
        return None, "annotation root is not an object"
    return data, None


def _image_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def collect() -> tuple[list[dict], list[str]]:
    """Return (products, problems) from data/raw + data/annotations."""
    problems: list[str] = []
    products: list[dict] = []
    seen_ids: dict[str, str] = {}

    if not ANNOTATIONS.exists():
        return [], []
    for path in sorted(ANNOTATIONS.glob("*.json")):
        if path.name.startswith("_"):
            continue
        ann, err = _load_annotation(path)
        pid = path.stem
        if err:
            problems.append(f"{path.name}: {err}")
            continue
        assert ann is not None
        declared_pid = str(ann.get("product_id") or pid)
        if declared_pid != pid:
            problems.append(f"{path.name}: product_id '{declared_pid}' does not match filename '{pid}'")
        if declared_pid in seen_ids:
            problems.append(f"duplicate product_id '{declared_pid}' ({path.name} and {seen_ids[declared_pid]})")
        seen_ids[declared_pid] = path.name

        views = ann.get("views") or []
        if not views:
            problems.append(f"{path.name}: no views listed")
        for view in views:
            if not isinstance(view, dict) or not view.get("file"):
                problems.append(f"{path.name}: malformed view entry {view!r}")
                continue
            if view.get("view") not in VALID_VIEWS:
                problems.append(f"{path.name}: unknown view kind '{view.get('view')}'")
            if view.get("quality") and view["quality"] not in VALID_QUALITY:
                problems.append(f"{path.name}: unknown quality '{view['quality']}'")
            image_path = RAW / declared_pid / str(view["file"])
            if not image_path.exists():
                problems.append(f"{path.name}: missing image file raw/{declared_pid}/{view['file']}")
            elif image_path.suffix.lower() not in IMAGE_SUFFIXES:
                problems.append(f"{path.name}: unsupported image type '{image_path.suffix}'")

        fields = ann.get("fields") or {}
        if not isinstance(fields, dict):
            problems.append(f"{path.name}: 'fields' is not an object")
            fields = {}
        for name, spec in fields.items():
            if name not in KNOWN_FIELDS:
                problems.append(f"{path.name}: unknown field name '{name}' (not in the extraction schema)")
            if not isinstance(spec, dict):
                problems.append(f"{path.name}: field '{name}' is not an object")
                continue
            value = spec.get("value")
            if value is None or str(value).strip() == "":
                problems.append(
                    f"{path.name}: field '{name}' has an empty value — omit the field instead of "
                    "asserting an empty declaration"
                )
            bbox = spec.get("bbox")
            if bbox is not None:
                if not (isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(v, int) for v in bbox)):
                    problems.append(f"{path.name}: field '{name}' bbox must be [x1,y1,x2,y2] integers")
                elif not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
                    problems.append(f"{path.name}: field '{name}' bbox is degenerate {bbox}")
                if spec.get("image") is None:
                    problems.append(f"{path.name}: field '{name}' has a bbox but no source image")
        products.append({"product_id": declared_pid, "path": path, "annotation": ann})

    # images present with no annotation, and duplicate image content
    if RAW.exists():
        annotated_files: set[str] = set()
        for product in products:
            for view in (product["annotation"].get("views") or []):
                if isinstance(view, dict) and view.get("file"):
                    annotated_files.add(f"{product['product_id']}/{view['file']}")
        digests: dict[str, str] = {}
        for image in sorted(RAW.rglob("*")):
            if not image.is_file() or image.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            rel = image.relative_to(RAW).as_posix()
            if rel not in annotated_files:
                problems.append(f"raw/{rel}: image has no annotation entry")
            digest = _image_digest(image)
            if digest in digests:
                problems.append(f"raw/{rel}: duplicate image content of raw/{digests[digest]}")
            else:
                digests[digest] = rel
    return products, problems


def check_split_leakage(problems: list[str]) -> None:
    """A product must never appear in more than one split."""
    load = {}
    for name in ("train", "validation", "test"):
        path = SPLITS / f"{name}.txt"
        load[name] = (
            [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
            if path.exists()
            else []
        )
    ids_seen: dict[str, str] = {}
    for name, ids in load.items():
        for pid in ids:
            if pid in ids_seen:
                problems.append(
                    f"split leakage: product '{pid}' in both {ids_seen[pid]} and {name} "
                    "(splits must be by product, not by image)"
                )
            ids_seen[pid] = name


def cmd_stats(_args) -> int:
    products, problems = collect()
    print(f"products annotated : {len(products)}")
    views = sum(len([v for v in (p['annotation'].get('views') or []) if isinstance(v, dict)]) for p in products)
    fields = sum(len(p["annotation"].get("fields") or {}) for p in products)
    print(f"views referenced   : {views}")
    print(f"fields annotated   : {fields}")
    if products:
        by_cat: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for p in products:
            by_cat[str(p["annotation"].get("category") or "unknown")] = by_cat.get(str(p["annotation"].get("category") or "unknown"), 0) + 1
            by_type[str(p["annotation"].get("package_type") or "unknown")] = by_type.get(str(p["annotation"].get("package_type") or "unknown"), 0) + 1
        print(f"categories         : {by_cat}")
        print(f"package types      : {by_type}")
    else:
        print("No real dataset yet — real-image accuracy is therefore 'insufficient data'.")
    print(f"validation problems: {len(problems)}")
    for problem in problems:
        print(f"  - {problem}")
    return 0


def write_validation_report(products: list[dict], problems: list[str]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Dataset validation report",
        "",
        f"Generated: {now} by `scripts/dataset.py validate`",
        "",
        f"- products annotated: **{len(products)}**",
        f"- problems found: **{len(problems)}**",
        "",
    ]
    if not problems:
        lines += ["No problems found.", ""]
    else:
        lines += ["| # | problem |", "|---|---|"]
        lines += [f"| {i} | {p.replace('|', '/')} |" for i, p in enumerate(problems, 1)]
        lines.append("")
    lines += [
        "## Notes",
        "",
        "- Fields omitted from an annotation mean the human ground truth says nothing about that",
        "  field. An empty string is NOT valid ground truth for 'this declaration is absent'.",
        "- Splits are by product id; an image duplicate across splits is leakage and is reported above.",
        "- This report never asserts accuracy — see `docs/SCANNING_ACCURACY_REPORT.md`.",
        "",
    ]
    VALIDATION_REPORT.parent.mkdir(parents=True, exist_ok=True)
    VALIDATION_REPORT.write_text("\n".join(lines), encoding="utf-8")


def cmd_validate(args) -> int:
    products, problems = collect()
    check_split_leakage(problems)
    write_validation_report(products, problems)
    print(f"products: {len(products)}  problems: {len(problems)}")
    for problem in problems:
        print(f"  - {problem}")
    try:
        shown = VALIDATION_REPORT.relative_to(ROOT)
    except ValueError:  # --data-dir pointed outside the repository
        shown = VALIDATION_REPORT
    print(f"report written: {shown}")
    if args.strict and problems:
        return 1
    return 0


def cmd_split(args) -> int:
    products, problems = collect()
    if problems:
        print("Refusing to split: dataset has validation problems. Run `validate` first.")
        for problem in problems[:20]:
            print(f"  - {problem}")
        return 1
    ids = sorted(p["product_id"] for p in products)
    if not ids:
        print("No annotated products to split.")
        return 1
    ratios = [float(x) for x in args.ratios.split(",")]
    if len(ratios) != 3 or abs(sum(ratios) - 1.0) > 1e-6:
        print("--ratios must be three values summing to 1.0, e.g. 0.7,0.15,0.15")
        return 2
    rng = random.Random(args.seed)
    shuffled = ids[:]
    rng.shuffle(shuffled)
    n = len(shuffled)
    n_train = max(1, int(round(n * ratios[0]))) if n >= 3 else max(0, n - 2)
    n_val = max(1, int(round(n * ratios[1]))) if n >= 3 else 0
    if n_train + n_val >= n:  # keep at least one product in test
        n_train = max(0, n - 2)
        n_val = min(n_val, max(0, n - 1 - n_train))
    groups = {
        "train": shuffled[:n_train],
        "validation": shuffled[n_train:n_train + n_val],
        "test": shuffled[n_train + n_val:],
    }
    SPLITS.mkdir(parents=True, exist_ok=True)
    for name, members in groups.items():
        header = (
            f"# POCKET {name} split — product ids, split by PRODUCT (seed={args.seed}, "
            f"ratios={args.ratios}). Generated by scripts/dataset.py; do not edit by hand.\n"
        )
        (SPLITS / f"{name}.txt").write_text(header + "\n".join(members) + "\n", encoding="utf-8")
        print(f"{name:11} {len(members):3} products")
    return 0


def cmd_ingest(args) -> int:
    product_id = args.product_id
    if args.view not in VALID_VIEWS:
        print(f"--view must be one of {sorted(VALID_VIEWS)}")
        return 2
    target_dir = RAW / product_id
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for image in args.images:
        src = Path(image)
        if not src.is_file():
            print(f"not a file: {image}")
            return 2
        if src.suffix.lower() not in IMAGE_SUFFIXES:
            print(f"unsupported image type: {image} (allowed: {sorted(IMAGE_SUFFIXES)})")
            return 2
        dest = target_dir / src.name
        if dest.exists() and dest.read_bytes() != src.read_bytes():
            print(f"refusing to overwrite existing evidence: {dest}")
            return 1
        if not dest.exists():
            shutil.copy2(src, dest)  # copy, never move: the original stays where the capture left it
        copied.append(src.name)

    ann_path = ANNOTATIONS / f"{product_id}.json"
    ANNOTATIONS.mkdir(parents=True, exist_ok=True)
    if ann_path.exists():
        ann = json.loads(ann_path.read_text(encoding="utf-8"))
    else:
        ann = {
            "product_id": product_id,
            "category": args.category or "",
            "package_type": args.package_type or "",
            "annotator": "",
            "annotated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "views": [],
            "fields": {},
            "_todo": "Fill in the fields you can read on the package. Omit what is not visible. Never guess.",
        }
    known = {v.get("file") for v in ann["views"] if isinstance(v, dict)}
    for name in copied:
        if name not in known:
            ann["views"].append({"file": name, "view": args.view, "quality": args.quality})
    ann_path.write_text(json.dumps(ann, indent=2) + "\n", encoding="utf-8")
    print(f"copied {len(copied)} image(s) to raw/{product_id}/ and updated annotations/{product_id}.json")
    print("Next: fill in the annotation fields by hand, then run `validate`.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--data-dir",
        default=None,
        help="dataset root (default: ./data, or $POCKET_DATA_DIR)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="count products/views/fields and list validation problems").set_defaults(func=cmd_stats)

    validate = sub.add_parser("validate", help="validate the dataset and write a report")
    validate.add_argument("--strict", action="store_true", help="exit non-zero when problems exist")
    validate.set_defaults(func=cmd_validate)

    split = sub.add_parser("split", help="generate product-level train/validation/test splits")
    split.add_argument("--seed", type=int, default=20260915)
    split.add_argument("--ratios", default="0.7,0.15,0.15")
    split.set_defaults(func=cmd_split)

    ingest = sub.add_parser("ingest", help="copy captured photos in and create an annotation skeleton")
    ingest.add_argument("product_id")
    ingest.add_argument("view")
    ingest.add_argument("images", nargs="+")
    ingest.add_argument("--category", default="")
    ingest.add_argument("--package-type", dest="package_type", default="")
    ingest.add_argument("--quality", default="GOOD", choices=sorted(VALID_QUALITY))
    ingest.set_defaults(func=cmd_ingest)

    args = parser.parse_args(argv)
    data_dir = args.data_dir or os.environ.get("POCKET_DATA_DIR")
    if data_dir:
        set_data_dir(data_dir)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
