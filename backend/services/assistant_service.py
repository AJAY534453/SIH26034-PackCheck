"""Assistant service — grounded, concise answers about this application and its legal references.

Design rules (why this is not a text generator with a free hand):

* **Grounded.** An answer is assembled from the application's own knowledge base
  (:mod:`backend.assistant.knowledge`), from the versioned rule data the rule engine uses, and from
  live records in the caller's own data scope. Nothing is invented.
* **Concise.** One or two sentences, at most a few short steps, plus sources. The requirement is
  clarity, not verbosity.
* **Legal questions are separated from application guidance.** A legal answer always carries its
  reference and states that the Official Gazette text is authoritative; the assistant never
  paraphrases a legal requirement from memory.
* **Role aware.** Suggestions and answers are tuned to the caller's role and permissions, so a
  regulated entity is never told to use a staff-only screen.
* **Optional re-phrasing.** When a provider is configured the assembled, already-grounded answer may
  be re-worded — and the re-worded text is DISCARDED if it changes any number, which keeps the
  figures and legal references exactly as the application produced them.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from backend.assistant.knowledge import (
    GLOSSARY,
    KIND_APP,
    KIND_GLOSSARY,
    KIND_LEGAL,
    SUGGESTED_QUESTIONS,
    TOPICS,
    Topic,
)
from backend.authz import Permission, has_permission, scope_org_id
from backend.authz.permissions import ROLE_LABELS
from backend.models import Inspection, ProductScan, Rule, Violation, User
from backend.models.product_scan import PENDING_MESSAGE
from backend.rules.font_size import requirement_for
from backend.rules.registry import get_active_versions
from backend.services import repository_service

AREA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:cm\s*[²2]|sq\.?\s*cm|square\s*cm)")
# A rule reference such as "rule 6", "rule 6(1)", "rule 6(1)(c)", "rule 7(2)".
RULE_MENTION_RE = re.compile(
    r"rule\s*([0-9]{1,2}(?:\s*\(\s*[0-9a-z]+\s*\)){0,2})", re.IGNORECASE
)


def _common_prefix_len(a: str, b: str) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _score(topic: Topic, query: str, tokens: set[str]) -> float:
    score = 0.0
    for keyword in topic.keywords:
        if " " in keyword:
            if keyword in query:
                score += 3.0
        elif keyword in tokens:
            score += 2.0
        elif any(t.startswith(keyword) and len(keyword) >= 4 for t in tokens):
            score += 1.0
    for word in _tokens(topic.title):
        if len(word) >= 4 and word in tokens:
            score += 0.5
    return score


def _rank(query: str, *, limit: int = 3) -> list[tuple[float, Topic]]:
    q = (query or "").lower()
    tokens = set(_tokens(q))
    scored = [(_score(t, q, tokens), t) for t in TOPICS]
    scored = [pair for pair in scored if pair[0] > 0]
    scored.sort(key=lambda pair: -pair[0])
    return scored[:limit]


def _role_topics(user: User) -> list[Topic]:
    role = getattr(user, "role", "")
    preferred = [t for t in TOPICS if role in t.roles]
    return preferred


def _suggestions(user: User) -> list[str]:
    out = [t.title for t in _role_topics(user)]
    if has_permission(user, Permission.COMPLIANCE_FINALIZE):
        out.append("How do I finalize a pending decision?")
    for question in SUGGESTED_QUESTIONS:
        if question not in out:
            out.append(question)
    return out[:5]


def _source(label: str, *, kind: str = KIND_APP, reference: str = "", link: str = "") -> dict:
    return {"label": label, "kind": kind, "reference": reference, "link": link}


# --------------------------------------------------------------------------- dynamic handlers

def _legal_answer(db: Session, query: str) -> dict | None:
    """Answer a legal question from the versioned rule data — never from memory."""
    match = RULE_MENTION_RE.search(query)
    if not match:
        return None
    asked = re.sub(r"\s+", "", match.group(1))
    rules = db.query(Rule).all()
    hit = None
    best = 0
    for rule in rules:
        candidates = {re.sub(r"\s+", "", rule.rule_number or ""), re.sub(r"\s+", "", rule.rule_id or "")}
        for c in candidates:
            if not c:
                continue
            # Prefer an exact match; otherwise the CLOSEST rule reference (a longer common prefix
            # always beats a shorter one, so "6(1)(c)" is not answered with "6(1)(a)").
            if asked == c:
                hit, best = rule, 10_000
                break
            if asked.startswith(c) or c.startswith(asked):
                common = _common_prefix_len(asked, c)
                if common > best:
                    hit, best = rule, common
        if best == 10_000:
            break
    if hit is None:
        return {
            "answer": (
                f"I do not hold a rule matching 'rule {match.group(1)}' in the rule library. "
                "Open the Rule Library to see the versioned requirements this application evaluates, "
                "and always check the Official Gazette text for the authoritative wording."
            ),
            "points": [],
            "kind": KIND_LEGAL,
            "intent": "legal_lookup_miss",
            "sources": [_source("Rule Library", kind=KIND_LEGAL, link="/rules")],
            "link": "/rules",
        }
    version = next(
        (rv for rv in get_active_versions(db).values() if rv.rule_id_fk == hit.id), None
    )
    requirement = (version.requirement if version else "") or hit.description
    return {
        "answer": f"Rule {hit.rule_number} — {hit.title}: {requirement}",
        "points": [
            f"Source: {hit.source_reference or 'Legal Metrology (Packaged Commodities) Rules, 2011'}",
            "Status in this application: evaluated automatically"
            if (version and version.check_type != "manual_only")
            else "This requirement is verified manually; the application records that fact and never assumes it passed.",
            "The Official Gazette text is authoritative — this is a pointer, not legal advice.",
        ],
        "kind": KIND_LEGAL,
        "intent": "legal_rule",
        "sources": [
            _source(
                f"Rule {hit.rule_number}",
                kind=KIND_LEGAL,
                reference=hit.source_reference or "",
                link=f"/rules",
            )
        ],
        "link": "/rules",
    }


FONT_HINTS = (
    "font", "letter height", "height of letter", "height of the letter", "type size",
    "minimum size", "millimetre", "millimeter", "how tall", "how big", "mm",
)


def _font_size_answer(db: Session, query: str) -> dict | None:
    q = query.lower()
    if not any(k in q for k in FONT_HINTS):
        if not ("size" in q and any(k in q for k in ("label", "print", "text", "letter"))):
            return None
    area_match = AREA_RE.search(q)
    params = next(
        (rv.params or {} for rv in get_active_versions(db).values() if rv.check_type == "font_size"),
        {},
    )
    if not params:
        return None
    table_hint = "table_ii" if any(k in q for k in ("number", "pieces", "pcs", "length", "area", "metre", "meter")) else "table_i"
    lines: list[str] = []
    if area_match:
        area = float(area_match.group(1))
        requirement = requirement_for(params, quantity_family="NUMBER" if table_hint == "table_ii" else "MASS", area_cm2=area)
        if requirement:
            lines.append(
                f"For a principal display panel of {area:g} cm², Rule 7 requires at least "
                f"{requirement.required_mm} mm for ordinary printing and "
                f"{requirement_for(params, quantity_family='NUMBER' if table_hint == 'table_ii' else 'MASS', area_cm2=area, form='moulded').required_mm} mm "
                f"when blown, formed or molded ({requirement.table_id} serial {requirement.serial})."
            )
    else:
        table = params.get(table_hint) or {}
        rows = table.get("brackets", [])
        normal = ", ".join(f"{r['label']} → {r['normal_mm']} mm" for r in rows)
        lines.append(f"{table.get('id', 'Rule 7 table')}: {normal}.")
    lines.append(
        "Rule 7(3) also requires the width of a letter or numeral to be at least one third of its height. "
        "Rule 7(5) keeps the size of the net weight, retail sale price, expiry/best-before and consumer-care "
        "text subject to this rule even when another law also requires the information."
    )
    return {
        "answer": lines[0],
        "points": lines[1:],
        "kind": KIND_LEGAL,
        "intent": "font_size_requirement",
        "sources": [
            _source(
                "LM (PC) Rules, 2011 — Rule 7(2)-(5), Table-I / Table-II (G.S.R. 629(E), 23.06.2017)",
                kind=KIND_LEGAL,
                reference="Rule 7 — principal display panel: area, size and letter",
                link="/rules",
            )
        ],
        "link": "/inspections",
    }


def _data_answer(db: Session, user: User, query: str) -> dict | None:
    q = (query or "").lower()
    wants_count = any(k in q for k in ("how many", "count", "number of", "total"))
    if not wants_count:
        return None
    scoped = scope_org_id(user)
    inspections = db.query(Inspection)
    if scoped is not None:
        inspections = inspections.filter(Inspection.organization_id == scoped)
    if "pending" in q or "finaliz" in q:
        summary = repository_service.list_scans(db, user=user, page=1, page_size=1)
        answer = (
            f"{summary['pending_finalization']} scan(s) are awaiting official finalization and "
            f"{summary['finalized']} are finalized, in the {summary['total']} scan repository "
            "record(s) you can see."
        )
        link = "/repository"
    elif "inspection" in q:
        awaiting = inspections.filter(Inspection.status == "AWAITING_REVIEW").count()
        answer = (
            f"{inspections.count()} inspection(s) are in your scope; {awaiting} are awaiting review."
        )
        link = "/inspections"
    elif "violation" in q:
        violations = db.query(Violation)
        if scoped is not None:
            violations = violations.join(Inspection, Violation.inspection_id == Inspection.id).filter(
                Inspection.organization_id == scoped
            )
        open_count = violations.filter(Violation.status.in_(["OPEN", "NEEDS_REVIEW"])).count()
        answer = f"{violations.count()} violation record(s) are visible to you; {open_count} still need attention."
        link = "/violations"
    elif "score" in q or "compliant" in q:
        summary = repository_service.list_scans(db, user=user, page=1, page_size=1)
        facets = summary["facets"]["ai_verdict"]
        answer = (
            "Automated verdicts in your scope: "
            + ", ".join(f"{k or 'UNSCORED'} {v}" for k, v in sorted(facets.items()))
            + "."
        )
        link = "/repository"
    else:
        return None
    return {
        "answer": answer,
        "points": ["Counts come from your own data scope; another organization's rows are never included."],
        "kind": KIND_APP,
        "intent": "live_count",
        "sources": [_source("Live application data", link=link)],
        "link": link,
    }


def _glossary_answer(query: str) -> dict | None:
    q = (query or "").lower()
    if not any(k in q for k in ("what does", "mean", "meaning", "explain", "difference between", "stand for")):
        return None
    tokens = set(_tokens(q))
    for term, meaning in GLOSSARY.items():
        t_tokens = set(_tokens(term))
        if term in q or t_tokens <= tokens:
            return {
                "answer": f"{term.upper()}: {meaning}",
                "points": [],
                "kind": KIND_GLOSSARY,
                "intent": "glossary",
                "sources": [_source("Status meanings used across the application")],
                "link": "",
            }
    return None


# --------------------------------------------------------------------------- PRO identity

#: The assistant presents itself as PRO everywhere: the panel, the greeting, the closing line.
ASSISTANT_NAME = "PRO"

#: Short address used in the closing line, keyed by role. "Have a good day, Admin."
_SHORT_ROLE = {
    "ADMIN": "Admin",
    "INSPECTOR": "Inspector",
    "VIEWER": "Viewer",
    "ENFORCEMENT_OFFICER": "Officer",
    "REGULATED_ENTITY": "Compliance contact",
    "INTERNAL_COMPLIANCE": "Compliance team",
}


def _first_name(user: User) -> str:
    name = (getattr(user, "full_name", "") or getattr(user, "username", "") or "").strip()
    return name.split()[0] if name else "there"


def _role_label(user: User) -> str:
    role = getattr(user, "role", "") or ""
    return ROLE_LABELS.get(role, role.replace("_", " ").title() or "user")


def _greeting(user: User) -> str:
    """PRO greets the actual signed-in person by name and role — never a generic welcome."""
    return f"Hello {_first_name(user)}, signed in as {_role_label(user)}."


def _closing(user: User) -> str:
    """One short, role-aware sign-off. Never a paragraph."""
    role = getattr(user, "role", "") or ""
    return f"Have a good day, {_SHORT_ROLE.get(role, _role_label(user))}."


# --------------------------------------------------------------------------- record context


def resolve_context(db: Session, user: User, context: dict | None) -> dict:
    """Resolve the record the caller currently has open — inside their own scope only.

    The context is a hint, never an authorization: an id that is outside the caller's organization
    resolves to nothing, and PRO then says it cannot see that record rather than describing it.
    """
    out: dict = {"inspection": None, "scan": None, "path": "", "out_of_scope": False}
    if not context:
        return out
    out["path"] = str(context.get("path") or "")[:200]
    scoped = scope_org_id(user)

    inspection = None
    scan = None
    scan_id = context.get("scan_id")
    inspection_id = context.get("inspection_id")
    if isinstance(scan_id, int) and scan_id > 0:
        scan = db.query(ProductScan).filter(ProductScan.id == scan_id).first()
        if scan is not None and scoped is not None and scan.organization_id != scoped:
            out["out_of_scope"] = True
            return out
    if isinstance(inspection_id, int) and inspection_id > 0:
        inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
        if inspection is not None and scoped is not None and inspection.organization_id != scoped:
            out["out_of_scope"] = True
            return out
    if inspection is None and scan is not None:
        inspection = db.query(Inspection).filter(Inspection.id == scan.inspection_id).first()
    if scan is None and inspection is not None:
        scan = db.query(ProductScan).filter(ProductScan.inspection_id == inspection.id).first()
    out["inspection"] = inspection
    out["scan"] = scan
    if inspection is None and scan is None:
        # The id exists nowhere — not a permission problem, and nothing is revealed either way.
        out["out_of_scope"] = bool(scan_id or inspection_id)
    return out


def _context_line(ctx: dict) -> str:
    """One short sentence naming the open record, or empty when nothing is open."""
    inspection = ctx.get("inspection")
    if inspection is None:
        return ""
    scan = ctx.get("scan")
    identity = " ".join(x for x in [(scan.brand if scan else ""), (scan.product_name if scan else "")] if x).strip()
    if not identity:
        identity = "unidentified product"
    score = f"{round(float(scan.compliance_score or 0), 1)}% compliance" if scan else "not yet reviewed"
    return f"You have {inspection.inspection_number} open ({identity}) — {score}."


def _inspection_answer(db: Session, user: User, query: str, ctx: dict) -> dict | None:
    """Answer about the record currently open, from its own stored analysis and evidence."""
    if ctx.get("out_of_scope"):
        return {
            "answer": (
                "I can only read records inside your own scope, and that record is outside it. "
                "PRO never describes a record the signed-in user may not open."
            ),
            "points": [],
            "kind": KIND_APP,
            "intent": "context_out_of_scope",
            "sources": [],
            "link": "",
        }
    inspection = ctx.get("inspection")
    if inspection is None:
        return None
    scan = ctx.get("scan")
    q = (query or "").lower()
    link = f"/inspections/{inspection.id}"
    can_review = has_permission(user, Permission.COMPLIANCE_VIEW)

    def base(
        answer: str,
        points: list[str],
        intent: str,
        source_label: str = "Inspection record",
        ref: str = "",
    ) -> dict:
        return {
            "answer": answer,
            "points": points,
            "kind": KIND_APP,
            "intent": intent,
            "sources": [_source(source_label, reference=ref, link=link)],
            "link": link,
        }

    # ---------- what changed / was this record corrected? ----------
    if any(k in q for k in ("reprocess", "reprocessed", "migrat", "what changed", "corrected", "updated analysis")):
        from backend.services import reprocess_service

        revisions = reprocess_service.revisions_for(db, inspection.id, limit=1)
        if not revisions:
            return base(
                f"{inspection.inspection_number} has not been reprocessed; its active result was "
                f"produced by engine {inspection.pipeline_version or 'an earlier pipeline'}. "
                f"Ask an inspector or admin to run 'Reprocess inspection' to regenerate it.",
                [],
                "context_reprocess_none",
                "Inspection processing record",
            )
        rev = revisions[0]
        headline = (rev.get("changes") or {}).get("headline") or []
        points = [
            f"Run {rev['created_at']} by {rev['actor'] or 'system'} ({rev['kind']}) — engine {rev['engine_version']}, rules {rev['rule_fingerprint']}.",
            f"Reason: {rev['reason'] or 'not stated'}.",
            f"Fields changed: {rev['fields_changed']}; rule results changed: {rev['rules_changed']}.",
            "The previous result is preserved on the revision row as the BEFORE snapshot.",
        ]
        return base(
            f"Corrected result for {inspection.inspection_number}: " + ("; ".join(headline[:3]) if headline else "no material change"),
            points,
            "context_reprocess",
            f"Reprocessing revision #{rev['revision_no']}",
            rev["engine_version"],
        )

    # ---------- why is it pending / why manual review ----------
    if any(k in q for k in ("pending", "manual review", "needs review", "why not final", "why final", "finaliz", "approve")):
        if scan is None:
            return base(
                f"{inspection.inspection_number} has not been through the automated compliance review yet, "
                "so there is nothing to finalize. Run the pipeline first.",
                [],
                "context_no_review",
                "Inspection record",
            )
        if scan.review_status == "FINALIZED" and scan.official_decision:
            return base(
                f"{inspection.inspection_number} is FINALIZED: official decision {scan.official_decision}, "
                f"recorded by {scan.finalized_by or 'an authorized official'}.",
                [
                    f"AI preliminary verdict was {scan.ai_verdict or '—'} at {round(float(scan.compliance_score or 0), 1)}% compliance "
                    f"and {round(float(scan.coverage_score or 0), 1)}% evidence coverage.",
                    "The official decision is the one that counts; the automated verdict is only preliminary.",
                ],
                "context_finalized",
                "Compliance review record",
                f"threshold {scan.threshold:g}%",
            )
        unresolved = _unresolved_checks(scan)
        points = [
            f"Automated verdict: {scan.ai_verdict or '—'} — preliminary only, never a legal decision.",
            f"Application threshold: {scan.threshold:g}%; evidence coverage floor: {scan.coverage_floor:g}%.",
        ]
        if unresolved:
            points.append(
                "Unresolved requirements: "
                + "; ".join(f"Rule {c['rule_number']} ({c['status']})" for c in unresolved[:4])
                + "."
            )
        points.append(
            "An authorized official records the final decision on the inspection page; until then the public status reads: "
            + PENDING_MESSAGE
        )
        return base(
            f"{inspection.inspection_number} needs official finalization: compliance "
            f"{round(float(scan.compliance_score or 0), 1)}% and evidence coverage "
            f"{round(float(scan.coverage_score or 0), 1)}% do not satisfy the application's automated-pass gate "
            f"(≥ {scan.threshold:g}% compliance, no failure, coverage ≥ {scan.coverage_floor:g}%).",
            points,
            "context_pending",
            "Compliance review record",
            f"{round(float(scan.compliance_score or 0), 1)}% / {round(float(scan.coverage_score or 0), 1)}%",
        )

    # ---------- evidence question about a named declaration ----------
    if any(k in q for k in ("evidence", "where did", "which image", "source image", "crop", "how do you know", "confiden")):
        name = _mentioned_field(q, db)
        if name:
            return base(*_evidence_answer(db, inspection, name, link))
        rows = _fields_with_evidence(db, inspection.id)
        if not rows:
            return base(
                f"No retained evidence crops exist for {inspection.inspection_number} yet.",
                ["Evidence is captured when the pipeline stores a source region for a detected value."],
                "context_evidence_none",
                "Evidence store",
            )
        return base(
            f"{inspection.inspection_number} has {len(rows)} declaration(s) with retained source regions.",
            [f"{r['field_name'].replace('_', ' ')} = {r['value']} ← {r['view']} image, conf {round(r['confidence'] * 100)}%" for r in rows[:5]],
            "context_evidence_list",
            "Evidence store",
        )

    # ---------- score / coverage explanation ----------
    if any(k in q for k in ("score", "percentage", "coverage", "compliant", "verdict", "why")):
        if scan is None:
            return None
        return base(
            f"{inspection.inspection_number}: AI preliminary verdict {scan.ai_verdict or '—'} — compliance "
            f"{round(float(scan.compliance_score or 0), 1)}% over decided checks, evidence coverage "
            f"{round(float(scan.coverage_score or 0), 1)}% of the applicable machine-checkable requirements.",
            [
                f"Review status: {scan.review_status}. Official decision: {scan.official_decision or 'pending'}.",
                "Coverage is reported next to the score so unread evidence is never mistaken for half-compliance.",
                "The scan page shows the itemised basis: what was counted, what was excluded, and why.",
            ],
            "context_score",
            "Compliance review record",
            f"{round(float(scan.compliance_score or 0), 1)}%",
        )

    # ---------- a named declaration inside this record ----------
    name = _mentioned_field(q, db)
    if name:
        return base(*_field_answer(db, inspection, name, link))

    # ---------- overview (also the fallback when a record is open) ----------
    if scan is None:
        return None
    detected = _detected_summary(scan)
    return base(
        f"{inspection.inspection_number} — {detected}",
        [
            f"Automated (preliminary) verdict: {scan.ai_verdict or '—'}; compliance {round(float(scan.compliance_score or 0), 1)}%, "
            f"coverage {round(float(scan.coverage_score or 0), 1)}%.",
            "Ask me why it is pending, what evidence a value came from, or what changed in a reprocess run.",
        ],
        "context_overview",
        "Inspection record",
    )


#: Question shapes that ask about the RECORD open on screen rather than about how the application
#: works in general. With a record open these win over the general topics: "what is the compliance
#: score?" means THIS score, while "what does the compliance score mean?" stays an explanation.
_RECORD_INTENTS = (
    "pending", "manual review", "needs review", "finaliz", "approve", "recent",
    "reprocess", "migrat", "what changed", "corrected", "updated analysis",
    "evidence", "where did", "which image", "source image", "crop", "how do you know", "confiden",
)
_SCORE_WORDS = ("score", "percentage", "coverage", "compliant", "verdict")
_EXPLANATORY = ("mean", "meaning", "how is", "how are", "how do", "how does", "difference", "explain", "calculated", "formula")
_DEFINITE = ("this", "it", "its", "current", "record", "inspection", "scan", "here", "our")


def context_intent(query: str, *, has_topic: bool) -> bool:
    """Whether a question should be answered about the open record instead of a general topic."""
    q = (query or "").lower()
    if any(k in q for k in _RECORD_INTENTS):
        return True
    if any(k in q for k in _SCORE_WORDS):
        explanatory = any(k in q for k in _EXPLANATORY)
        definite = any(k in q for k in _DEFINITE)
        return (not explanatory) or definite
    # Nothing matched a general topic either — a record overview is more useful than "I could not
    # match that", and never less accurate.
    return not has_topic


def _unresolved_checks(scan: ProductScan) -> list[dict]:
    import json

    try:
        checks = json.loads(scan.checks_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [
        c
        for c in checks
        if isinstance(c, dict) and c.get("status") in ("UNCERTAIN", "FAIL", "NOT_VERIFIED", "MANUAL_VERIFICATION_REQUIRED")
    ]


def _detected_summary(scan: ProductScan) -> str:
    bits = [x for x in (scan.brand, scan.product_name) if x]
    head = " ".join(bits).strip() or "product not identified from the supplied images"
    extras = []
    if scan.net_quantity:
        extras.append(f"net quantity {scan.net_quantity}")
    if scan.mrp:
        extras.append(f"MRP {scan.mrp}")
    if scan.manufacturer:
        extras.append(f"manufacturer {scan.manufacturer}")
    return head + (f" ({'; '.join(extras)})" if extras else "")


def _mentioned_field(query: str, db: Session) -> str:
    """Which tracked declaration the question names, if any (from the schema, not a guess)."""
    from backend.services.inspection_service import TRACKED_DECLARATION_FIELDS

    tokens = set(_tokens(query))
    best = ""
    best_len = 0
    for name in TRACKED_DECLARATION_FIELDS:
        parts = set(_tokens(name))
        if parts and parts <= tokens:
            if len(parts) > best_len:
                best, best_len = name, len(parts)
    return best


def _fields_with_evidence(db: Session, inspection_id: int) -> list[dict]:
    from backend.models import Evidence, InspectionImage

    rows = db.query(Evidence).filter(Evidence.inspection_id == inspection_id).all()
    views = {
        i.id: (i.role or "").replace("_", " ").title()
        for i in db.query(InspectionImage).filter(InspectionImage.inspection_id == inspection_id).all()
    }
    out = []
    for e in rows:
        if not (e.normalized_value or "").strip():
            continue
        out.append(
            {
                "field_name": e.field_name or "",
                "value": (e.normalized_value or "").strip()[:60],
                "view": views.get(e.image_id or -1, "package"),
                "confidence": float(e.confidence or 0.0),
            }
        )
    return out


def _evidence_answer(db: Session, inspection, field_name: str, link: str) -> tuple[str, list[str], str]:
    """Where one declaration's value came from, with the retained region named."""
    from backend.models import Evidence, InspectionImage

    row = (
        db.query(Evidence)
        .filter(Evidence.inspection_id == inspection.id, Evidence.field_name == field_name)
        .order_by(Evidence.id)
        .first()
    )
    label = field_name.replace("_", " ")
    if row is None:
        return (
            f"No retained source region exists for '{label}' on {inspection.inspection_number} — the value was "
            "not anchored to a stored crop, or nothing was detected in the supplied images.",
            ["'Not detected in the supplied images' never means the declaration is absent from the package."],
            "context_evidence_absent",
        )
    view = (
        db.query(InspectionImage).filter(InspectionImage.id == row.image_id).first()
        if row.image_id is not None
        else None
    )
    view_label = (view.role or "package").replace("_", " ").title() if view else "package"
    return (
        f"{label}: {row.normalized_value or '—'} — read from the {view_label} image (confidence "
        f"{round(float(row.confidence or 0) * 100)}%).",
        [
            f"Raw OCR text: {row.raw_text or '—'}",
            f"Extraction: {row.extraction_method or 'OCR'}",
            f"Open {link} → Compliance analysis → View evidence to see the highlighted region on the image.",
            f"Evidence record #{row.id} retained on {inspection.inspection_number}.",
        ],
        "context_evidence",
    )


def _field_answer(db: Session, inspection, field_name: str, link: str) -> tuple[str, list[str], str]:
    from backend.models import ExtractedField

    row = (
        db.query(ExtractedField)
        .filter(ExtractedField.inspection_id == inspection.id, ExtractedField.field_name == field_name)
        .first()
    )
    label = field_name.replace("_", " ")
    if row is None:
        return (
            f"'{label}' is not a tracked declaration on {inspection.inspection_number}.",
            [],
            "context_field_unknown",
        )
    value = (row.display_value or "").strip()
    if not value:
        return (
            f"{label}: not detected in the supplied images — {row.uncertainty_reason or 'manual verification required'}.",
            ["This does not prove the declaration is absent from the package."],
            "context_field_missing",
        )
    points = [
        f"State: {row.state}; confidence {round(float(row.confidence or 0) * 100)}%.",
        f"Read from: {row.source_engine or 'OCR'}" + (f" + {row.preprocessing_variant}" if row.preprocessing_variant and row.preprocessing_variant != "original" else "") + f" on image #{row.source_image_id or '—'}.",
    ]
    if row.uncertainty_reason:
        points.append(f"Caution: {row.uncertainty_reason}")
    points.append(f"Open {link} to see the retained region and the rule that uses it.")
    return (f"{label}: {value}", points, "context_field")


# --------------------------------------------------------------------------- provider re-phrasing

_DIGIT_RE = re.compile(r"\d+(?:\.\d+)?")


def _maybe_rephrase(answer: str, points: list[str], query: str) -> tuple[str, list[str], str]:
    """Optionally re-word an already-grounded answer; discarded if any figure changes."""
    from backend.ai.provider import provider_status, run_text_json

    if not provider_status().enabled:
        return answer, points, "retrieval"
    prompt = (
        "You are re-wording an answer that has ALREADY been produced by a compliance application. "
        "Keep every number, unit, status word and legal reference EXACTLY as given. Do not add facts, "
        "do not add legal advice, do not exceed two sentences for 'answer'.\n"
        f"User question: {query}\n"
        f"answer: {answer}\n"
        f"points: {' | '.join(points)}\n"
        'Reply as JSON: {"answer": "...", "points": ["..."]}'
    )
    try:
        import json

        payload = json.loads(run_text_json(prompt))
        new_answer = str(payload.get("answer") or "").strip()
        new_points = [str(p).strip() for p in (payload.get("points") or []) if str(p).strip()]
    except Exception:
        return answer, points, "retrieval"
    if not new_answer:
        return answer, points, "retrieval"
    original_digits = sorted(_DIGIT_RE.findall(" ".join([answer] + points)))
    if sorted(_DIGIT_RE.findall(" ".join([new_answer] + new_points))) != original_digits:
        # A re-wording that changed a figure is not trustworthy — keep the grounded text.
        return answer, points, "retrieval_guard"
    return new_answer, new_points or points, "provider_rephrased"


# --------------------------------------------------------------------------- public entry point

def respond(db: Session, user: User, message: str, context: dict | None = None) -> dict:
    """Answer one question. Always returns a concise, sourced, role-aware result.

    ``context`` may name the record the user currently has open (``inspection_id`` / ``scan_id`` /
    ``path``). It is resolved inside the caller's own scope, so PRO can answer about the open
    inspection without ever describing a record the caller may not open.
    """
    query = (message or "").strip()
    ctx = resolve_context(db, user, context)
    context_line = _context_line(ctx)

    def shell(result: dict) -> dict:
        """Every reply carries PRO's identity, the role-aware sign-off and the open-record hint."""
        result.setdefault("name", ASSISTANT_NAME)
        result.setdefault("closing", _closing(user))
        result.setdefault("context_line", context_line)
        result.setdefault("followups", _suggestions(user))
        return result

    if not query:
        return shell(
            {
                "answer": "Ask me how to use any part of PACKCHECK AI — scanning, the compliance review, "
                          "finalization, product history or the legal requirement a check came from.",
                "points": [context_line] if context_line else [],
                "kind": KIND_APP,
                "intent": "empty",
                "sources": [],
                "link": "",
                "engine": "retrieval",
            }
        )

    lowered = query.lower()
    if re.fullmatch(r"(hi|hello|hey|namaste|good (morning|afternoon|evening))[\s!.,]*", lowered):
        return shell(
            {
                "answer": f"{_greeting(user)} What would you like help with?",
                "points": [context_line] if context_line else [],
                "kind": KIND_APP,
                "intent": "greeting",
                "sources": [],
                "link": "",
                "engine": "retrieval",
            }
        )

    def finish(result: dict) -> dict:
        result["answer"], result["points"], result["engine"] = _maybe_rephrase(
            result["answer"], result["points"], query
        )
        return shell(result)

    def topic_result(topic: Topic) -> dict:
        points = list(topic.steps)
        if topic.kind == KIND_LEGAL:
            points.append("The Official Gazette text is authoritative; this is a pointer, not legal advice.")
        return {
            "answer": topic.answer,
            "points": points,
            "kind": topic.kind,
            "intent": topic.id,
            "sources": [_source(topic.title, kind=topic.kind, link=topic.link)],
            "link": topic.link,
        }

    ranked = _rank(query)
    strong_topic = ranked[0][1] if ranked and ranked[0][0] >= 3.0 else None

    # Specific handlers first (a named rule, a computable legal figure, a live count, the OPEN
    # RECORD), then a strong topic match, then the glossary — so "what does the compliance score
    # mean" explains the scoring rather than returning a one-line dictionary entry, while "why is
    # this pending" answers about the inspection the user is actually looking at.
    for handler in (
        lambda: _legal_answer(db, query),
        lambda: _font_size_answer(db, query),
        lambda: _data_answer(db, user, query),
        lambda: _inspection_answer(db, user, query, ctx)
        if (ctx.get("inspection") is not None or ctx.get("out_of_scope"))
        and context_intent(query, has_topic=strong_topic is not None)
        else None,
        lambda: topic_result(strong_topic) if strong_topic else None,
        lambda: _glossary_answer(query),
    ):
        result = handler()
        if result:
            return finish(result)

    if ranked and ranked[0][0] >= 2.0:
        return finish(topic_result(ranked[0][1]))

    # With a record open, an unmatched question still gets something useful about THAT record
    # instead of a generic "I could not match that".
    if ctx.get("inspection") is not None or ctx.get("out_of_scope"):
        contextual = _inspection_answer(db, user, "overview", ctx)
        if contextual:
            return finish(contextual)

    related = [t.title for _, t in ranked] or [t.title for t in TOPICS[:3]]
    return shell(
        {
            "answer": (
                "I could not match that to this application. I answer questions about scanning, the "
                "compliance review and its score, finalizing pending decisions, product history, the "
                "repository, user tools and the legal references the checks use."
            ),
            "points": [f"Try: {title}" for title in related[:3]] + [f"Pending results always read: {PENDING_MESSAGE}"],
            "kind": KIND_APP,
            "intent": "unmatched",
            "sources": [],
            "link": "",
            "engine": "retrieval",
        }
    )


def welcome(db: Session, user: User, context: dict | None = None) -> dict:
    """What PRO offers on open — role aware, record aware, no invented capability."""
    role_topics = [t.title for t in _role_topics(user)]
    ctx = resolve_context(db, user, context)
    context_line = _context_line(ctx) if ctx.get("inspection") is not None else ""
    context_link = f"/inspections/{ctx['inspection'].id}" if ctx.get("inspection") is not None else ""
    suggestions = _suggestions(user)
    if context_line:
        suggestions = [
            "Why does this need finalization?",
            "What evidence supports the detected values?",
            "What changed in the last reprocess?",
            *suggestions,
        ][:5]
    return {
        "name": ASSISTANT_NAME,
        "greeting": _greeting(user),
        "closing": _closing(user),
        "suggestions": suggestions,
        "role_focus": role_topics,
        "context_line": context_line,
        "context_link": context_link,
        "can_finalize": has_permission(user, Permission.COMPLIANCE_FINALIZE),
        "can_run_analysis": has_permission(user, Permission.ANALYSIS_RUN),
        "note": (
            f"I am {ASSISTANT_NAME} — I explain how PACKCHECK AI works and point to the legal "
            "reference a check came from. I never issue a legal decision; official decisions are "
            "recorded by authorized officials."
        ),
    }


__all__ = ["respond", "welcome", "resolve_context", "ASSISTANT_NAME"]
