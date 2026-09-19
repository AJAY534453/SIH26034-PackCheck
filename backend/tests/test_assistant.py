"""In-application assistant: grounding, conciseness, role awareness and the legal/guidance split."""
from __future__ import annotations

import pytest


@pytest.fixture()
def entity_headers(client):
    r = client.post("/auth/login", json={"username": "entity", "password": "entity123", "include_token": True})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _ask(client, headers, message):
    r = client.post("/assistant/chat", headers=headers, json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()


def _words(text: str) -> int:
    return len(text.split())


# ----------------------------------------------------------------- access + welcome

def test_welcome_is_role_aware_and_states_its_limits(client, auth_headers, viewer_headers):
    staff = client.get("/assistant/welcome", headers=auth_headers).json()
    assert staff["can_finalize"] is True
    assert staff["greeting"].startswith("Hello")
    assert staff["suggestions"]
    assert "never issue a legal decision" in staff["note"]

    viewer = client.get("/assistant/welcome", headers=viewer_headers).json()
    assert viewer["can_finalize"] is False, "the assistant must not promise a capability the role lacks"
    assert viewer["can_run_analysis"] is False


def test_assistant_requires_authentication(client):
    assert client.post("/assistant/chat", json={"message": "hi"}).status_code == 401
    assert client.get("/assistant/welcome").status_code == 401


def test_empty_and_overlong_questions_are_rejected(client, auth_headers):
    assert client.post("/assistant/chat", headers=auth_headers, json={"message": "   "}).status_code == 422
    assert client.post("/assistant/chat", headers=auth_headers, json={"message": "x" * 600}).status_code == 422


# ----------------------------------------------------------------- answers

def test_scanning_question_returns_a_short_workflow_with_a_link(client, auth_headers):
    reply = _ask(client, auth_headers, "How do I scan a product?")
    assert reply["intent"] == "scan_workflow"
    assert reply["kind"] == "workflow"
    assert reply["link"] == "/inspections/new"
    assert len(reply["points"]) >= 3, "a procedure is given as ordered steps"
    assert _words(reply["answer"]) < 60, "answers stay concise"
    assert reply["followups"]


def test_score_question_explains_the_weighting(client, auth_headers):
    reply = _ask(client, auth_headers, "What does the AI compliance score mean?")
    assert reply["intent"] == "ai_review"
    assert "PASS" in reply["answer"] and "UNCERTAIN" in reply["answer"] and "FAIL" in reply["answer"]


def test_threshold_question_answers_the_85_percent_rule(client, auth_headers):
    reply = _ask(client, auth_headers, "Why was this scan flagged below the threshold?")
    assert "85" in reply["answer"]
    assert "finalization" in reply["answer"].lower()


def test_finalization_question_gives_the_exact_steps(client, auth_headers):
    reply = _ask(client, auth_headers, "How do I finalize a pending decision?")
    assert reply["intent"] == "finalize_pending"
    assert "Compliance Repository" in " ".join(reply["points"])
    assert reply["link"] == "/repository"


def test_pending_status_wording_is_consistent_with_the_api(client, auth_headers):
    reply = _ask(client, auth_headers, "What does a normal user see for an unfinished case?")
    assert "pending finalization by the higher officials" in reply["answer"].lower()


def test_glossary_terms_are_explained(client, auth_headers):
    for term, fragment in (("UNCERTAIN", "could not be established"), ("DETECTED", "read"), ("MISSING", "not found")):
        reply = _ask(client, auth_headers, f"What does {term} mean?")
        assert reply["kind"] == "glossary", reply
        assert reply["intent"] == "glossary"
        assert fragment.lower() in reply["answer"].lower(), reply["answer"]


def test_legal_question_is_answered_from_the_rule_library_with_its_source(client, auth_headers):
    reply = _ask(client, auth_headers, "What does rule 6(1)(c) require?")
    assert reply["kind"] == "legal_reference"
    assert reply["intent"] == "legal_rule"
    assert "6(1)(c)" in reply["answer"]
    assert any(s["kind"] == "legal_reference" for s in reply["sources"])
    joined = " ".join(reply["points"])
    assert "authoritative" in joined, "a legal answer must point to the authoritative source"
    assert "Legal Metrology" in " ".join([reply["answer"]] + reply["points"])


def test_an_unknown_rule_reference_is_refused_not_invented(client, auth_headers):
    reply = _ask(client, auth_headers, "What does rule 99(9) say?")
    assert reply["kind"] == "legal_reference"
    assert "do not hold a rule" in reply["answer"]


def test_font_size_requirement_is_computed_from_the_rule_data(client, auth_headers):
    reply = _ask(client, auth_headers, "What is the minimum font size for a 300 cm2 panel?")
    assert reply["kind"] == "legal_reference"
    assert reply["intent"] == "font_size_requirement"
    # Table-I (weight/volume) for 100 < A < 500 cm² is 2.5 mm; moulded 4.0 mm
    assert "2.5 mm" in reply["answer"] and "4.0 mm" in reply["answer"]
    assert "Table-I" in reply["answer"]
    joined = " ".join(reply["points"])
    assert "one third" in joined, "the width requirement of Rule 7(3) is stated too"


def test_font_size_question_without_an_area_lists_the_table(client, auth_headers):
    reply = _ask(client, auth_headers, "How tall must the printed letters be?")
    assert reply["intent"] == "font_size_requirement"
    assert "mm" in reply["answer"] and "cm2" in reply["answer"]


def test_live_counts_come_from_the_users_own_scope(client, auth_headers):
    reply = _ask(client, auth_headers, "How many scans are pending finalization?")
    assert reply["intent"] == "live_count"
    assert "scan" in reply["answer"]
    assert any(ch.isdigit() for ch in reply["answer"])
    assert any("scope" in p.lower() for p in reply["points"])


def test_application_guidance_and_legal_reference_are_kept_apart(client, auth_headers):
    guidance = _ask(client, auth_headers, "How do I upload images?")
    legal = _ask(client, auth_headers, "What does rule 7 say about the principal display panel?")
    assert guidance["kind"] in ("app_guidance", "workflow")
    assert legal["kind"] == "legal_reference"


def test_greeting_is_short_and_offers_suggestions(client, auth_headers):
    reply = _ask(client, auth_headers, "hello")
    assert reply["intent"] == "greeting"
    assert _words(reply["answer"]) < 25
    assert len(reply["followups"]) >= 3


def test_unmatched_question_is_honest_and_suggests_topics(client, auth_headers):
    reply = _ask(client, auth_headers, "zxqv plumbus frobnicate")
    assert reply["intent"] == "unmatched"
    assert "could not match" in reply["answer"]
    assert len(reply["points"]) >= 3


def test_every_answer_is_concise_and_sourced_where_it_should_be(client, auth_headers):
    questions = [
        "How do I scan a product?",
        "What does the AI compliance score mean?",
        "How do I calibrate for millimetres?",
        "What does OPEN mean?",
        "How many inspections are awaiting review?",
        "Does it need internet?",
    ]
    for question in questions:
        reply = _ask(client, auth_headers, question)
        assert reply["answer"].strip(), question
        assert _words(reply["answer"]) <= 70, f"too long for: {question}"
        assert len(reply["points"]) <= 6, question
        # application answers always name where to act or where the fact came from
        if reply["kind"] != "app_guidance" or reply["intent"] != "unmatched":
            assert reply["sources"] or reply["link"] or reply["points"], question


def test_engine_reports_whether_a_provider_rephrased(client, auth_headers):
    reply = _ask(client, auth_headers, "How do I scan a product?")
    assert reply["engine"] in ("retrieval", "provider_rephrased", "retrieval_guard")


def test_assistant_is_available_to_a_regulated_entity(client, entity_headers):
    reply = _ask(client, entity_headers, "What does PENDING_FINALIZATION mean?")
    assert reply["kind"] == "glossary"
    assert "final" in reply["answer"].lower()


def test_assistant_queries_are_audited(client, admin_headers):
    _ask(client, admin_headers, "How do I scan a product?")
    audit = client.get("/audit?action=assistant_query", headers=admin_headers)
    assert audit.status_code == 200
    assert any(row["action"] == "assistant_query" for row in audit.json()["items"])
