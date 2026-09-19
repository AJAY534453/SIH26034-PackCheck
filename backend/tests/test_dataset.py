"""Dataset tooling tests: validation, product-level splitting, ingestion.

The tooling is what guards every accuracy claim, so its own failure modes are tested:
a validator that misses leakage or an empty "ground truth" value would silently inflate metrics.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts import dataset as ds  # noqa: E402


@pytest.fixture()
def tmp_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "VALIDATION_REPORT", tmp_path / "DATASET_VALIDATION_REPORT.md")
    ds.set_data_dir(tmp_path)
    (tmp_path / "raw" / "p0001").mkdir(parents=True)
    (tmp_path / "annotations").mkdir(parents=True)
    yield tmp_path
    ds.set_data_dir(ROOT / "data")  # restore


def _write_image(path: Path, payload: bytes = b"fake-jpeg-bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _write_annotation(root: Path, product_id: str, fields: dict | None = None, views: list | None = None) -> None:
    ann = {
        "product_id": product_id,
        "category": "biscuit",
        "package_type": "pouch",
        "views": views if views is not None else [{"file": "front.jpg", "view": "front", "quality": "GOOD"}],
        "fields": fields if fields is not None else {"mrp": {"value": "5.00"}},
    }
    (root / "annotations" / f"{product_id}.json").write_text(json.dumps(ann), encoding="utf-8")


def test_clean_dataset_has_no_problems(tmp_dataset):
    _write_image(tmp_dataset / "raw" / "p0001" / "front.jpg")
    _write_annotation(tmp_dataset, "p0001")
    products, problems = ds.collect()
    assert [p["product_id"] for p in products] == ["p0001"]
    assert problems == []


def test_missing_image_is_reported(tmp_dataset):
    _write_annotation(tmp_dataset, "p0001")  # no image written
    _, problems = ds.collect()
    assert any("missing image file" in p for p in problems)


def test_unannotated_image_is_reported(tmp_dataset):
    _write_image(tmp_dataset / "raw" / "p0001" / "front.jpg")
    _write_annotation(tmp_dataset, "p0001")
    _write_image(tmp_dataset / "raw" / "p0001" / "back.jpg")  # not listed in the annotation
    _, problems = ds.collect()
    assert any("image has no annotation entry" in p for p in problems)


def test_duplicate_image_content_is_reported(tmp_dataset):
    _write_image(tmp_dataset / "raw" / "p0001" / "front.jpg", b"identical")
    _write_image(tmp_dataset / "raw" / "p0002" / "front.jpg", b"identical")
    _write_annotation(tmp_dataset, "p0001")
    _write_annotation(tmp_dataset, "p0002")
    _, problems = ds.collect()
    assert any("duplicate image content" in p for p in problems)


def test_empty_field_value_is_rejected(tmp_dataset):
    """An empty string is not valid ground truth for 'this declaration is absent'."""
    _write_image(tmp_dataset / "raw" / "p0001" / "front.jpg")
    _write_annotation(tmp_dataset, "p0001", fields={"mrp": {"value": ""}})
    _, problems = ds.collect()
    assert any("empty value" in p for p in problems)


def test_unknown_field_name_is_rejected(tmp_dataset):
    _write_image(tmp_dataset / "raw" / "p0001" / "front.jpg")
    _write_annotation(tmp_dataset, "p0001", fields={"made_up_field": {"value": "x"}})
    _, problems = ds.collect()
    assert any("unknown field name" in p for p in problems)


def test_bad_bbox_is_rejected(tmp_dataset):
    _write_image(tmp_dataset / "raw" / "p0001" / "front.jpg")
    _write_annotation(tmp_dataset, "p0001", fields={"mrp": {"value": "5", "bbox": [10, 10, 5, 5], "image": "front.jpg"}})
    _, problems = ds.collect()
    assert any("degenerate" in p for p in problems)


def test_bbox_without_source_image_is_rejected(tmp_dataset):
    _write_image(tmp_dataset / "raw" / "p0001" / "front.jpg")
    _write_annotation(tmp_dataset, "p0001", fields={"mrp": {"value": "5", "bbox": [1, 2, 30, 40]}})
    _, problems = ds.collect()
    assert any("no source image" in p for p in problems)


def test_invalid_json_is_reported(tmp_dataset):
    (tmp_dataset / "annotations" / "broken.json").write_text("{not json", encoding="utf-8")
    _, problems = ds.collect()
    assert any("invalid JSON" in p for p in problems)


def test_split_leakage_is_detected(tmp_dataset):
    (tmp_dataset / "splits").mkdir(parents=True, exist_ok=True)
    (tmp_dataset / "splits" / "train.txt").write_text("p0001\n", encoding="utf-8")
    (tmp_dataset / "splits" / "test.txt").write_text("p0001\n", encoding="utf-8")
    problems: list[str] = []
    ds.check_split_leakage(problems)
    assert any("split leakage" in p for p in problems)


def test_split_is_product_level_and_reproducible(tmp_dataset):
    for pid in ("p0001", "p0002", "p0003", "p0004", "p0005", "p0006", "p0007", "p0008", "p0009", "p0010"):
        _write_image(tmp_dataset / "raw" / pid / "front.jpg", pid.encode())
        _write_annotation(tmp_dataset, pid)

    assert ds.main(["--data-dir", str(tmp_dataset), "split", "--seed", "7"]) == 0
    first = {
        name: (tmp_dataset / "splits" / f"{name}.txt").read_text(encoding="utf-8")
        for name in ("train", "validation", "test")
    }
    sizes = {name: len([l for l in body.splitlines() if l and not l.startswith("#")]) for name, body in first.items()}
    assert sum(sizes.values()) == 10, sizes
    assert sizes["test"] >= 1 and sizes["train"] >= 1

    # every product appears exactly once
    all_ids = [
        l for body in first.values() for l in body.splitlines() if l and not l.startswith("#")
    ]
    assert sorted(all_ids) == sorted(f"p{i:04d}" for i in range(1, 11))

    # same seed -> same split
    assert ds.main(["--data-dir", str(tmp_dataset), "split", "--seed", "7"]) == 0
    second = {
        name: (tmp_dataset / "splits" / f"{name}.txt").read_text(encoding="utf-8") for name in first
    }
    assert first == second


def test_split_refuses_when_dataset_is_invalid(tmp_dataset):
    _write_annotation(tmp_dataset, "p0001")  # annotation without its image
    assert ds.main(["--data-dir", str(tmp_dataset), "split"]) == 1


def test_ingest_copies_evidence_and_creates_a_skeleton(tmp_dataset, tmp_path):
    source = tmp_path / "capture.jpg"
    source.write_bytes(b"photo-bytes")
    assert ds.main(["--data-dir", str(tmp_dataset), "ingest", "p0042", "front", str(source)]) == 0
    assert (tmp_dataset / "raw" / "p0042" / "capture.jpg").read_bytes() == b"photo-bytes"
    ann = json.loads((tmp_dataset / "annotations" / "p0042.json").read_text(encoding="utf-8"))
    assert ann["fields"] == {}  # no fabricated values
    assert ann["views"][0]["view"] == "front"
    # the original capture is copied, never moved
    assert source.exists()


def test_validate_strict_exits_nonzero_on_problems(tmp_dataset):
    _write_annotation(tmp_dataset, "p0001")
    assert ds.main(["--data-dir", str(tmp_dataset), "validate", "--strict"]) == 1


def test_repo_dataset_is_valid():
    """The real dataset shipped in the repo must always pass its own validation."""
    ds.set_data_dir(ROOT / "data")
    products, problems = ds.collect()
    assert problems == [], problems
    assert products, "the repository should carry at least one real annotated product"
