# POCKET — SIH26034 Requirement Traceability

**Problem statement SIH26034:** *"Software System to check compliance of Packaged Commodities
under Legal Metrology (Packaged Commodities) Rules, 2011 by scanning products, images and labels."*

Every row below is **implemented and testable** unless the Status column says otherwise. Nothing
in this table exists only in a slide deck: each feature names the module that implements it, the
test that verifies it, and the screen where a judge can see it. Genuine gaps are marked
`NOT IMPLEMENTED` with the reason — never hidden.

Status legend: **DONE** · **PARTIAL** (implemented, with a stated limitation) · **NOT IMPLEMENTED**

---

## 1. Required workflow (end to end)

| # | Requirement | Feature / Implementation | Test | Demo screen | Status |
|---|---|---|---|---|---|
| A1 | Scan/upload product images and labels | `backend/api/inspections.py` upload endpoint, extension/magic-byte/dimension/size validation, duplicate detection, per-image role (FRONT/BACK/LEFT/RIGHT/BOTTOM/CLOSE-UP) | `test_api.py::test_upload_*` | New Inspection | DONE |
| A2 | Preserve the original evidence | Originals written once to the evidence store with SHA-256 checksum, stored filename, timestamp and inspection link; processed images never replace originals | `test_api.py` upload tests; evidence store inspection | Inspection Detail → Evidence | DONE |
| A3 | Assess image quality | `backend/services/image_service.py` — resolution, blur (variance of Laplacian), exposure, contrast, glare, text density → GOOD / ACCEPTABLE / POOR / UNUSABLE with an honest score | `test_services.py` quality tests | New Inspection → quality feedback | DONE |
| A4 | OCR the image(s) | RapidOCR (bundled ONNX, CPU, offline) with multi-variant passes (original, upscaled, grayscale/contrast, targeted bottom-strip), OCR fusion with IoU dedupe, and bbox back-mapping to original coordinates | `test_robustness2.py` bottom-strip + bbox tests; `scripts/e2e_check.py` | Inspection Detail (raw OCR per field) | DONE |
| A5 | Extract structured declarations | `backend/extraction/*` — anchor + value + spatial relationship + text-type + context semantics across 23 tracked fields | `test_extraction.py`, `test_robustness2.py`, `test_upgrade.py` | Inspection Detail → Declarations | DONE |
| A6 | Normalize values | `backend/normalization/*` — MRP/currency, quantity+unit+type, dates with ambiguity flags, phone, email, website; raw value always preserved alongside the normalized one | `test_services.py` normalizer tests | Inspection Detail (raw vs normalized) | DONE |
| A7 | Classify the commodity | `backend/classification/*` — category inference with a recorded signal trail and DETECTED / UNCERTAIN / CONFLICTING state | `test_services.py`, `test_upgrade.py` | Inspection Detail → Signals | DONE |
| A8 | Determine applicable requirements | `backend/rules/applicability.py` per-rule gates (imported vs domestic, category, pack type) | `test_rules.py` | Inspection Detail → Rules (`NOT_APPLICABLE` rows) | DONE |
| A9 | Evaluate the legal requirements **deterministically** | `backend/rules/engine.py` + `decision.py` — versioned rules, PASS / FAIL / UNCERTAIN / NOT_APPLICABLE; no AI in the decision path | `test_rules.py` | Inspection Detail → Rules | DONE |
| A10 | Show evidence for every result | Every detected field carries image_id, bbox (original-image coordinates), raw OCR text, source pass/variant, confidence; viewer highlights the source region | `test_robustness2.py` bbox mapping; live EvidenceViewer verification | Inspection Detail → Evidence Viewer | DONE |
| A11 | Surface uncertainty and conflicts | `FieldState.DETECTED/UNCERTAIN/CONFLICTING/MISSING`; conflicts are never auto-resolved and are queued on the Dashboard | `test_upgrade.py` conflict tests; `test_api.py` dashboard test | Dashboard → "Conflicts requiring resolution"; Inspection Detail | DONE |
| A12 | Human review | Correct a field, confirm absence, reject, add a field, resolve a conflict, add a note, decide a violation, finalize — each audited with actor/time/reason/before/after | `test_api.py` review + violation lifecycle tests | Inspection Detail → Manual review | DONE |
| A13 | Final compliance decision | COMPLIANT / NON_COMPLIANT / NEEDS_MANUAL_REVIEW; a human finalization supersedes and is recorded as the human's call | `test_rules.py`, `test_api.py` | Inspection Detail → Result banner | DONE |
| A14 | Report generation | PDF (ReportLab, evidence-referenced), CSV (structured fields + states + confidence), JSON (complete machine-readable record) | `test_api.py` export tests; `scripts/e2e_check.py` | Inspection Detail → Reports | DONE |
| A15 | Audit everything | Append-only `audit_logs` table for every mutating action; ADMIN-only read API + Audit Log screen; no edit/delete path exists | `test_api.py::test_audit_log_admin_only_and_append_only` | Audit Log | DONE |
| A16 | Combine evidence across several images of ONE package | All images of an inspection are processed and fused field-by-field (strongest reading wins); disagreeing images stay CONFLICTING | `test_extraction.py` cross-image aggregation tests | New Inspection (multi-image) → Inspection Detail | DONE |
| A17 | Handle declaration wording/layout variation without hardcoding | Anchor + value + spatial relationship + text type + context; composite quantities, identifier-aware numeric correction, entity/wordmark separation | `test_extraction.py`, `test_golden_britannia.py`, `test_robustness2.py` | Inspection Detail | DONE |
| A18 | Be honest about accuracy | Real-image metrics from human ground truth only; synthetic variation harness reported separately; explicit "insufficient data" instead of a percentage when the sample is too small | `test_dataset.py`, `scripts/dataset.py validate`, `scripts/run_benchmark.py` | `docs/SCANNING_ACCURACY_REPORT.md` | DONE |
| A19 | Measurable dataset for judging/training variance | `data/` architecture (raw / annotations / splits / benchmark / difficult_cases), product-level splits with leakage validation, ingest tooling | `test_dataset.py` (15 tests) | `data/README.md`, `scripts/dataset.py` | DONE |
| A20 | The demo must not fail silently | `/health` liveness probe; launcher waits for the API; dev proxy returns an actionable 503; login screen shows a live backend status banner and a retry | manual + live verification (`curl`, Preview tab) | Login screen, `start.bat` | DONE |

---

## 2. Declaration coverage (Rule 6 / 9 / 13, 2011 Rules as amended)

| Declaration | Tracked field | Rule | Status |
|---|---|---|---|
| Name & address of manufacturer / packer / importer | `manufacturer`, `manufacturer_address`, `packer`, `packer_address`, `importer`, `importer_address` | 6(1)(a) | DONE |
| Common or generic name | `common_name` | 6(1)(b) | DONE |
| Net quantity | `net_quantity` | 6(1)(c) | DONE |
| Month & year of manufacture / pre-packing / import | `date_manufacturing`, `date_packing`, `date_import` | 6(1)(d) | DONE |
| Retail sale price (MRP), kept separate from unit sale price | `mrp`, `unit_sale_price` | 6(1)(e) | DONE |
| Consumer care details | `consumer_care_phone`, `consumer_care_email`, `website` | 6(1)(f) | DONE |
| Best before / use by / expiry | `date_best_before`, `date_expiry` (duration-from-manufacture kept semantically distinct from a date) | 6(1)(g) | DONE |
| Country of origin (imported) | `country_of_origin` | 6(1)(h) | DONE, applicability-gated |
| Legibility | legibility evaluation over image quality + OCR confidence | 9(1) | PARTIAL — legibility is assessed from image/OCR signals; **physical font height in millimetres requires a calibrated scale and is a manual check** |
| Batch / lot number | `batch_lot` | 13(1) | DONE |
| Standard pack sizes | — | 5 | MANUAL_ONLY rule (declared in the library, routed to human verification) |
| Exemptions applicability | — | 4 | MANUAL_ONLY rule |
| Permissible errors in net quantity | — | 7 | MANUAL_ONLY rule — **a photograph cannot establish actual quantity** |
| FSSAI licence | `fssai_license` | (FSSAI labelling, surfaced as evidence) | DONE |

---

## 3. Declared scope boundaries (honest limitations)

| Item | Status | Reason |
|---|---|---|
| Physical weight / volume of the contents | NOT IMPLEMENTED (by design) | Requires a calibrated weighing/measuring instrument; a photograph cannot establish it. Routed to manual verification (Rule 7 is MANUAL_ONLY). |
| Physical font height in millimetres | NOT IMPLEMENTED (by design) | Requires a known scale reference in the image; pixel measurements alone cannot prove a millimetre figure. |
| Price actually charged at the point of sale | NOT IMPLEMENTED (by design) | Not decidable from a label image. |
| Declaration authenticity / tamper detection | NOT IMPLEMENTED | Out of scope of image-based inspection assistance. |
| Packer / importer registration number as a structured field | NOT IMPLEMENTED | The value is often visible and is OCR-readable, but no dedicated extractor/field exists yet; noted in `LIMITATIONS.md`. |
| Indic-script (Tamil/Hindi/…) declaration *semantics* | PARTIAL | Script detection + multilingual duplicate-evidence handling exist; extraction patterns are English-first. Extending the field-variation schema to more scripts is designed-for but not yet populated. |
| OCR-based automatic FAIL on missing declaration | NOT IMPLEMENTED (by design) | A missing detection is `MISSING`/`UNCERTAIN` and routes to human review. Only positive evidence — including a human-confirmed absence — can produce a FAIL. |
| Real-world accuracy percentage | NOT CLAIMED | Requires human ground truth in `data/benchmark/`. Current report says **INSUFFICIENT DATA**. Only synthetic/combinatorial robustness numbers are published, clearly labelled as synthetic. |

---

## 4. Robustness / scale claims (stated precisely)

| Claim | Reality |
|---|---|
| "Handles a very large variety of layouts and formats" | Semantic extraction over a declarative field-variation schema (aliases, abbreviations, OCR-confusable variants, units, formats) — recognition widens by widening the schema, not by adding per-product code. |
| "Millions of possible variations" | A **seeded combinatorial generator** (`backend/testing/variations.py`) can emit millions of declaration variations; the published harness run evaluates **3,600 samples** (seed 42) per report. Generated samples are **synthetic**, never presented as real photographed products. |
| "10M+ real product images" | **Not claimed and not true.** No real image dataset of that size exists in this project. |
| Accuracy | Only synthetic harness rates (detection 43.9%, false-assignment 4.64%) plus per-inspection evidence. No real-world accuracy is asserted. |

---

## 5. Security & access control

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| Authentication | PyJWT bearer tokens, bcrypt-hashed passwords, `/auth/login`, `/auth/logout`, `/auth/me` | `test_api.py` auth tests | DONE |
| Role-based access (ADMIN / INSPECTOR / VIEWER) | `require_role` dependency on every mutating endpoint | `test_api.py` role tests | DONE |
| Evidence files not publicly readable | `/files/{kind}/{filename}` requires a valid token; path traversal blocked | `test_api.py::test_files_endpoint_*` | DONE |
| Global audit trail is privileged | `/audit` is ADMIN-only | `test_api.py::test_audit_log_*` | DONE |
| Upload validation | Extension + magic-byte + dimension + size checks, duplicate rejection, unsafe filename handling | `test_api.py` upload rejection tests | DONE |
| No paid/cloud services | RapidOCR + OpenCV + ONNX Runtime + SQLite + ReportLab, all local; the optional vision provider is off by default and needs no new dependency | `scripts/e2e_check.py` runs fully offline; `test_ai_layer.py` asserts no provider call happens without a key | DONE |

---

## 6. SIH proposal features (the solution as pitched)

Requirements taken from the SIH26034 solution description. Where a capability genuinely depends on
a human, it says so — nothing is presented as automated that is not.

| # | Requirement | Feature / Implementation | Backend | Frontend | API | Test | Demo step | Status |
|---|---|---|---|---|---|---|---|---|
| B1 | Scan front, back and side labels | One inspection accepts many images with a per-image role (FRONT / BACK / LEFT_SIDE / RIGHT_SIDE / TOP / BOTTOM / CLOSE_UP / ADDITIONAL_EVIDENCE); declarations are fused across all of them | `services/inspection_service.py` (multi-image candidate pool + cross-image fusion) | New Inspection → per-image role selector; Inspection Detail → package evidence gallery | `POST /inspections/{id}/images` | `test_api.py` upload tests, `test_upgrade.py` fusion | Upload 3 faces of one product, one inspection | DONE |
| B2 | AI-powered extraction (MRP, net quantity, dates, ingredients, manufacturer, nutrition) | On-device RapidOCR multi-pass as the always-available reader; optional vision provider as a second perception source | `ocr/*`, `extraction/*`, `ai/extractor.py` | New Inspection → declarations with confidence | `POST /inspections/{id}/process`, `GET /ai/status` | `test_ai_layer.py`, `test_extraction.py` | Scan a pack; see the vision stage run or be marked *skipped* | DONE (vision **optional**, OCR always on) |
| B3 | Configurable, versioned compliance rule engine | Rules + versions are database rows seeded from a registry; each evaluation carries rule number, requirement, observed evidence, reason and a `NOT_APPLICABLE` gate; nothing is hard-coded in the UI | `rules/registry.py`, `rules/applicability.py`, `rules/engine.py`, `models/rules.py` | Rule Library (searchable, versioned); Inspection Detail → rule-by-rule with reasons | `GET /rules`, `GET /rules/{rule_id}`, `GET /inspections/{id}` | `test_rules.py` | Rule Library → open a rule → see its versions | DONE |
| B4 | Compliant / Needs Verification / Potential Non-Compliance | Four deterministic rule outcomes (PASS / FAIL / UNCERTAIN / NOT_APPLICABLE) aggregated into COMPLIANT / NON_COMPLIANT / NEEDS_MANUAL_REVIEW; UNCERTAIN is never collapsed into FAIL | `rules/engine.py`, `rules/decision.py` | Decision banner + per-rule badges (always icon **and** text) | `GET /inspections/{id}` | `test_rules.py`, `test_upgrade.py` | Finalize a decision on an inspection | DONE |
| B5 | Evidence for every finding | Each field stores image, bbox (original-image coordinates), raw OCR text, engine, preprocessing variant, confidence and reason; a candidate offered for review also keeps its source region with a `note` | `services/evidence_service.py`, `models/evidence.py` | Inspection Detail → Evidence viewer (bbox overlay, crop, raw text, reason) | `GET /files/{kind}/{name}` | `test_api.py` evidence-completeness tests | Click a field → the region is highlighted on the photo | DONE |
| B6 | Bill Scanner: compare the billed price with the MRP | Bill image stored as immutable evidence; the comparison is arithmetic performed in Python on stored values; a gap is reported as a **potential** price difference with the amount and percentage | `api/tools.py`, `services/tools_service.py`, `models/tools.py` | Bill Scanner (upload → verify reading → comparison strip → raise complaint) | `POST /bills/scan`, `POST /bills`, `PATCH /bills/{id}`, `GET /bills` | `test_tools.py` | Bill Scanner → add a bill → see ₹ difference | DONE |
| B7 | Grocery: expiry alerts **only** for Grocery-added products | `grocery_items` rows are written only by an explicit user action; alert state (FRESH / EXPIRING_SOON / EXPIRED / NO_EXPIRY_DATA) is computed live from the tracked date; a duration without an anchor is not converted into a date | `api/tools.py`, `services/tools_service.py` | Grocery (add form, alerts card, days remaining, inline date fix) | `POST /grocery`, `GET /grocery`, `PATCH /grocery/{id}`, `DELETE /grocery/{id}` | `test_tools.py` | Grocery → add the scanned product → alerts appear only for it | DONE |
| B8 | Complaint creation and tracking | Complaint with product/inspection/bill linkage and an append-only JSON timeline; status transitions role-gated (INSPECTOR/ADMIN) and audited | `api/tools.py`, `services/tools_service.py`, `models/tools.py` | Complaint Center (file form, lifecycle timeline, next-step action) | `POST /complaints`, `GET /complaints`, `POST /complaints/{id}/advance` | `test_tools.py` | File a complaint from a bill → move it to Under review | DONE |
| B9 | Poor / blurred image handling and small or complex text | Quality assessment before extraction (blur, exposure, glare, text density) with honest GOOD/ACCEPTABLE/POOR/UNUSABLE status and capture guidance; multi-variant preprocessing + targeted multi-pass OCR with conditional escalation | `preprocessing/quality.py`, `preprocessing/enhance.py`, `ocr/multipass.py` | New Inspection → capture guidance + per-image quality; Inspection Detail → quality table | `GET /inspections/{id}` | `test_services.py` quality tests, `test_robustness2.py` escalation policy | Upload a dark/blurred image; quality is reported as poor | DONE |
| B10 | Low-cost / offline architecture | CPU-only: RapidOCR (ONNX), OpenCV, SQLite, ReportLab. No key, no cloud, no paid API needed; the optional vision provider is the only outbound call and is off by default | whole backend | whole app | — | `scripts/e2e_check.py` (offline end-to-end) | Run the demo with the network cable out | DONE |
| B11 | Mobile-friendly capture | Responsive layout tiers (desktop / tablet icon rail / phone) with touch-sized controls; the inspection workspace reflows to a single column | — | `frontend/src/styles.css` breakpoint tiers; verified in the browser at 656px and desktop widths | — | live UI verification | Resize the browser / open on a tablet | DONE |

### Supported, with human verification (stated honestly)

| Capability | Why a human is required | How the product handles it |
|---|---|---|
| Actual physical quantity / net content | An image shows a printed declaration, not the contents of the pack | Printed declaration is extracted; the physical check is a `MANUAL_ONLY` rule and is never auto-passed |
| Price actually charged, and whether a gap is lawful | Image evidence cannot establish the transaction or its explanation | Bill Scanner reports a *potential* price difference and routes it to verification |
| Font height in millimetres | Requires scale calibration; images provide pixels | Declared `MANUAL_ONLY`, with the reason shown |
| Whether a declaration is genuinely absent from the product | Only an inspector can verify an unphotographed or poorly captured face | `MISSING` (not detected in supplied images) vs `HUMAN_CONFIRMED_ABSENT` (inspector verified) are distinct states, and only the latter can support a violation |
| Exact legal interpretation of an exemption or permissible error | Depends on the pack and the applicable amendment | `UNCERTAIN` + manual review; never a fabricated FAIL |

### Not implemented (and not claimed)

| Item | Status |
|---|---|
| Trained/measured declaration-recognition accuracy percentage | **NOT CLAIMED.** The real-image benchmark reports INSUFFICIENT DATA until a ground-truthed set exists; unit and synthetic checks are reported separately and never blended in |
| Retail price feed / POS integration for bill verification | NOT IMPLEMENTED — the bill image and its stored values are the evidence |
| Automatic determination of legal guilt | NOT IMPLEMENTED by design: the platform prepares evidence and evaluations for an authorized officer |
