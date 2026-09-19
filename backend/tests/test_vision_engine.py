"""On-device vision engine: the observations it makes, and the ones it must never make.

The engine's contract is narrow on purpose. It reports layout and readability FACTS measured from
pixels, in original-image coordinates, deterministically — and it never emits a declaration value,
a compliance verdict, or a millimetre measurement it cannot support.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from backend.ocr.base import OcrLine
from backend.vision import (
    ENGINE_LABEL,
    KIND_LEGIBILITY,
    KIND_PANEL,
    KIND_SYMBOL,
    KIND_TEXT_BLOCK,
    STATUS_COMPLETED,
    STATUS_COMPLETED_WITH_PROVIDER,
    STATUS_FAILED,
    STATUS_PROVIDER_FALLBACK,
    STATUS_PROVIDER_NOT_CONFIGURED,
    analyse_bytes,
    available,
    bbox_str,
    fact_rows,
    hero_note_for,
    panel_facts,
    parse_bbox,
    prepare_image,
    finish_image,
    status_for,
    summarise,
)


def _synthetic_label(width: int = 900, height: int = 600) -> bytes:
    """A label-like image: a large wordmark band, a denser text area, and a compact symbol."""
    img = np.full((height, width, 3), 255, np.uint8)
    # wordmark band: tall, high-contrast block near the top
    cv2.rectangle(img, (60, 40), (840, 140), (0, 0, 0), -1)
    # body text: several thin lines
    for i in range(6):
        y = 220 + i * 34
        cv2.rectangle(img, (80, y), (820, y + 18), (0, 0, 0), -1)
    # compact graphic mark: a filled circle, small and non-textual
    cv2.circle(img, (150, 520), 26, (0, 0, 0), -1)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _lines_for(band_box=(60, 40, 840, 140)):
    x1, y1, x2, y2 = band_box
    return [
        OcrLine(text="MEDIMIX", confidence=0.94, bbox=band_box, engine="rapidocr", variant="original"),
        OcrLine(text="AYURVEDIC SOAP", confidence=0.81, bbox=(x1, y2 + 20, x2 // 2, y2 + 58), engine="rapidocr", variant="original"),
        OcrLine(text="Net Quantity 750 g", confidence=0.72, bbox=(80, 340, 500, 366), engine="rapidocr", variant="original"),
    ]


def test_engine_is_available_in_this_environment():
    assert available(), "the vision engine must be usable wherever the OCR stack is installed"


def test_regions_are_measured_in_original_pixel_coordinates():
    data = _synthetic_label()
    reading, prep = analyse_bytes(data, _lines_for(), filename="label.png")

    assert reading.error == ""
    assert (prep.width, prep.height) == (900, 600)
    kinds = {r.kind for r in reading.regions}
    assert KIND_TEXT_BLOCK in kinds and KIND_LEGIBILITY in kinds

    for region in reading.regions:
        if region.bbox is None:
            continue
        x1, y1, x2, y2 = region.bbox
        assert 0 <= x1 < x2 <= prep.width, f"bbox {region.bbox} is not inside the ORIGINAL image"
        assert 0 <= y1 < y2 <= prep.height

    # The hero block is the tall wordmark band, chosen by measured prominence, not by position.
    assert reading.hero_bbox is not None
    hx1, hy1, _hx2, _hy2 = reading.hero_bbox
    assert hx1 <= 70 and hy1 <= 50, "the tallest, most contrasted printed block must win"


def test_readability_is_measured_and_kept_separate_from_legal_font_size():
    reading, _ = analyse_bytes(_synthetic_label(), _lines_for())
    legibility = next(r for r in reading.regions if r.kind == KIND_LEGIBILITY)
    assert legibility.bbox is None, "readability is an image-level measurement, not a region"
    assert reading.legibility.startswith("VISUALLY_")
    assert "Rule 7" in legibility.note
    assert "millimetre" in legibility.note


def test_analysis_is_deterministic():
    data = _synthetic_label()
    first, _ = analyse_bytes(data, _lines_for())
    second, _ = analyse_bytes(data, _lines_for())
    assert [r.label for r in first.regions] == [r.label for r in second.regions]
    assert [r.bbox for r in first.regions] == [r.bbox for r in second.regions]
    assert first.hero_bbox == second.hero_bbox


def test_two_phase_split_gives_the_same_result_as_one_shot(tmp_path):
    """The pipeline runs the pixel phase concurrently with OCR; that split must change nothing."""
    data = _synthetic_label()
    path = tmp_path / "label.png"
    path.write_bytes(data)
    prep = prepare_image(path, image_id=7, filename="label.png")
    staged = finish_image(prep, _lines_for())
    one_shot, _ = analyse_bytes(data, _lines_for())
    assert [r.bbox for r in staged.regions] == [r.bbox for r in one_shot.regions]
    assert staged.hero_bbox == one_shot.hero_bbox


def test_missing_file_fails_honestly(tmp_path):
    prep = prepare_image(tmp_path / "does-not-exist.png")
    reading = finish_image(prep)
    assert reading.error and "could not be read" in reading.error.lower()
    assert reading.regions == []


def test_a_non_image_upload_is_rejected_without_invention():
    reading, prep = analyse_bytes(b"not an image at all", [])
    assert reading.error
    assert "decoded" in reading.error.lower()
    assert prep.width == 0


def test_fact_rows_and_panel_facts_carry_the_measurements():
    reading, _ = analyse_bytes(_synthetic_label(), _lines_for())
    rows = fact_rows(type("R", (), {"engine": ENGINE_LABEL, "images": [reading]})())
    assert rows and all(row["engine"] == ENGINE_LABEL for row in rows)
    assert any(row["kind"] == KIND_PANEL for row in rows)
    facts = panel_facts(type("R", (), {"engine": ENGINE_LABEL, "images": [reading]})())
    only = facts[reading.image_id]
    assert only["legibility"] == reading.legibility
    assert only["hero_bbox"] == bbox_str(reading.hero_bbox)


def test_hero_note_only_applies_inside_the_prominent_block():
    reading, _ = analyse_bytes(_synthetic_label(), _lines_for())
    result = type("R", (), {"engine": ENGINE_LABEL, "images": [reading]})()
    inside = hero_note_for(reading.image_id, reading.hero_bbox, result)
    assert "most prominent printed block" in inside
    outside = hero_note_for(reading.image_id, (10, 580, 40, 596), result)
    assert outside == ""
    assert hero_note_for(None, reading.hero_bbox, result) == ""


def test_bbox_helpers_round_trip_and_reject_nonsense():
    assert parse_bbox("10,20,30,40") == (10, 20, 30, 40)
    assert parse_bbox("30,40,10,20") is None
    assert parse_bbox("") is None
    assert bbox_str((1, 2, 3, 4)) == "1,2,3,4"
    assert bbox_str(None) == ""


@pytest.mark.parametrize(
    ("provider_configured", "provider_used", "provider_error", "expected"),
    [
        (True, True, "", STATUS_COMPLETED_WITH_PROVIDER),
        (True, False, "", STATUS_COMPLETED),
        (True, False, "provider timed out", STATUS_PROVIDER_FALLBACK),
        (False, False, "", STATUS_PROVIDER_NOT_CONFIGURED),
    ],
)
def test_status_vocabulary_distinguishes_every_perception_outcome(
    provider_configured, provider_used, provider_error, expected
):
    reading, _ = analyse_bytes(_synthetic_label(), _lines_for())
    result = type("R", (), {"engine": ENGINE_LABEL, "images": [reading], "error": "", "ok": True})()
    assert status_for(result, provider_configured, provider_used, provider_error) == expected


def test_status_is_failed_when_the_engine_produced_nothing():
    result = type("R", (), {"engine": ENGINE_LABEL, "images": [], "error": "OpenCV missing", "ok": False})()
    assert status_for(result, True, False, "") == STATUS_FAILED


def test_summary_states_what_was_done_and_never_claims_a_value():
    reading, _ = analyse_bytes(_synthetic_label(), _lines_for())
    result = type("R", (), {"engine": ENGINE_LABEL, "images": [reading], "error": "", "ok": True,
                            "region_count": len(reading.regions)})()
    text = summarise(result, elapsed_ms=420)
    assert ENGINE_LABEL in text
    assert "420 ms" in text
    assert "region" in text
    # It reports observations — it must not contain any declaration value language.
    assert "MRP" not in text and "compliant" not in text.lower()


def test_a_symbol_region_is_only_reported_when_the_component_is_compact_and_non_textual():
    reading, _ = analyse_bytes(_synthetic_label(), _lines_for())
    symbols = [r for r in reading.regions if r.kind == KIND_SYMBOL]
    assert symbols, "the synthetic circle is a compact non-textual component"
    for symbol in symbols:
        x1, y1, x2, y2 = symbol.bbox
        # a text line is 900 px wide; a graphic mark is compact
        assert (x2 - x1) < 200 and (y2 - y1) < 200
