# Architecture

## Overview

Modular monolith: one FastAPI process, one SQLite database, one evidence file store, one React SPA.

```
React SPA (Vite, :5174)
   │  /api proxy
   ▼
FastAPI (:8001) ── auth · inspections · meta(files/dashboard/products/violations/reports/rules)
   │                 · repository (scan repository, history, finalization, Rule 7 calibration)
   │                 · listings (e-commerce capture, listing declarations, package comparison)
   │                 · assistant (in-application AI assistant) · roles · tools
   │
   ├── services/           business logic (inspection_service orchestrates the pipeline)
   │                       compliance_service scores · repository_service stores the record
   │                       listing_service compares · assistant_service answers
   ├── preprocessing/      quality + variants + geometry (OpenCV)
   ├── ocr/                engine abstraction + RapidOCR (swappable)
   ├── extraction/         typed field extractors + candidate scoring + conflict detection
   ├── normalization/      canonical values + technical validation
   ├── classification/     category suggestion from OCR evidence
   ├── rules/              registry + applicability + deterministic engine + decision logic
   ├── evidence/ reports/  crop storage + ReportLab PDF
   └── models/ database/   SQLAlchemy 2 + SQLite
```

## Pipeline (15 stages, tracked live in `inspections.stage_status`)

```
UPLOAD VALIDATION → ORIGINAL PRESERVATION → QUALITY ASSESSMENT → PREPROCESSING VARIANTS
→ TEXT DETECTION / OCR → REGION OCR (crop re-check) → VISION EXTRACTION (optional)
→ CANDIDATE GENERATION → FIELD EXTRACTION → NORMALIZATION → CLASSIFICATION
→ RULE APPLICABILITY → RULE VALIDATION → EVIDENCE GENERATION → INSPECTION RESULT (decision)
```

`vision_extraction` is reported as `done` only when a provider actually returned readings, and as
`skipped` (with a reason recorded on the inspection) otherwise — the stage list never claims work
that did not run.

Any stage failure degrades safely: partial results are retained, status becomes FAILED with a
reason, and nothing downstream is fabricated.

## Vision layer (`backend/ai/`)

The platform is OCR-first and works with no vendor at all. A vision provider is an **optional second
perception source**, and it is architecturally boxed in:

```
backend/ai/provider.py    provider abstraction + status. Currently Google Gemini over HTTPS using
                          only the standard library (no SDK, nothing new to install). The API key
                          is read from the server environment and is never returned, logged or
                          forwarded to a client.
backend/ai/extractor.py   the strict JSON prompt ("report only what is visible; null when unsure;
                          never decide compliance"), response parsing/validation, OCR
                          CORROBORATION, and conversion of readings into ordinary Candidates.
backend/ai/schema.py      the tracked field names a model may report, and the observation types.
```

Why a reading is not a fact:

| Situation | What the pipeline does |
|---|---|
| The reading matches the on-device OCR text of the same image | Candidate keeps the model's confidence and is ranked normally |
| The reading matches nothing the OCR saw | Confidence is capped (≤0.62) and the candidate is marked `inferred`, which the field writer records as **UNCERTAIN** with the reason "not corroborated by on-device OCR — offered for human confirmation" |
| The reading disagrees with an OCR reading of the same declaration | Both survive as candidates; the existing ranker marks the field **CONFLICTING** and queues it for human resolution |
| The provider errors, times out or returns malformed JSON | The stage records the failure and the inspection completes normally on OCR evidence alone |

The consequence that matters for the SIH question *"how does your AI know this?"*: a vision reading
is never able to skip normalization, field validators, ranking, conflict detection, the rule engine
or human review — it only adds an observation to the same evidence pool.

## Consumer tools (`backend/api/tools.py`, `backend/services/tools_service.py`)

| Tool | Storage | Deterministic logic (Python, never the model) |
|---|---|---|
| Bill Scanner | `bills` + an immutable image in the `bills` evidence folder | billed price vs printed MRP → `POTENTIAL_PRICE_DIFFERENCE` / `PRICE_AT_OR_BELOW_MRP` / `INSUFFICIENT_DATA`, with the difference and percentage |
| Grocery | `grocery_items` (only ever written by an explicit user action) | expiry resolution (`PRINTED_ON_PACKAGE`, `DURATION_FROM_MANUFACTURING_DATE`, `DURATION_ONLY_NO_ANCHOR`, `NOT_DECLARED`) and the day countdown that produces EXPIRED / EXPIRING_SOON |
| Complaint Center | `complaints` with an append-only JSON `timeline` | the status machine SUBMITTED → UNDER_REVIEW → EVIDENCE_VERIFIED → RESOLVED, role-gated and fully audited |

Bill readings carry an `extraction_source` (`vision` | `manual` | `demo` | `vision+manual`), so the UI can
state where a number came from. Sample values are labelled SAMPLE wherever they appear and are never
blended into a real reading.

## Key design decisions

**Extraction ≠ Validation ≠ Legal decision.** Extractors produce *candidates* (observations).
The rule engine produces *evaluations*. `rules/decision.py` aggregates to COMPLIANT /
NON_COMPLIANT / NEEDS_MANUAL_REVIEW. A human finalizes. No LLM participates in any legal decision —
and with a provider configured, an LLM does not participate in a legal decision either: it only adds
corroborated-or-flagged candidates to the extraction stage.

**MRP extraction is keyword-anchored, never "largest number".** An MRP keyword must co-occur on
the OCR line with a currency token; unit-price patterns (`₹0.40/g`, `per kg`, `/100g`), nutrition
rows, phones, FSSAI numbers, PINs, batches and dates are negative patterns. Unit sale price is a
separate field that can never satisfy the MRP rule.

**Conflicts are surfaced, never merged.** If two materially different candidates score within
85% of each other, the field is CONFLICTING and the inspection routes to review. Numeric
formatting differences (200 vs 200.00) are agreement, not conflict.

**Missing evidence ≠ violation.** A declaration that wasn't detected yields FAIL only when image
quality was GOOD/ACCEPTABLE (so OCR plausibly captured the label); otherwise UNCERTAIN. Poor image
quality never becomes a legal conclusion.

**Rules are versioned data.** Definitions live in `backend/rules/definitions/*.json`, seeded into
`rules` + `rule_versions`. Editing a definition creates a new version; evaluations pin the exact
`rule_version_id` used, so history is immutable. Manual-only checks (Rule 4/5/7 style) are
explicitly marked and never block an automated decision — they inform the reviewer.

**Applicability precedes validation.** Import-only rules require import evidence; category rules
require a confidently classified category; when classification is UNCERTAIN/CONFLICTING,
category-specific applicability returns NOT_APPLICABLE with an explanatory reason.

**Originals are immutable.** Uploads are stored byte-for-byte with UUID names; every processed
artifact (variants, crops, reports) is a new file. Evidence rows only reference files that exist.

**A candidate is as inspectable as a fact.** Evidence rows are retained for any field that either
asserts a value (DETECTED / CONFLICTING / MANUALLY_CORRECTED) *or* offers one for review
(UNCERTAIN with a value — an ambiguous date code, a logo read as a brand). The evidence row carries
the source image, bbox in **original-image coordinates**, crop, raw OCR text and a `note` marking
review candidates. An inspector must be able to see where a candidate came from before confirming
or rejecting it; a value with no reachable source region is never shown as though it were evidence.

**Normalization never rewrites evidence.** Each field keeps `raw_value` (exactly what OCR read)
separate from `display_value` (canonical/human-readable). For entity and address fields, display
tidying is limited to OCR presentation defects — word joins (`KonguNagar` → `Kongu Nagar`), repeated
commas, a missing space after a comma, and a PIN-code hyphen. ALL-CAPS fused strings are never split
(the split would be a guess) and no word is ever added, removed or corrected.

**OCR evidence is stored once and reused.** `ocr_results` holds every recognised line per image
(engine, preprocessing variant, bbox in original-image coordinates). Because the original image is
immutable, reprocessing reuses that evidence by default — identical evidence in, identical
declarations out — and `?refresh_ocr=true` re-runs the recogniser when that is explicitly wanted.
This makes reprocessing deterministic *and* removes the pipeline's slowest step from it
(measured on the real three-surface inspection: ~60 s with recognition, **1.6 s** reusing evidence).

**Label/value association uses the block's geometry, not a single row.** Declaration blocks are
printed as two columns (labels left, values right) and OCR row offsets between them can exceed a
line height on small/inkjet print. Values are therefore sought in the anchor's value column within
a row band, and a printed date block is additionally reconciled chronologically: a packing or
manufacturing date cannot post-date the use-by/expiry date of the same pack, so if the
nearest-neighbour reading produced such an ordering the values are re-paired and the superseded
reading is recorded in the candidate's `reason`. A coherent block is left untouched.

**Rendered typography is read as layout, and refused when the evidence is weak.** A product title
printed on stacked lines (`CLASSIC` / `SWEET & SALTY`, often interrupted by a promo tag) is merged
into one block by column and row adjacency; a line read below 60% glyph confidence is never
published as a title; and a single printed word is never asserted as both the brand and the product
title (the brand reading stands, the title is reported as not detected).

**A company entity is never the product name.** Legal-entity suffixes and unambiguous company words
are detected on a *squashed* view of the line, so OCR word fusion (`BRITANNIA INDUSTRIESLTD.`)
cannot smuggle a manufacturer line into the product-title field. An entity printed with no readable
role anchor is still offered (as UNCERTAIN, role unresolved) rather than dropped.

**Identifiers keep identifier semantics.** A token in the value column of a `Batch`/`Lot` label is
an identifier: it is never converted into a quantity or a date, letters in it are never rewritten
as digits, and values without a single digit are not offered at all.

**A declaration a photograph cannot show is not scored against it.** The online-listing
requirements (Rule 6(10) and the package/listing comparison) live in
`backend/rules/definitions/listings/`, which the package rule registry's `glob("*.json")` does not
reach. A listing requirement therefore can never enter a package compliance percentage, and a
package requirement is never scored against a web page. Each listing catalog entry carries a
`legal_status` — `VERIFIED_PRIMARY`, `VERIFIED_SECONDARY_TEXT`, `LEGAL_REFERENCE_REQUIRES_VERIFICATION`
or `APPLICATION_POLICY_NOT_A_LEGAL_PROVISION` — and the UI renders that status verbatim next to the
result, so a citation the application has not confidently verified cannot read as settled law.

## Online listings / e-commerce (`backend/services/listing_service.py`)

```
listing text (pasted, or transcribed from a screenshot / captured page)
   ↓  extraction/listing.py — the listing is rendered as synthesised OCR lines and the SAME
   ↓  extractors run over it (MRP, quantity, dates, entities, contacts, identifiers)
listing declarations (each with the line it was read from)
   ├── Rule 6(10) declaration check   → declarations found / ABSENT FROM CAPTURED TEXT
   └── package vs listing comparison  → per-field verdict, package | listing | method
```

The two guarantees that shape the implementation:

* a declaration absent from the **captured text** is reported as absent *from the capture* — never as
  "not displayed on the listing", because a listing page can carry declarations in a tab or a
  specification table the capture missed;
* a difference between the package and the listing is a **POTENTIAL INCONSISTENCY** with the
  comparison's own legal status attached. The application does not decide that a difference breaches
  any provision; it records the two readings and the method used to compare them (numeric for
  amounts, unit-normalised for quantities, two similarity thresholds for text — brand names tolerate
  spelling variants, entity names do not).

A price shown on a listing page is **not** recorded as the MRP declaration. The package extractor
deliberately offers a keyword-less currency amount as a weak MRP candidate; the listing reader drops
that candidate (the marker `UNANCHORED_REASON_MARKER` in `extraction/mrp.py` is the coupling) and
records the amount as listing-price evidence instead. A discounted listing price must never be
published as the retail sale price declaration.

## Placement analysis (`backend/rules/placement.py` + the `placement` check)

Rule 6(1) requires the declarations to be borne on the package or a label affixed to it, Rule 9
requires them to be legible and prominent, and Rule 7(1) allows a card or tape on a package of
10 cm³ or less to serve as the principal display panel. What a photograph can establish is which
**view** of the package each declaration was read from:

```
InspectionImage.role (FRONT / BACK / …)  +  ExtractedField.source_image_id
        ↓
build_panel_evidence()  → per-field view, roles supplied
        ↓
placement check → PASS (all watched declarations read from a panel-type view)
                | UNCERTAIN (only on a side/bottom view, split across views, roles unassigned,
                            or nothing detected) — NEVER FAIL
```

A shortfall never becomes a violation: the photographed surface cannot be certified as the principal
display panel as a matter of law, so the check says exactly that and asks for manual verification.

## Report status model

`ProductScan.review_status` (AI_PRELIMINARY / PENDING_FINALIZATION / FINALIZED) is the *stored*
review state. The report status is **derived** from the pipeline state and that review state on every
read (`repository_service.report_status`) rather than stored a second time, because a second column
could disagree with the two facts that produce it:

| Derived status | Condition |
|---|---|
| `DRAFT` | inspection created, pipeline not finished |
| `PROCESSING` | pipeline running |
| `ERROR` | pipeline failed |
| `AI_ANALYSIS_COMPLETE` | automated review finished, pass-oriented **preliminary** verdict |
| `NEEDS_MANUAL_REVIEW` | automated review finished and flagged something for a human |
| `PENDING_FINALIZATION` | automated review finished, application threshold not met |
| `FINALIZED` | an authorized official recorded the final decision |

`APPROVED`/`REJECTED` are the **outcomes of `FINALIZED`** (COMPLIANT/NON_COMPLIANT) and are never
used for an AI verdict, so a preliminary result cannot be read as an official one.

## Database (SQLite)

Tables: `users, organizations, products, inspections, inspection_images, ocr_results, field_candidates,
extracted_fields, classifications, rules, rule_versions, rule_evaluations, violations, evidence,
reports, review_actions, audit_logs, auth_sessions, product_scans, product_listings,
image_analyses`. Foreign keys ON, WAL mode. `field_candidates` retains every
scored candidate (winners and losers) so conflicts and audits remain reconstructable.
`product_scans` is the repository record per inspection (checks, violations, score, coverage,
AI verdict, official decision, finalizer, remarks) and `product_listings` holds captured listing
evidence with its extraction and last comparison. Schema changes are additive and automatic:
`Base.metadata.create_all` creates missing tables and `backend/migrations.py` adds missing columns
to existing ones.

## Security

JWT (HS256) with scrypt password hashing; every protected operation resolves a named **permission**
(`backend/authz/permissions.py`) — `inspections.view`, `inspections.manage`, `inspections.review`,
`analysis.run`, `compliance.view`, `compliance.finalize`, `listings.use`, `reports.generate`,
`rules.view`, `audit.view`, `tools.use`, … — and no router hard-codes a role check. Roles:
ADMIN / INSPECTOR / VIEWER plus ENFORCEMENT_OFFICER, REGULATED_ENTITY and INTERNAL_COMPLIANCE,
where the latter two are organisation-scoped in the query itself (a user in one organisation can
never read another's rows). Original roles preserved;
evidence files served only through `/files/{kind}/{name}` with path-traversal stripping and auth
via header **or** `?token=` (for `<img>`/download contexts); CORS restricted to the Vite origin;
uploads validated by extension + decoded format + size + dimensions + perceptual-hash duplicate
check; global exception handler prevents stack-trace leakage; every mutating action lands in
`audit_logs` (actor, action, before/after, reason, timestamp).

## Reprocessing & migration (`backend/services/reprocess_service.py`)

An inspection analysed by an older engine keeps that analysis until it is deliberately regenerated;
"newer software" is not the same claim as "the active result was produced by the current pipeline".

* **Engine stamp.** `backend/version.py` carries `ENGINE_VERSION` (behaviour, not release number)
  and a rule-set fingerprint computed from the active `rule_versions` (id, version, check type,
  weight, criticality, effective date). `process_inspection` stamps every result with it. A record
  whose stamp differs is *listed* for reprocessing, never silently rewritten.
* **In place.** `reprocess_inspection()` snapshots the record (fields with state/confidence, every
  rule result, violations, classification, scan score/coverage/verdict/review status, evidence
  counts), re-runs `process_inspection`, snapshots again, computes a structured diff and writes one
  `inspection_revisions` row (actor, reason, engine version, rule fingerprint, before/after/changes).
  `ProductScan.inspection_id` is unique and `refresh_scan` is idempotent, so a reprocessed
  inspection cannot become a second scan or a second product.
* **Preserved.** Manually corrected fields win over re-extraction, a recorded official decision is
  not discarded, a reviewer's CONFIRM/DISMISS/RESOLVE on a violation survives re-evaluation, and the
  previous result stays recoverable on the revision row. The audit log gains
  `inspection_reprocess_started` / `inspection_reprocessed` (or `…_failed`) entries.
* **Bulk runs.** One background worker at a time (`start_migration`), polling endpoints for
  progress, per-record outcomes and refusal of an overlapping run (HTTP 409). Nothing freezes the
  request thread.
* **Capabilities.** Single record: `inspections.manage`. Bulk migration: `users.manage`.
  Revision history: `inspections.view`, organisation-scoped like the inspection itself.

## Rule → evidence linking

`GET /inspections/{id}` returns `rule_evidence`: for every rule evaluation, the retained crops its
conclusion rests on. The mapping is data-driven — the field an evaluation measured
(`detail.detected_field`, placement `on_panel`/`off_panel`/`not_detected`) plus the rule version's
own declared `params.fields` — so an unrelated image is never attached to a conclusion. The
inspection page renders them as evidence chips that open the source image with the region
highlighted, alongside the raw OCR text, confidence and method.

## PRO — the in-application assistant (`backend/services/assistant_service.py`)

PRO is a retrieval assistant, not a text generator with a free hand:

* **Grounded.** Answers are assembled from the application knowledge base
  (`backend/assistant/knowledge.py`), the versioned rule data and the caller's own records. Nothing
  is invented; legal answers always carry their reference and state that the Official Gazette text is
  authoritative.
* **Record aware.** The client sends the record it has open (`context`: `path`, `inspection_id`,
  `scan_id`); `resolve_context()` re-resolves it **inside the caller's own scope**, so PRO can answer
  about the open inspection ("why does this need finalization?", "what evidence supports the MRP?",
  "what changed in the last reprocess?") and refuses — without describing anything — when the id is
  outside that scope.
* **Role aware.** Greeting, suggestions and capability statements follow the signed-in role and
  permissions; every reply carries PRO's name and a short role-aware sign-off.
* **Optional re-phrasing.** When a provider is configured, the already-grounded answer may be
  re-worded, and the re-wording is discarded if any figure changed (`retrieval_guard`).
