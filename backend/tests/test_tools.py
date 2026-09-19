"""Bill Scanner / Grocery / Complaint Center tests.

The behaviours worth locking down (each is a product rule, not an implementation detail):

- A price comparison is arithmetic done in Python from stored values — never the model's verdict.
- Nothing enters the grocery tracker implicitly, and only grocery items raise expiry alerts.
- A declared duration ("best before 12 months") is not turned into a date without an anchor.
- Sample/demo values are labelled as such and never blended with real readings.
- Complaint status changes are append-only history with role enforcement.
"""
from __future__ import annotations

from datetime import date, timedelta

from backend.config import settings
from backend.services import tools_service as svc


# ---------------------------------------------------------------- AI status


def test_ai_status_is_honest_about_missing_provider(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "", raising=False)
    r = client.get("/ai/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["mode"] == "ocr_only"
    assert "on-device OCR" in body["reason"]
    # The key must never be echoed back, in any form.
    assert "key" not in body or isinstance(body.get("key"), type(None))


def test_status_endpoint_reports_vision_component_but_stays_ok(client, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "", raising=False)
    body = client.get("/status").json()
    assert body["status"] == "ok"  # a vision provider is optional, never gating
    assert body["components"]["vision_provider"] is False


# ---------------------------------------------------------------- deterministic comparison


def test_compare_bill_to_mrp_units():
    over = svc.compare_bill_to_mrp(45.0, 40.0)
    assert over["status"] == "POTENTIAL_PRICE_DIFFERENCE"
    assert over["difference"] == 5.0
    assert over["difference_pct"] == 12.5
    assert "verify" in over["reason"].lower()

    under = svc.compare_bill_to_mrp(30.0, 40.0)
    assert under["status"] == "PRICE_AT_OR_BELOW_MRP"

    missing = svc.compare_bill_to_mrp(None, 40.0)
    assert missing["status"] == "INSUFFICIENT_DATA"

    broken = svc.compare_bill_to_mrp(10.0, 0.0)
    assert broken["status"] == "INSUFFICIENT_DATA"


def test_parse_money_handles_printed_forms():
    assert svc.parse_money("₹ 200.00") == 200.0
    assert svc.parse_money("Rs.120") == 120.0
    assert svc.parse_money("MRP 45") == 45.0
    assert svc.parse_money("no digits here") is None


# ---------------------------------------------------------------- bills API


def test_manual_bill_compares_and_flags(client, auth_headers):
    r = client.post(
        "/bills",
        headers=auth_headers,
        json={"product_name": "Test Biscuits", "billed_price": "45", "mrp": "40", "store_name": "Shop"},
    )
    assert r.status_code == 200, r.text
    bill = r.json()["bill"]
    assert bill["comparison_status"] == "POTENTIAL_PRICE_DIFFERENCE"
    assert bill["price_difference"] == 5.0
    assert bill["extraction_source"] == "manual"

    # Correcting the MRP re-runs the comparison deterministically.
    r = client.patch(f"/bills/{bill['id']}", headers=auth_headers, json={"mrp": "50"})
    assert r.json()["bill"]["comparison_status"] == "PRICE_AT_OR_BELOW_MRP"


def test_demo_bill_is_labelled_and_never_blended(client, auth_headers):
    r = client.post("/bills", headers=auth_headers, json={"source": "demo", "product_name": "Sample", "billed_price": 10, "mrp": 8})
    bill = r.json()["bill"]
    assert bill["extraction_source"] == "demo"
    assert "NOT a reading" in bill["extraction_detail"]


def test_bill_scan_without_provider_stores_evidence_and_says_why(client, auth_headers, _label_png, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "", raising=False)
    r = client.post(
        "/bills/scan",
        headers=auth_headers,
        files={"file": ("bill.png", _label_png, "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["readings"] == {}  # nothing invented
    assert body["bill"]["stored_filename"]  # evidence still preserved
    assert body["ai"]["enabled"] is False
    assert "No automated reading" in body["message"]
    # The stored image is protected like every other evidence file. The login above also set an
    # HttpOnly session cookie on this client, so clear the cookie jar to test the anonymous case.
    client.cookies.clear()
    assert client.get(f"/files/bills/{body['bill']['stored_filename']}").status_code == 401


def test_bill_scan_rejects_non_image(client, auth_headers):
    r = client.post("/bills/scan", headers=auth_headers, files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 422


# ---------------------------------------------------------------- grocery


def test_grocery_is_explicit_and_only_grocery_items_raise_alerts(client, auth_headers):
    # Scanning/inspecting never adds a grocery row: the count only moves on an explicit add.
    before = client.get("/grocery", headers=auth_headers).json()["total"]

    soon = (date.today() + timedelta(days=3)).isoformat()
    r = client.post(
        "/grocery",
        headers=auth_headers,
        json={"product_name": "Curd 400 g", "expiry_date": soon, "mrp": "₹ 30"},
    )
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["alert"] == "EXPIRING_SOON"
    assert item["expiry_basis"] == "PRINTED_ON_PACKAGE"

    body = client.get("/grocery", headers=auth_headers).json()
    assert body["total"] == before + 1
    assert any(a["id"] == item["id"] for a in body["alerts"])


def test_grocery_duration_without_anchor_is_not_converted_into_a_date(client, auth_headers):
    r = client.post(
        "/grocery",
        headers=auth_headers,
        json={"product_name": "Malt Drink 500 g", "best_before_text": "Best before 12 months"},
    )
    body = r.json()
    assert body["item"]["expiry_date"] == ""  # no invention
    assert body["item"]["expiry_basis"] == "DURATION_ONLY_NO_ANCHOR"
    assert body["item"]["alert"] == "NO_EXPIRY_DATA"
    assert "no manufacturing" in body["expiry_note"].lower()


def test_grocery_duration_anchored_to_mfg_date_is_computed_and_flagged_provisional(client, auth_headers):
    r = client.post(
        "/grocery",
        headers=auth_headers,
        json={"product_name": "Malt Drink 500 g", "best_before_text": "Best before 1 month", "mfg_date": "2026-01-01"},
    )
    item = r.json()["item"]
    assert item["expiry_date"] == "2026-01-31"
    assert item["expiry_basis"] == "DURATION_FROM_MANUFACTURING_DATE"


def test_grocery_requires_a_product_name(client, auth_headers):
    assert client.post("/grocery", headers=auth_headers, json={"quantity": "500 g"}).status_code == 422


def test_grocery_owner_can_remove(client, auth_headers):
    item = client.post("/grocery", headers=auth_headers, json={"product_name": "Tea 250 g"}).json()["item"]
    assert client.delete(f"/grocery/{item['id']}", headers=auth_headers).status_code == 200
    remaining = client.get("/grocery", headers=auth_headers).json()["items"]
    assert all(row["id"] != item["id"] for row in remaining)


def test_grocery_expiry_grouping():
    assert svc.grocery_alert("")["alert"] == "NO_EXPIRY_DATA"
    expired = svc.grocery_alert((date.today() - timedelta(days=1)).isoformat())
    assert expired["alert"] == "EXPIRED" and expired["days_remaining"] == -1
    fresh = svc.grocery_alert((date.today() + timedelta(days=60)).isoformat())
    assert fresh["alert"] == "FRESH"


# ---------------------------------------------------------------- complaints


def test_complaint_lifecycle_is_append_only_and_role_gated(client, auth_headers, viewer_headers, admin_headers):
    r = client.post(
        "/complaints",
        headers=viewer_headers,
        json={"product_name": "Biscuits", "issue": "Billed above the printed MRP at the store."},
    )
    assert r.status_code == 200, r.text
    complaint = r.json()["complaint"]
    assert complaint["status"] == "SUBMITTED"
    assert len(complaint["timeline"]) == 1

    # A consumer/viewer may not move a complaint along.
    assert client.post(f"/complaints/{complaint['id']}/advance", headers=viewer_headers, json={"status": "UNDER_REVIEW"}).status_code == 403

    r = client.post(
        f"/complaints/{complaint['id']}/advance",
        headers=auth_headers,
        json={"status": "UNDER_REVIEW", "note": "Assigned to inspector."},
    )
    assert r.status_code == 200
    updated = r.json()["complaint"]
    assert updated["status"] == "UNDER_REVIEW"
    assert [t["status"] for t in updated["timeline"]] == ["SUBMITTED", "UNDER_REVIEW"]

    r = client.post(
        f"/complaints/{complaint['id']}/advance",
        headers=admin_headers,
        json={"status": "RESOLVED", "note": "Store corrected the price."},
    )
    assert r.json()["complaint"]["status"] == "RESOLVED"
    assert r.json()["complaint"]["resolution"] == "Store corrected the price."

    listed = client.get("/complaints", headers=viewer_headers).json()
    assert listed["by_status"].get("RESOLVED") == 1


def test_complaint_rejects_an_empty_issue(client, auth_headers):
    assert client.post("/complaints", headers=auth_headers, json={"issue": "short"}).status_code == 422


def test_complaint_rejects_unknown_status(client, auth_headers):
    cid = client.post("/complaints", headers=auth_headers, json={"issue": "A description long enough."}).json()["complaint"]["id"]
    assert client.post(f"/complaints/{cid}/advance", headers=auth_headers, json={"status": "NONSENSE"}).status_code == 422


# ---------------------------------------------------------------- dashboard wiring


def test_dashboard_counts_consumer_tools_and_never_fakes_them(client, auth_headers, viewer_headers):
    before = client.get("/dashboard/stats", headers=viewer_headers).json()
    client.post("/bills", headers=auth_headers, json={"product_name": "B", "billed_price": 45, "mrp": 40})
    client.post("/grocery", headers=auth_headers, json={"product_name": "G", "expiry_date": (date.today() - timedelta(days=2)).isoformat()})
    client.post("/complaints", headers=auth_headers, json={"product_name": "C", "issue": "Something worth reviewing here."})

    after = client.get("/dashboard/stats", headers=viewer_headers).json()
    assert after["bills"] == before["bills"] + 1
    assert after["grocery_items"] == before["grocery_items"] + 1
    assert after["grocery_alert_count"] == before["grocery_alert_count"] + 1
    assert after["complaints"] == before["complaints"] + 1
    assert after["open_complaints"] == before["open_complaints"] + 1
    assert len(after["potential_price_differences"]) == len(before["potential_price_differences"]) + 1
    assert any(g["alert"] == "EXPIRED" for g in after["grocery_alerts"])


def test_tools_require_authentication(client):
    assert client.get("/bills").status_code == 401
    assert client.get("/grocery").status_code == 401
    assert client.get("/complaints").status_code == 401
    assert client.get("/ai/status").status_code == 401
