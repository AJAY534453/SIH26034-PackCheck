"""API tests: auth, roles, upload validation, workflow, export."""
from __future__ import annotations


def test_root_and_status(client):
    assert client.get("/").status_code == 200
    assert client.get("/status").json()["status"] == "ok"


def test_login_rejects_bad_password(client):
    r = client.post("/auth/login", json={"username": "inspector", "password": "wrong"})
    assert r.status_code == 401


def test_me_requires_auth(client):
    assert client.get("/auth/me").status_code == 401


def test_role_enforcement(client, viewer_headers, auth_headers):
    # viewer cannot create inspections
    assert client.post("/inspections", headers=viewer_headers).status_code == 403
    # inspector can
    assert client.post("/inspections", headers=auth_headers).status_code == 200


def test_full_workflow(client, auth_headers, _label_png):
    r = client.post("/inspections", headers=auth_headers)
    assert r.status_code == 200
    insp = r.json()
    iid = insp["id"]

    # upload image
    r = client.post(
        f"/inspections/{iid}/images",
        headers=auth_headers,
        data={"role": "BACK"},
        files={"file": ("label.png", _label_png, "image/png")},
    )
    assert r.status_code == 200, r.text

    # process (blank label -> OCR finds nothing -> review, never fake results)
    r = client.post(f"/inspections/{iid}/process", headers=auth_headers)
    assert r.status_code == 200

    r = client.get(f"/inspections/{iid}", headers=auth_headers)
    assert r.status_code == 200
    detail = r.json()
    assert detail["final_decision"] in ("COMPLIANT", "NON_COMPLIANT", "NEEDS_MANUAL_REVIEW")
    assert "summary" in detail and detail["summary"]

    # export JSON
    r = client.get(f"/inspections/{iid}/export", headers=auth_headers)
    assert r.status_code == 200
    # export CSV
    r = client.get(f"/inspections/{iid}/export?format=csv", headers=auth_headers)
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]


def test_upload_rejects_bad_extension(client, auth_headers):
    r = client.post("/inspections", headers=auth_headers)
    iid = r.json()["id"]
    r = client.post(
        f"/inspections/{iid}/images",
        headers=auth_headers,
        data={"role": "FRONT"},
        files={"file": ("malware.exe", b"MZ\x90\x00fake", "application/octet-stream")},
    )
    assert r.status_code == 422


def test_upload_rejects_corrupt_image(client, auth_headers):
    r = client.post("/inspections", headers=auth_headers)
    iid = r.json()["id"]
    r = client.post(
        f"/inspections/{iid}/images",
        headers=auth_headers,
        data={"role": "FRONT"},
        files={"file": ("broken.png", b"\x89PNG\r\n\x1a\nGARBAGE", "image/png")},
    )
    assert r.status_code == 422


def test_upload_rejects_duplicate(client, auth_headers, _label_png):
    r = client.post("/inspections", headers=auth_headers)
    iid = r.json()["id"]
    files = {"file": ("label.png", _label_png, "image/png")}
    r1 = client.post(f"/inspections/{iid}/images", headers=auth_headers, data={"role": "FRONT"}, files=files)
    assert r1.status_code == 200
    _label_png.seek(0) if hasattr(_label_png, "seek") else None
    r2 = client.post(
        f"/inspections/{iid}/images",
        headers=auth_headers,
        data={"role": "BACK"},
        files={"file": ("label2.png", _label_png, "image/png")},
    )
    assert r2.status_code == 422
    assert "duplicate" in r2.json()["detail"].lower()


def test_process_requires_image(client, auth_headers):
    r = client.post("/inspections", headers=auth_headers)
    iid = r.json()["id"]
    r = client.post(f"/inspections/{iid}/process", headers=auth_headers)
    assert r.status_code == 422


def test_review_field_edit_and_audit(client, auth_headers):
    r = client.post("/inspections", headers=auth_headers)
    iid = r.json()["id"]
    r = client.post(
        f"/inspections/{iid}/review/field",
        headers=auth_headers,
        json={"action": "ADD_FIELD", "field_name": "mrp", "corrected_value": "₹ 150", "reason": "checked physically"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "MANUALLY_CORRECTED"

    # detail shows review action recorded
    r = client.get(f"/inspections/{iid}", headers=auth_headers)
    assert any(a["action"] == "ADD_FIELD" for a in r.json()["review_actions"])


def test_final_decision_by_reviewer(client, auth_headers):
    r = client.post("/inspections", headers=auth_headers)
    iid = r.json()["id"]
    r = client.post(
        f"/inspections/{iid}/review/final",
        headers=auth_headers,
        json={"decision": "NEEDS_MANUAL_REVIEW", "reason": "awaiting physical verification"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "COMPLETED"


def test_dashboard_and_rules(client, auth_headers):
    assert client.get("/dashboard/stats", headers=auth_headers).status_code == 200
    r = client.get("/rules", headers=auth_headers)
    assert r.status_code == 200 and r.json()["total"] >= 10


def test_files_endpoint_requires_auth(client):
    assert client.get("/files/originals/whatever.png").status_code == 401


def test_files_endpoint_blocks_traversal(client, auth_headers):
    assert client.get("/files/originals/..%2F..%2Fsecret.txt", headers=auth_headers).status_code == 404


def test_audit_log_admin_only_and_append_only(client, auth_headers, admin_headers, viewer_headers):
    """The global audit trail is readable by ADMIN only and has no mutation path."""
    assert client.get("/audit", headers=auth_headers).status_code == 403
    assert client.get("/audit", headers=viewer_headers).status_code == 403

    r = client.get("/audit", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body and "actions" in body
    assert any(i["action"] == "login" for i in body["items"])

    # A mutating action is appended to the trail with actor + reason.
    iid = client.post("/inspections", headers=auth_headers).json()["id"]
    client.post(f"/inspections/{iid}/review/note", headers=auth_headers,
                json={"note": "audit trail check"})
    r = client.get("/audit?action=inspection_reviewed", headers=admin_headers)
    assert r.status_code == 200
    hits = r.json()["items"]
    assert hits and hits[0]["actor"] == "inspector"
    assert any(i["after"] == "audit trail check" for i in hits)

    # Filtering by inspection id returns only that inspection's entries.
    r = client.get("/audit?inspection=INS-9999-000001", headers=admin_headers)
    assert r.status_code == 200 and r.json()["total"] == 0

    # Append-only: no write methods are exposed for the trail.
    assert client.post("/audit", headers=admin_headers, json={}).status_code == 405
    assert client.delete("/audit/1", headers=admin_headers).status_code in (404, 405)


def test_violation_human_decision_lifecycle(client, auth_headers, viewer_headers):
    """Human CONFIRM/DISMISS/RESOLVE on a violation: reason-gated, audited, decision-changing.

    A confirmed absence is positive evidence, so the deterministic engine raises a violation;
    the reviewer's later determination must be honoured without ever silently overriding it.
    """
    iid = client.post("/inspections", headers=auth_headers).json()["id"]
    r = client.post(
        f"/inspections/{iid}/review/field",
        headers=auth_headers,
        json={"action": "CONFIRM_ABSENT", "field_name": "mrp", "reason": "physical examination: no MRP printed"},
    )
    assert r.status_code == 200
    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    mrp_violations = [v for v in detail["violations"] if v["rule_number"] == "6(1)(e)"]
    assert mrp_violations, "human-confirmed absence must produce an evidence-backed violation"
    vid = mrp_violations[0]["id"]
    assert detail["final_decision"] == "NON_COMPLIANT"

    # Dismissing requires a recorded reason (never a silent override).
    r = client.post(f"/inspections/{iid}/review/violation/{vid}", headers=auth_headers,
                    json={"action": "DISMISS", "reason": ""})
    assert r.status_code == 422
    # Unknown action is rejected.
    r = client.post(f"/inspections/{iid}/review/violation/{vid}", headers=auth_headers,
                    json={"action": "FORGIVE", "reason": "x"})
    assert r.status_code == 422
    # Read-only role may not act.
    r = client.post(f"/inspections/{iid}/review/violation/{vid}", headers=viewer_headers,
                    json={"action": "DISMISS", "reason": "x"})
    assert r.status_code == 403

    # Dismiss with a reason: recorded, audited and excluded from the automated decision.
    r = client.post(f"/inspections/{iid}/review/violation/{vid}", headers=auth_headers,
                    json={"action": "DISMISS", "reason": "MRP is present and legible on the physical label"})
    assert r.status_code == 200 and r.json()["status"] == "DISMISSED"
    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    assert detail["final_decision"] == "NEEDS_MANUAL_REVIEW"
    assert any(a["action"] == "VIOLATION_DISMISS" for a in detail["review_actions"])
    assert any(v["id"] == vid and v["status"] == "DISMISSED" for v in detail["violations"])
    assert "dismissed/resolved" in (detail["summary"] or "").lower()

    # Confirming drives non-compliance again (the human's call still governs the decision).
    r = client.post(f"/inspections/{iid}/review/violation/{vid}", headers=auth_headers,
                    json={"action": "CONFIRM", "reason": "verified missing on the physical package"})
    assert r.status_code == 200 and r.json()["status"] == "CONFIRMED"
    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    assert detail["final_decision"] == "NON_COMPLIANT"
    assert any(a["action"] == "VIOLATION_CONFIRM" for a in detail["review_actions"])


def test_violation_decision_survives_reprocess(client, auth_headers):
    """A dismissed violation is not silently reset by a later re-evaluation."""
    iid = client.post("/inspections", headers=auth_headers).json()["id"]
    client.post(f"/inspections/{iid}/review/field", headers=auth_headers,
                json={"action": "CONFIRM_ABSENT", "field_name": "mrp", "reason": "no MRP"})
    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    vid = [v for v in detail["violations"] if v["rule_number"] == "6(1)(e)"][0]["id"]
    client.post(f"/inspections/{iid}/review/violation/{vid}", headers=auth_headers,
                json={"action": "DISMISS", "reason": "present on label"})

    # A second review action re-runs the whole deterministic evaluation.
    client.post(f"/inspections/{iid}/review/field", headers=auth_headers,
                json={"action": "CONFIRM_ABSENT", "field_name": "batch_lot", "reason": "no batch printed"})
    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    mrp = [v for v in detail["violations"] if v["rule_number"] == "6(1)(e)"]
    assert mrp and mrp[0]["status"] == "DISMISSED", "dismissal must be durable across re-evaluation"


# ------------------------------------------------- review candidates carry evidence


def _declared_label_png() -> bytes:
    """A rendered label whose date is printed in the ambiguous DD/MM/YY form."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (760, 400), "white")
    d = ImageDraw.Draw(img)

    def F(size):
        try:
            return ImageFont.truetype("arial.ttf", size)
        except Exception:
            return ImageFont.load_default()

    d.text((30, 20), "KRACKJACK BISCUITS", fill="black", font=F(36))
    d.text((30, 90), "Net Wt. 500 g", fill="black", font=F(24))
    d.text((30, 140), "MRP Rs. 200", fill="black", font=F(24))
    d.text((30, 200), "MFD: 04/02/25", fill="black", font=F(24))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_review_candidate_exposes_its_source_region(client, auth_headers):
    """A value offered for review (uncertain date code) must be as inspectable as a detected
    value: an inspector cannot confirm or reject a candidate without seeing where it came from."""
    iid = client.post("/inspections", headers=auth_headers).json()["id"]
    r = client.post(
        f"/inspections/{iid}/images",
        headers=auth_headers,
        data={"role": "BACK"},
        files={"file": ("declared.png", _declared_label_png(), "image/png")},
    )
    assert r.status_code == 200, r.text
    assert client.post(f"/inspections/{iid}/process", headers=auth_headers).status_code == 200

    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    fields = {f["field_name"]: f for f in detail["fields"]}
    mfg = fields["date_manufacturing"]
    assert mfg["display_value"], "the MFD declaration must be read from the rendered label"
    assert mfg["state"] == "UNCERTAIN", "day/month order is an assumption, not a fact"

    ev = [e for e in detail["evidence"] if e["field_name"] == "date_manufacturing"]
    assert ev and ev[0]["bbox"], "a review candidate must carry its source region"
    assert "candidate" in (ev[0]["note"] or "").lower()


def test_detected_fields_never_lack_evidence(client, auth_headers):
    """No DETECTED value may be shown without a retained source region."""
    iid = client.post("/inspections", headers=auth_headers).json()["id"]
    r = client.post(
        f"/inspections/{iid}/images",
        headers=auth_headers,
        data={"role": "BACK"},
        files={"file": ("declared.png", _declared_label_png(), "image/png")},
    )
    assert r.status_code == 200, r.text
    assert client.post(f"/inspections/{iid}/process", headers=auth_headers).status_code == 200

    detail = client.get(f"/inspections/{iid}", headers=auth_headers).json()
    have = {e["field_name"] for e in detail["evidence"] if (e["bbox"] or "").strip()}
    bare = [
        f["field_name"]
        for f in detail["fields"]
        if f["state"] == "DETECTED" and f["display_value"].strip() and f["field_name"] not in have
    ]
    assert not bare, f"detected fields without evidence: {bare}"
