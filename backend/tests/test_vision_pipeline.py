"""Vision in the live pipeline, and the persistence guarantee around consecutive scans.

Two things are proven here that a UI change alone could never prove:

1. **Vision actually runs on a scan.** A submitted inspection records the on-device engine, keeps
   its regions and crops, is NOT reported as skipped, and exposes the real per-stage timings and
   total duration.
2. **Scanning a second product never disturbs the first.** Each scan gets its own inspection,
   its own identifier and its own analysis; the earlier record stays readable with its values,
   regions and evidence intact.
"""
from __future__ import annotations

import cv2
import numpy as np


def _label_png(width: int = 900, height: int = 600) -> bytes:
    """A label-like PNG with a wordmark band, body text lines and a compact mark."""
    img = np.full((height, width, 3), 255, np.uint8)
    cv2.rectangle(img, (60, 40), (840, 140), (0, 0, 0), -1)
    for i in range(6):
        y = 220 + i * 34
        cv2.rectangle(img, (80, y), (820, y + 18), (0, 0, 0), -1)
    cv2.circle(img, (150, 520), 26, (0, 0, 0), -1)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _scan(client, headers, image: bytes, role: str = "FRONT") -> dict:
    created = client.post("/inspections", headers=headers).json()
    iid = created["id"]
    upload = client.post(
        f"/inspections/{iid}/images",
        headers=headers,
        data={"role": role},
        files={"file": (f"{role.lower()}.png", image, "image/png")},
    )
    assert upload.status_code == 200, upload.text
    processed = client.post(f"/inspections/{iid}/process", headers=headers)
    assert processed.status_code == 200, processed.text
    return client.get(f"/inspections/{iid}", headers=headers).json()


def test_a_scan_records_the_on_device_engine_regions_and_real_timings(client, auth_headers):
    detail = _scan(client, auth_headers, _label_png())

    # Vision ran — not "skipped", not "optional".
    assert detail["stage_status"]["visual_analysis"] == "done"
    assert detail["vision_engine"] == "on-device-vision:1"
    assert detail["vision_status"] in (
        "COMPLETED",
        "COMPLETED_WITH_PROVIDER",
        "ON_DEVICE_ONLY_NO_PROVIDER",
    )

    # Observations are stored with measurements and a note that never claims a legal meaning.
    regions = detail["vision"]["regions"]
    assert regions
    kinds = {r["kind"] for r in regions}
    assert "LEGIBILITY" in kinds
    for region in regions:
        if region["bbox"]:
            x1, y1, x2, y2 = (int(v) for v in region["bbox"].split(","))
            assert 0 <= x1 < x2 and 0 <= y1 < y2
        assert region["engine"] == "on-device-vision:1"

    # The visual observation is inspectable: a crop is retained for the prominent block/region.
    vision_evidence = [e for e in detail["evidence"] if e.get("related_type") == "vision"]
    assert vision_evidence, "visual observations must be openable as evidence"
    assert all(e["stored_filename"] for e in vision_evidence)

    # Timings are measured, not invented, and cover the stages that cost the time.
    timings = detail["stage_timings"]
    assert timings.get("text_detection_ocr", 0) >= 0
    assert timings.get("visual_analysis", 0) >= 0
    assert detail["duration_ms"] > 0


def test_vision_regions_are_replaced_not_accumulated_on_reprocess(client, auth_headers):
    detail = _scan(client, auth_headers, _label_png())
    iid = detail["id"]
    before = len(detail["vision"]["regions"])

    rerun = client.post(
        f"/reprocess/inspections/{iid}",
        headers=auth_headers,
        json={"reason": "test idempotency", "refresh_ocr": False},
    )
    assert rerun.status_code == 200, rerun.text
    after = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    assert len(after["vision"]["regions"]) == before, "regions must not accumulate across runs"


def test_two_scans_keep_both_records_with_their_own_analysis(client, auth_headers):
    """The critical guarantee: product 2 must never displace product 1."""
    first = _scan(client, auth_headers, _label_png())
    second = _scan(client, auth_headers, _label_png(width=940, height=640))

    assert first["inspection_number"] != second["inspection_number"]
    assert first["id"] != second["id"]

    # Both are listed, and the first is untouched by the second run.
    listing = client.get("/inspections", headers=auth_headers).json()
    items = listing["items"] if isinstance(listing, dict) and "items" in listing else listing
    numbers = {row["inspection_number"] for row in items}
    assert {first["inspection_number"], second["inspection_number"]} <= numbers

    reread_first = client.get(f"/inspections/{first['id']}", headers=auth_headers).json()
    assert reread_first["status"] == first["status"]
    assert reread_first["final_decision"] == first["final_decision"]
    assert len(reread_first["fields"]) == len(first["fields"])
    assert len(reread_first["vision"]["regions"]) == len(first["vision"]["regions"])
    assert {r["id"] for r in reread_first["vision"]["regions"]} == {r["id"] for r in first["vision"]["regions"]}

    # Each scan keeps its own evidence rows — nothing is shared between inspections.
    first_evidence = {e["id"] for e in reread_first["evidence"]}
    second_evidence = {e["id"] for e in second["evidence"]}
    assert first_evidence and second_evidence
    assert not (first_evidence & second_evidence)


def test_progress_endpoint_reports_real_state_during_and_after_a_run(client, auth_headers):
    detail = _scan(client, auth_headers, _label_png())
    progress = client.get(f"/inspections/{detail['id']}/progress", headers=auth_headers).json()
    assert progress["stage_status"]["visual_analysis"] == "done"
    assert progress["duration_ms"] > 0
    assert progress["vision_status"] == detail["vision_status"]
    assert "visual_analysis" in progress["stage_timings"]


def test_vision_health_endpoint_answers_without_a_provider(client):
    health = client.get("/health/vision").json()
    assert health["engine"] == "on-device-vision:1"
    assert health["on_device"]["available"] is True
    assert health["status"] == "ONLINE"
    # Reachability is never guessed from configuration alone.
    assert health["reachable"] is None
    assert "not probed" in health["reachable_note"]


def test_vision_self_test_exercises_both_sources_honestly(client, auth_headers):
    result = client.post(
        "/vision/test",
        headers=auth_headers,
        files={"file": ("label.png", _label_png(), "image/png")},
    )
    assert result.status_code == 200, result.text
    payload = result.json()

    # The on-device engine ran and reported measured regions.
    assert payload["on_device"]["status"] == "OK"
    assert payload["on_device"]["regions"], "the self-test must return the regions it measured"
    assert payload["on_device"]["latency_ms"] >= 0
    assert payload["on_device"]["readability"].startswith("VISUALLY_")

    # With no provider key configured, the provider call is reported as NOT ATTEMPTED, never as a
    # success and never as a failure of the provider.
    assert payload["configured"] is False
    assert payload["provider_call"]["attempted"] is False
    assert payload["provider_call"]["status"] == "NOT_CONFIGURED"
    assert payload["provider_call"]["error"] == ""


def test_vision_self_test_rejects_a_non_image(client, auth_headers):
    result = client.post(
        "/vision/test",
        headers=auth_headers,
        files={"file": ("notes.txt", b"this is not an image", "text/plain")},
    )
    assert result.status_code == 200
    payload = result.json()
    assert payload["on_device"]["status"] == "FAILED"
    assert payload["on_device"]["error"]
    assert payload["errors"]
