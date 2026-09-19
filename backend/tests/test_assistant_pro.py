"""PRO: the in-application assistant.

What this locks: PRO identifies itself consistently, greets the actual signed-in person with their
role, signs off briefly, answers about the record the user has open from that record's own stored
analysis and evidence, and NEVER describes a record outside the caller's scope.
"""
from __future__ import annotations

import io

from PIL import Image

from backend.models.product_scan import PENDING_MESSAGE


# ----------------------------------------------------------------- helpers


def _make_user(username: str, password: str, role: str, *, full_name: str, organization_id: int | None = None) -> int:
    from backend.database import SessionLocal
    from backend.models import User
    from backend.security import hash_password

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            user = User(
                username=username,
                full_name=full_name,
                role=role,
                password_hash=hash_password(password),
                organization_id=organization_id,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user.id
    finally:
        db.close()


def _login(client, username: str, password: str) -> dict:
    r = client.post("/auth/login", json={"username": username, "password": password, "include_token": True})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _processed_inspection(client, headers) -> int:
    iid = client.post("/inspections", headers=headers).json()["id"]
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, "PNG")
    assert (
        client.post(
            f"/inspections/{iid}/images",
            headers=headers,
            data={"role": "BACK"},
            files={"file": ("label.png", buf.getvalue(), "image/png")},
        ).status_code
        == 200
    )
    assert client.post(f"/inspections/{iid}/process", headers=headers).status_code == 200
    return iid


def _scan_id_for(client, headers, iid: int) -> int:
    listing = client.get("/repository/scans?page_size=200", headers=headers).json()
    return next(s["id"] for s in listing["items"] if s["inspection_id"] == iid)


def _force_pending(iid: int, *, score: float = 42.0, coverage: float = 55.0) -> None:
    """Make the record's automated review unambiguously pending-finalization."""
    from backend.database import SessionLocal
    from backend.models import ProductScan

    db = SessionLocal()
    try:
        scan = db.query(ProductScan).filter(ProductScan.inspection_id == iid).first()
        scan.compliance_score = score
        scan.coverage_score = coverage
        scan.ai_verdict = "NEEDS_MANUAL_REVIEW"
        scan.review_status = "PENDING_FINALIZATION"
        scan.official_decision = None
        db.commit()
    finally:
        db.close()


def _add_evidence(iid: int, field_name: str, value: str, raw: str) -> int:
    """Attach one retained evidence row (what the pipeline stores for a detected declaration)."""
    from backend.database import SessionLocal
    from backend.models import Evidence, InspectionImage

    db = SessionLocal()
    try:
        image = db.query(InspectionImage).filter(InspectionImage.inspection_id == iid).first()
        ev = Evidence(
            inspection_id=iid,
            kind="crop",
            field_name=field_name,
            related_type="field",
            image_id=image.id if image else None,
            bbox="(10, 10, 120, 40)",
            stored_filename="crop-test.png",
            raw_text=raw,
            normalized_value=value,
            confidence=0.94,
            extraction_method="rapidocr + original",
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)
        return ev.id
    finally:
        db.close()


# ----------------------------------------------------------------- identity + sign-off


def test_welcome_names_pro_and_greets_the_actual_user_with_their_role(client, admin_headers):
    body = client.get("/assistant/welcome", headers=admin_headers).json()
    assert body["name"] == "PRO"
    assert "admin" in body["greeting"].lower()          # the real signed-in account name
    assert "Administrator" in body["greeting"]          # and the role label
    assert body["closing"] == "Have a good day, Admin."
    assert "PRO" in body["note"]


def test_welcome_is_role_aware_for_an_inspector(client, auth_headers):
    body = client.get("/assistant/welcome", headers=auth_headers).json()
    assert body["closing"] == "Have a good day, Inspector."
    assert "Inspector" in body["greeting"]


def test_every_chat_reply_carries_pros_identity_and_a_short_signoff(client, auth_headers):
    body = client.post("/assistant/chat", headers=auth_headers, json={"message": "How do I scan a product?"}).json()
    assert body["name"] == "PRO"
    assert body["closing"] == "Have a good day, Inspector."
    assert body["answer"] and len(body["answer"]) < 600  # concise by contract


# ----------------------------------------------------------------- record context


def test_pro_answers_about_the_record_the_user_has_open(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    _force_pending(iid)

    reply = client.post(
        "/assistant/chat",
        headers=auth_headers,
        json={
            "message": "why does this need finalization?",
            "context": {"path": f"/inspections/{iid}", "inspection_id": iid},
        },
    ).json()

    assert reply["intent"].startswith("context_")
    assert reply["link"] == f"/inspections/{iid}"
    assert "finalization" in reply["answer"].lower() or "finaliz" in reply["answer"].lower()
    assert "42" in reply["answer"] and "85" in reply["answer"]     # its own score and the threshold
    assert any(PENDING_MESSAGE in p or "pending" in p.lower() for p in reply["points"])
    assert reply["closing"] == "Have a good day, Inspector."


def test_pro_explains_the_score_and_coverage_of_the_open_record(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    _force_pending(iid, score=77.5, coverage=61.0)

    reply = client.post(
        "/assistant/chat",
        headers=auth_headers,
        json={"message": "what is the compliance score?", "context": {"inspection_id": iid}},
    ).json()
    assert "77.5" in reply["answer"] and "61" in reply["answer"]
    assert "preliminary" in reply["answer"].lower()


def test_pro_points_to_the_exact_evidence_for_a_named_declaration(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    _add_evidence(iid, "mrp", "₹220.00", "MRP Rs.220.00 (Incl. of all taxes)")

    reply = client.post(
        "/assistant/chat",
        headers=auth_headers,
        json={"message": "where did the mrp come from?", "context": {"inspection_id": iid}},
    ).json()
    assert reply["intent"] == "context_evidence"
    assert "₹220.00" in reply["answer"]
    assert "94%" in reply["answer"]                       # the stored confidence
    assert any("MRP Rs.220.00" in p for p in reply["points"])  # the raw OCR text is quoted
    assert any(f"/inspections/{iid}" in p for p in reply["points"])


def test_pro_reports_nothing_rather_than_guessing_when_no_evidence_is_retained(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    reply = client.post(
        "/assistant/chat",
        headers=auth_headers,
        json={"message": "show me the evidence for batch lot", "context": {"inspection_id": iid}},
    ).json()
    assert "no retained source region" in reply["answer"].lower()
    joined = " ".join(reply["points"]).lower()
    assert "never means" in joined or "not prove" in joined


def test_pro_explains_a_reprocessing_run_from_the_revision_record(client, auth_headers):
    iid = _processed_inspection(client, auth_headers)
    assert (
        client.post(
            f"/reprocess/inspections/{iid}",
            headers=auth_headers,
            json={"reason": "engine upgrade for the demo"},
        ).status_code
        == 200
    )
    reply = client.post(
        "/assistant/chat",
        headers=auth_headers,
        json={"message": "what changed when this was reprocessed?", "context": {"inspection_id": iid}},
    ).json()
    assert reply["intent"] in ("context_reprocess", "context_overview")
    assert "engine" in reply["answer"].lower() or "corrected" in reply["answer"].lower()


# ----------------------------------------------------------------- scope safety


def test_pro_never_describes_a_record_outside_the_callers_scope(client, admin_headers):
    """A regulated entity asking about someone else's inspection is refused, not served."""
    from types import SimpleNamespace

    from backend.authz import scope_org_id

    _make_user("entity_a", "entity123", "REGULATED_ENTITY", full_name="Entity A", organization_id=900)
    headers = _login(client, "entity_a", "entity123")

    # an inspection owned by ANOTHER organization
    iid = _processed_inspection(client, admin_headers)
    from backend.database import SessionLocal
    from backend.models import Inspection

    db = SessionLocal()
    try:
        db.query(Inspection).filter(Inspection.id == iid).update({"organization_id": 901})
        db.commit()
        number = db.query(Inspection).filter(Inspection.id == iid).first().inspection_number
    finally:
        db.close()
    assert scope_org_id(SimpleNamespace(role="REGULATED_ENTITY", organization_id=900)) == 900

    reply = client.post(
        "/assistant/chat",
        headers=headers,
        json={"message": "why is this pending?", "context": {"inspection_id": iid}},
    ).json()
    assert reply["intent"] == "context_out_of_scope"
    assert number not in reply["answer"]
    assert "scope" in reply["answer"].lower()


# ----------------------------------------------------------------- unchanged general behaviour


def test_a_record_open_does_not_capture_general_explanations(client, auth_headers):
    """"What does the score mean?" is about the MODEL even when a record is on screen."""
    iid = _processed_inspection(client, auth_headers)
    reply = client.post(
        "/assistant/chat",
        headers=auth_headers,
        json={"message": "what does the compliance score mean?", "context": {"inspection_id": iid}},
    ).json()
    assert not reply["intent"].startswith("context_score")
    assert reply["intent"] != "unmatched"


def test_general_workflow_questions_still_answer_as_topics(client, auth_headers):
    reply = client.post(
        "/assistant/chat",
        headers=auth_headers,
        json={"message": "how do I finalize a pending decision?"},
    ).json()
    assert reply["intent"] != "unmatched"
    assert reply["name"] == "PRO"
    assert reply["closing"] == "Have a good day, Inspector."
