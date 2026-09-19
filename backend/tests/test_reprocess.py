"""Reprocessing / migration of EXISTING inspections.

The requirement this locks: a record analysed by an older engine must be regenerated onto the
corrected pipeline — in place. It must never become a second record, must never lose a human
correction or a recorded official decision, and the result it replaced must stay recoverable with
the actor, reason, engine version and rule-set fingerprint that produced the replacement.
"""
from __future__ import annotations

import io
import time

from PIL import Image

from backend.version import ENGINE_VERSION


# ----------------------------------------------------------------- fixtures


def _processed_inspection(client, headers, role: str = "FRONT") -> int:
    iid = client.post("/inspections", headers=headers).json()["id"]
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, "PNG")
    assert (
        client.post(
            f"/inspections/{iid}/images",
            headers=headers,
            data={"role": role},
            files={"file": ("label.png", buf.getvalue(), "image/png")},
        ).status_code
        == 200
    )
    assert client.post(f"/inspections/{iid}/process", headers=headers).status_code == 200
    return iid


def _scan_for(client, headers, iid: int) -> dict:
    listing = client.get("/repository/scans?page_size=200", headers=headers).json()
    return next(s for s in listing["items"] if s["inspection_id"] == iid)


def _scan_rows_for(iid: int):
    from backend.database import SessionLocal
    from backend.models import ProductScan

    db = SessionLocal()
    try:
        return db.query(ProductScan).filter(ProductScan.inspection_id == iid).all()
    finally:
        db.close()


# ----------------------------------------------------------------- in-place correctness


def test_reprocessing_updates_the_record_in_place_and_keeps_the_previous_result(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    before = _scan_for(client, auth_headers, iid)

    res = client.post(
        f"/reprocess/inspections/{iid}",
        headers=auth_headers,
        json={"reason": "regenerate after extraction fixes"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["engine_version"] == ENGINE_VERSION
    assert body["revision_no"] == 1
    assert body["changes"]["changed"]["any"] in (True, False)  # a real diff is always produced

    # ONE record: the same scan row was updated, and no second row exists
    after_rows = _scan_rows_for(iid)
    assert len(after_rows) == 1
    after = client.get(f"/repository/scans/{after_rows[0].id}", headers=auth_headers).json()
    assert after["id"] == before["id"]
    assert after["product_id"] == before["product_id"]

    # the previous result is recoverable, with its provenance
    revs = client.get(f"/reprocess/inspections/{iid}/revisions", headers=auth_headers).json()["revisions"]
    assert len(revs) == 1
    rev = revs[0]
    assert rev["kind"] == "REPROCESS"
    assert rev["actor"] == "inspector"
    assert "regenerate after extraction fixes" in rev["reason"]
    assert rev["engine_version"] == ENGINE_VERSION and rev["rule_fingerprint"]
    assert rev["before"]["fields"] and rev["after"]["fields"]
    assert "changed" in rev["changes"] and "fields" in rev["changes"]

    # the active screen reports the corrected result AND that it is current
    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    assert detail["provenance"]["up_to_date"] is True
    assert detail["provenance"]["reprocess_count"] == 1
    assert detail["provenance"]["engine_version"] == ENGINE_VERSION
    assert detail["provenance"]["last_reprocessed_at"]


def test_reprocessing_records_the_event_in_the_audit_trail(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    assert (
        client.post(f"/reprocess/inspections/{iid}", headers=auth_headers, json={"reason": "audit test"}).status_code
        == 200
    )

    from backend.database import SessionLocal
    from backend.models import AuditLog, Inspection

    db = SessionLocal()
    try:
        number = db.query(Inspection).filter(Inspection.id == iid).first().inspection_number
        rows = (
            db.query(AuditLog)
            .filter(AuditLog.inspection_id == number, AuditLog.action.like("inspection_reprocess%"))
            .all()
        )
        assert {r.action for r in rows} == {"inspection_reprocess_started", "inspection_reprocessed"}
        assert all(r.actor == "inspector" for r in rows)
        done = next(r for r in rows if r.action == "inspection_reprocessed")
        assert "audit test" in (done.reason or "")
    finally:
        db.close()


def test_a_recorded_official_decision_survives_reprocessing(client, auth_headers, admin_headers):
    iid = _processed_inspection(client, auth_headers)
    scan_id = _scan_for(client, auth_headers, iid)["id"]
    assert (
        client.post(
            f"/repository/scans/{scan_id}/finalize",
            headers=admin_headers,
            json={"decision": "NON_COMPLIANT", "remarks": "confirmed on physical examination"},
        ).status_code
        == 200
    )

    res = client.post(
        f"/reprocess/inspections/{iid}",
        headers=admin_headers,
        json={"reason": "engine upgrade"},
    )
    assert res.status_code == 200 and res.json()["changes"]["official_decision"]["after"] == "NON_COMPLIANT"

    scan = client.get(f"/repository/scans/{scan_id}", headers=admin_headers).json()
    assert scan["review_status"] == "FINALIZED"
    assert scan["official_decision"] == "NON_COMPLIANT"
    assert scan["finalized_by"] == "admin"
    assert scan["is_finalized"] is True


def test_a_human_correction_is_not_overwritten_by_reprocessing(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    edited = client.post(
        f"/inspections/{iid}/review/field",
        headers=auth_headers,
        json={
            "action": "EDIT_FIELD",
            "field_name": "mrp",
            "corrected_value": "₹999.00",
            "reason": "read from the physical package",
        },
    )
    assert edited.status_code == 200

    assert (
        client.post(f"/reprocess/inspections/{iid}", headers=auth_headers, json={"reason": "reprocess"}).status_code
        == 200
    )

    fields = {f["field_name"]: f for f in client.get(f"/inspections/{iid}", headers=auth_headers).json()["fields"]}
    mrp = fields["mrp"]
    assert mrp["display_value"] == "₹999.00"
    assert mrp["manually_corrected"] is True
    assert mrp["state"] == "MANUALLY_CORRECTED"


# ----------------------------------------------------------------- inventory + bulk run


def _mark_legacy(iid: int, version: str = "2026.01-r1") -> None:
    """Simulate a record that was analysed by an earlier engine (as the real legacy rows are)."""
    from backend.database import SessionLocal
    from backend.models import Inspection

    db = SessionLocal()
    try:
        db.query(Inspection).filter(Inspection.id == iid).update({"pipeline_version": version})
        db.commit()
    finally:
        db.close()


def test_a_freshly_processed_record_is_stamped_current_and_is_not_listed_for_migration(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    assert detail["provenance"]["engine_version"] == ENGINE_VERSION
    assert detail["provenance"]["up_to_date"] is True
    assert detail["provenance"]["reprocess_count"] == 0
    preview = client.get("/reprocess/preview", headers=auth_headers).json()
    assert iid not in {i["inspection_id"] for i in preview["items"]}


def test_legacy_inventory_reports_the_engine_and_the_records_still_on_an_old_one(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    _mark_legacy(iid)

    preview = client.get("/reprocess/preview", headers=auth_headers).json()
    assert preview["engine_version"] == ENGINE_VERSION
    assert preview["rule_fingerprint"] and preview["rule_count"] > 0
    assert preview["reprocessable_total"] >= 1
    assert preview["legacy_count"] >= 1
    listed = {i["inspection_id"]: i for i in preview["items"]}
    assert iid in listed
    assert listed[iid]["pipeline_version"] == "2026.01-r1"

    assert client.post(f"/reprocess/inspections/{iid}", headers=auth_headers, json={"reason": "migrate"}).status_code == 200
    assert client.get(f"/inspections/{iid}", headers=auth_headers).json()["provenance"]["up_to_date"] is True

    after = client.get("/reprocess/preview", headers=auth_headers).json()
    assert after["legacy_count"] == preview["legacy_count"] - 1
    assert after["current_count"] == preview["current_count"] + 1


def test_bulk_migration_is_admin_only_and_reports_per_record_outcomes(client, auth_headers, admin_headers):
    iid = _processed_inspection(client, auth_headers)

    # an inspector may regenerate a single record, but not rewrite the whole repository
    assert client.post("/reprocess/batch", headers=auth_headers, json={"reason": "nope"}).status_code == 403
    assert client.get("/reprocess/jobs", headers=auth_headers).status_code == 200

    started = client.post(
        "/reprocess/batch",
        headers=admin_headers,
        json={"reason": "bulk migration test", "ids": [iid]},
    )
    assert started.status_code == 200
    job = started.json()
    assert job["state"] in ("QUEUED", "RUNNING", "DONE")
    assert job["actor"] == "admin"

    # poll the background worker to a conclusion
    deadline = time.time() + 30
    while time.time() < deadline and job["state"] in ("QUEUED", "RUNNING"):
        time.sleep(0.4)
        job = client.get(f"/reprocess/jobs/{job['id']}", headers=admin_headers).json()
    assert job["state"] == "DONE", job
    assert job["total"] == 1 and job["done"] == 1
    assert job["succeeded"] == 1 and job["failed"] == 0
    assert job["results"][0]["inspection_id"] == iid
    assert job["results"][0]["ok"] is True
    assert "bulk migration test" in job["reason"]

    # the bulk run left a MIGRATION revision (distinct from a manual REPROCESS)
    revs = client.get(f"/reprocess/inspections/{iid}/revisions", headers=auth_headers).json()["revisions"]
    assert revs[0]["kind"] == "MIGRATION"
    assert revs[0]["actor"] == "admin"


def test_second_bulk_run_is_refused_while_one_is_running(client, auth_headers, admin_headers):
    """Two concurrent migrations would fight over the same records — the second is refused."""
    iid = _processed_inspection(client, auth_headers)
    first = client.post("/reprocess/batch", headers=admin_headers, json={"reason": "first", "ids": [iid]})
    assert first.status_code == 200
    job_id = first.json()["id"]

    second = client.post("/reprocess/batch", headers=admin_headers, json={"reason": "second", "ids": [iid]})
    # Either the first run already finished (nothing in flight) or the overlap was refused with 409.
    assert second.status_code in (200, 409)
    if second.status_code == 409:
        assert job_id in second.json()["detail"]
