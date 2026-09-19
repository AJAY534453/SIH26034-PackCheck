# PROGRESS LOG — POCKET engineering trail

Factual entries only: files changed, tests run, results, remaining issues.
(UI-specific phases live in `UI_PROGRESS_LOG.md`; this file covers the platform.)

---

## Real-package extraction pass — the actual three-surface upload (complete)

Driven by the stored OCR of inspection 37 (three surfaces of one real biscuit pack). Every defect
below was reproduced from that evidence before being fixed, and is now covered by
`backend/tests/test_golden_real_multiview.py` (fixture = the stored OCR lines, misreads included)
and `backend/tests/test_real_world_fixes.py`.

**Defects found on the real photos (before → after)**

| Field | Before | After | Root cause fixed |
|---|---|---|---|
| product_name | `BRITANNIA INDUSTRIESLTD.` | `CLASSIC SWEET &SALTY` | company-entity detection failed on OCR-fused words; the stacked title (`CLASSIC`/`SWEET`/`&SALTY`, split by a promo tag) was never merged |
| manufacturer | MISSING | `BRITANNIA INDUSTRIESLTD.` (UNCERTAIN, role unresolved) | entity with no readable role anchor was dropped |
| net_quantity | `28.4 g` (CONFLICTING) | `34.5 g (declared: 28.4g+6.1gEXTRA-34)` | the unit run into the next word hid `6.1g`; a printed total without a unit was unreadable to the parser |
| batch_lot | `OTHER` | `B07269L` | a bare word beside `LOT No.` was accepted as a code; the real code sits in the value column |
| date_packing / date_expiry | `2027-01-19` / unresolved | `2026-07-20` / `2027-01-19` | two-column date block cross-wired by OCR row offsets |
| brand | CONFLICTING | `BRITANNIA` UNCERTAIN | one printed word was asserted as both brand and product title |
| consumer_care_email | DETECTED `...@briindio.com` | UNCERTAIN (domain uncorroborated) | structural validity was treated as correctness |

Result on inspection 37: **11 detected / 0 conflicts / 2 review items**, decision NEEDS_MANUAL_REVIEW,
rule 6(1)(b)–(e),(g) PASS and 6(1)(a),(f) UNCERTAIN (no declaration became a FAIL because OCR missed it).

**Also in this pass**

- `process_inspection(..., refresh_ocr=False)`: stored OCR evidence is reused by default
  (deterministic reprocess; 37 reprocessed in **1.6 s** instead of ~60 s), with
  `POST /inspections/{id}/process?refresh_ocr=true` to recognise again.
- Unanchored company entities are offered with `role_uncertain` → UNCERTAIN + explicit reason.
- Email-domain corroboration (`backend/normalization/validators.py`) wired into the pipeline.
- Synthetic harness (seed 7, 3,600 samples): detection 46.7% → **47.4%**, false-assignment
  4.92% → **5.19%**; manufacturer 50.5% → 67.5%, batch 27.0% → 23.5%. Trade-off documented in
  `BASELINE_METRICS.md`; the fixes were driven by real-photo evidence, not by the harness.
- Gates: `pytest backend/tests` 199 tests (198 passed +1 OCR-dependent test that is intermittent
  under host memory exhaustion and passes in isolation), `tsc --noEmit` clean, `npm run build`
  clean, `scripts/e2e_check.py` OK, dataset validation clean.

**Remaining genuine limitations** (see `docs/LIMITATIONS.md`): the food-manufacturer address block
on that pack is unreadable in the supplied photos (manufacturer_address stays MISSING); the
recogniser needs free host memory; and no public dataset of Legal Metrology annotations exists, so
real-image accuracy is measured only on the annotated products in `data/`.

---

## Engine upgrade — evidence semantics & safety invariants (complete)

- Non-detection → **UNCERTAIN**, never legal FAIL; FAIL requires positive or human-confirmed
  evidence (`backend/rules/engine.py`). Decision: any confirmed FAIL → NON-COMPLIANT;
  unresolved uncertainty → NEEDS_MANUAL_REVIEW (`backend/rules/decision.py`).
- Semantic manufacturer extraction: "Marketed by"/"Manufactured by" are anchors, never values
  (`backend/extraction/manufacturer.py`); brand/product/common-name separation
  (`backend/extraction/product_identity.py`); empty fields carry no confidence
  (`backend/extraction/dates.py`).
- Pipeline: MISSING rows persisted for all tracked fields, reprocess FK-order cleanup,
  marketer promotion with provenance, evidence crops for CONFLICTING fields, reusable
  `evaluate_and_persist_rules` (`backend/services/inspection_service.py`).
- New `CONFIRM_ABSENT` review action → audited, deterministic re-evaluation → CONFIRMED
  violation + NON-COMPLIANT (`backend/services/review_service.py`).
- Reports (PDF/CSV) distinguish DETECTED / MISSING / UNCERTAIN / CONFLICTING /
  HUMAN-CONFIRMED-ABSENT (`backend/services/report_service.py`, `backend/api/inspections.py`).
- **Tests:** 78 existing + 23 new regression tests = **101/101 pass**.

## Robustness framework (complete)

- `backend/extraction/field_schema.py` — knowledge layer of declaration representations;
  anchors derived from schema (widening schema widens recognition; no per-product hard-coding).
- `backend/ocr/textnorm.py` — numeric OCR-error recovery (O↔0, I↔1/l, S↔5, B↔8, G↔6, Z↔2),
  field-scoped, raw provenance always preserved.
- `backend/extraction/association.py` — bbox/label-value association (split "M.R.P." / "₹200.00"
  rejoined; currency-bridge lines supported).
- `backend/ocr/multipass.py` + pipeline wiring — confidence-gated targeted preprocessing passes.
- `backend/normalization/validators.py` (phone/email/website/FSSAI/quantity structure),
  `backend/normalization/consistency.py` (date ↔ duration temporal coherence).
- `backend/ocr/multilingual.py` — script detection; same-field cross-script duplicates
  reinforce instead of conflict (false-conflict count in harness: 0).
- `backend/testing/variations.py` — seeded streaming combinatorial generator (>10M enumerable
  space, verified); `backend/testing/robustness.py` — per-field metrics, corruption-stratified
  failure classification, markdown report. Current synthetic numbers in `BASELINE_METRICS.md`.
- **Tests:** suite stayed 101/101 green throughout.

## Known open gaps (honest list, from the harness)

1. Weak synthetic detection on phone-without-keyword, unlabeled batch codes, plain-value dates
   (partly by design — evidence-first policy rejects unanchored guesses), country-of-origin
   variants ("Produce of", "Made in" below-line).
2. Real-image benchmark not yet run (methodology documented; no accuracy claims made).
3. Legacy `Badge`/`Conf` frontend exports pending mechanical migration to `ui.tsx`.

---

## Hotfix — full-width application shell (this session)

**Reported defect (1366×768):** content compressed toward the right, huge blank area between
sidebar and content, topbar stopping mid-page.

**Root cause (shell-level, not Dashboard):** `.app-shell { display: flex }` row + fixed sidebar
(out of flow) made `.topbar` and `.main` side-by-side flex items; topbar shrank to content
width; `.main`'s `max-width: 1400px` worsened the squeeze.

**Changed:** `frontend/src/styles.css` only —
1. `.app-shell` → `flex-direction: column` (topbar and main stack; sidebar stays fixed).
2. `.main` → removed `max-width` so content fills all remaining width.

**Verified (live DOM + screenshots at 639px preview width):**
- sidebar→content gap: **0px**; main starts immediately after sidebar
- topbar spans sidebar edge → viewport right edge (diff < 2px)
- Dashboard and Inspection Detail fill available width; no horizontal scroll
- `tsc --noEmit` clean; production build passes; backend suite untouched (still 101/101).

**Not changed:** backend, database, APIs, OCR, rule engine, authentication, evidence system,
business logic, any component markup.

---

## OCR/extraction robustness build — executing the approved plan (complete)

Approved plan executed in full. Real diagnostic target: INS-2026-000017 (Sukku Kaapi panel photo,
image id 17). Baseline at start of build: 101/101 green.

**Root causes found (verified against the stored OCR, not guessed):**
1. Nutrition flags were computed on pre-sort line order while extractors sorted internally →
   flags pointed at the wrong lines (Customer Care line flagged as nutrition) — the single
   biggest extraction killer on real labels. Fixed with one canonical engine-side sort
   (`backend/extraction/engine.py`).
2. Manufacturer below-anchor walk broke on any non-entity line (e.g. `(approx)` nutrition
   fragment from another column) and on fused company tokens. Fixed with column guard,
   fused-entity recognition, skip-don't-break semantics, entity/address dedupe
   (`backend/extraction/manufacturer.py`).
3. Fused currency amounts (`M.R.P.` + `200.0010.4091`) unparsed. Fixed with anchor-present
   binding + decomposition (`backend/extraction/mrp.py`).
4. Batch mis-pairing / label-as-value (`Batch No` as its own value). Fixed in
   `backend/extraction/identifiers.py`.
5. Quantity junk-prefix correction gap (`NetWeight i5o0g`). Fixed via unit-terminated-token
   extension in `backend/ocr/textnorm.py` (leading noise letter DROPPED, never mapped to a
   digit — no fabricated 1500g; ledger records `('i','')`); closed the `Blog`→`810g` hole by
   requiring a real digit in unit-terminated tokens.
6. Product-name mis-attribution (entity line won; then narrative/quote/contact lines won).
   Fixed in `backend/extraction/product_identity.py`: role-anchor lookback (noise-tolerant,
   vertical-proximity bound), narrative/sentence-structure/contact/quoted-line exclusions,
   progressive title-height prior.
7. No targeted pass for the tiny glare-affected bottom declaration panel. Fixed in
   `backend/ocr/multipass.py`: conditional bottom-strip pass at 2× upscale with exact
   bbox back-mapping to original coordinates (`y0 + y/scale`), escalation only when critical
   declarations are still missing.

**Files changed:** `backend/extraction/engine.py`, `manufacturer.py`, `mrp.py`, `quantity.py`,
`identifiers.py`, `product_identity.py`, `base.py`; `backend/ocr/textnorm.py`, `multipass.py`.
**Not touched:** rule engine, auth/RBAC, DB schema, frontend, evidence contract.

**Golden regression:** `backend/tests/fixtures/golden_sukku_ocr.json` (162 stored OCR lines +
expected fields — expectations live ONLY in the fixture; `test_golden_expected_values_live_only_in_fixture`
asserts extraction code carries no product-specific constants).

**Tests:** 101 existing + 24 new = **125/125 pass**. Frontend `tsc --noEmit` clean, production
build passes, 33 evidence rows for image 17 all carry valid bboxes (EvidenceViewer contract intact).

**Real before/after (INS-2026-000017):** DETECTED fields 4 → 10 (FSSAI 0.90, phone 0.88,
email 0.88, website 0.90, MRP 0.69, net_quantity 0.87 [500 g], batch 0.81, best-before 0.86,
manufacturer 0.86, product_name 0.83). Manufacturing/expiry dates remain honestly UNCERTAIN
(ambiguous inkjet `040225`); 12 fields MISSING (no evidence — never auto-absent). Decision:
NEEDS_MANUAL_REVIEW, status AWAITING_REVIEW.

**Synthetic harness (seed 42, 3,600 samples):** detection 43.8% (baseline 43.6%),
false-assignment 4.64% (baseline 5.28%, −0.64pt), raw-match 13.7%. Anchor-driven fields strong
(MRP 77.5%, unit price 68.5%, net qty 63.5%); date/batch/phone remain deliberately conservative.
Synthetic numbers are never blended with real-image results.

**Remaining known issues:** packer-registration field not implemented (new field, out of approved
file scope); harness date/phone/batch detection still low (honest conservatism, documented);
phone-without-keyword and unlabeled-batch gaps unchanged; real-image benchmark still requires
human ground truth in `data/benchmark/`.

---

## Session-recovery verification (complete)

The build session was resumed after an interruption; a state-recovery pass confirmed all work
survived on disk (tests, fixture, docs, DB) — nothing was redone. Remaining item was live UI
verification of the improved backend data:

- Backend 8001 up; Vite dev server restarted on 5174; logged in as admin.
- **Inspection Detail (INS-2026-000017):** renders 10 DETECTED rows with confidence + source
  provenance (`rapidocr + upscaled`, `assoc:upscaled`), 2 honest UNCERTAIN rows with
  "anchor observation only" explanations, 11 NOT-DETECTED-IN-SUPPLIED-IMAGES rows with the
  missing-≠-absent disclaimer and `Confirm absent` (human-only) actions.
- **EvidenceViewer field→region highlight verified numerically:** selecting `mrp ₹ 200.00`
  draws the blue rect at fractions (0.352, 0.905, 0.557, 0.031) vs the expected union of the
  MRP label+value bboxes (0.351, 0.907, 0.559, 0.031) — all within 0.002. Bbox back-mapping
  through upscaled/associated passes is pixel-correct in the live UI.
- **Rule engine:** 6(1)(a,b,c,e,f,g), 9(1), 13(1) PASS on recovered evidence; 6(1)(d) UNCERTAIN
  (honest — ambiguous inkjet date); 5 and 4 UNCERTAIN (manual-only by design); 6(1)(h) NOT
  APPLICABLE. Deterministic layer untouched.
- Full backend suite re-run in this session: **125/125 pass**. No code changes were needed
  during recovery.

---

## SIH26034 readiness pass (complete)

Audited the whole repository against the official problem statement, closed real gaps, and re-ran
every gate. No rebuild, no mocks, no fabricated numbers.

### Gaps found and closed

1. **Violation human-decision lifecycle was missing.** The `ViolationStatus` enum already had
   CONFIRMED/DISMISSED/RESOLVED but nothing could ever set them, so an inspector could never agree
   with or reject a finding — a core SIH requirement.
   - `backend/services/review_service.py`: `review_violation()` — reason required for
     DISMISS/RESOLVE, audited, then re-decides the inspection via the deterministic engine.
   - `backend/services/inspection_service.py`: human decisions are **durable** across
     re-evaluation (captured by rule number before the delete/recreate, then re-applied) and the
     evaluation deletes now use `synchronize_session="fetch"` so no stale instance survives.
   - `backend/api/inspections.py`: `POST /inspections/{id}/review/violation/{violation_id}`.
   - `frontend/src/pages/InspectionDetail.tsx`: Confirm / Dismiss / Resolve actions per violation
     with a reason dialog that explains the consequence.
   - Fixed a real bug found while testing: after re-evaluation the in-session violation object was
     detached (`InvalidRequestError: Instance is not persistent within this Session`) — the
     response now re-queries the live row.
   - Verified: dismiss ⇒ NEEDS_MANUAL_REVIEW, confirm ⇒ NON_COMPLIANT, dismissal survives a later
     review action, viewer gets 403, missing reason gets 422.

2. **Audit trail was write-only.** Every action was already recorded (`audit_logs`) but there was
   no way to read it — unusable for compliance.
   - `backend/api/meta.py`: `GET /audit` (ADMIN-only, filters by action/actor/inspection, paged,
     append-only — no write method exists).
   - New `frontend/src/pages/AuditLog.tsx` + route + ADMIN-only nav entry.
   - `frontend/src/pages/Inspections.tsx` now reads `?search=` so audit rows deep-link into the
     inspection list.

3. **Conflicts were invisible on the Dashboard** even though the evidence layer flags them.
   Added `conflicts`, `open_violations`, `confirmed_violations` counts and a
   `conflict_queue` (inspection + conflicted field names) to `/dashboard/stats`, plus
   "Conflicting Declarations" / "Open Findings (Unconfirmed)" tiles and a
   "Conflicts requiring resolution" card.

4. **Batch value and address field quality.** On the golden inspection the stored batch value was
   `Batch No 90130` (the label leaked into the value) and `manufacturer_address` was empty while
   the address sat inside `manufacturer`.
   - `backend/extraction/identifiers.py`: the next-line fallback now strips a repeated
     batch/lot anchor from an association-synthesized line, and records combined provenance.
   - `backend/extraction/manufacturer.py`: the address is **additionally** offered as its own
     `{role}_address` candidate (additive — the entity value keeps the full printed block, so no
     existing behaviour or test was changed).
   - Golden inspection re-run: `batch_lot = 90130`, `manufacturer_address = DETECTED`.
     Detected fields **10 → 11**.

5. **E2E script only covered the happy path.** `scripts/e2e_check.py` now walks the full demo:
   evidence bbox coverage, human review (edit + note), the violation lifecycle including the
   safety principle (a human-confirmed absence is the ONLY image-side path to a rule FAIL),
   final decision, PDF, CSV, JSON export, audit trail summary and auth-gated evidence access.

### Documentation
- New `docs/SIH26034_TRACEABILITY.md` (requirement → feature → test → screen, with honest
  NOT IMPLEMENTED markers) and `docs/API_DOCUMENTATION.md` (all endpoints, roles, payloads,
  field states, error codes).
- Updated `README.md` (roles/audit section, doc index, test counts), `docs/TESTING.md`,
  `docs/LIMITATIONS.md`, `BASELINE_METRICS.md`.

### Gates (all re-run in this session)
- Backend: **130/130 pass** (was 125; 5 new regressions, none removed or weakened).
- Frontend: `tsc --noEmit` clean; production build passes.
- Synthetic harness (seed 42, 3,600 samples): detection **43.9%**, false-assignment **4.64%**
  — no regression from the new extraction changes.
- Real-image benchmark: still **INSUFFICIENT DATA** (no human ground truth added) — no accuracy
  is claimed, as required.
- Live verification: Audit Log page renders 122 real entries; Dashboard shows the real conflict
  (`INS-2026-000007` / `fssai license`); Inspection Detail renders the violation action buttons.

## Extraction-robustness pass (resumed after interruption) — dates, brand, entity display, candidate evidence

### What was already complete when this session resumed
The OCR/diagnostic pass, the multi-pass escalation, the nutrition-flag/line-order root-cause fix,
the MRP/quantity/batch/manufacturer guards, the golden fixture + no-hardcoding guard and the first
24 robustness tests were all on disk and green. This pass finished the remaining items and then
verified them live.

### Root causes found and fixed in this pass
1. **Ambiguous dates were being stored as DETECTED.** An inkjet code such as `040225` has no
   provable day/month order; the value is now offered with state `UNCERTAIN` and an explicit
   reason, so a date assumption can never reach the rule engine as a fact.
2. **A correct bottom-strip read lost a dedupe contest** to a wrong full-image read of the same
   region (`040225` vs `04702725`). `merge_lines` now keeps disagreeing digit reads of the same
   region as separate candidates instead of silently collapsing them — the disagreement surfaces
   as an ambiguity the inspector resolves.
3. **Duration declarations rendered as raw JSON** (`{"type": "DURATION_FROM_REFERENCE", ...}`).
   A structured declaration now normalizes to a human-readable display
   ("12 months from the date of manufacture") while keeping the canonical value.
4. **Brand had no evidence path at all** for a logo (typography is not a labelled declaration).
   A brand-mark detector now reads prominent uppercase text in the label's top band, requires
   corroboration by a repeat of the same token elsewhere, and marks the result `inferred` — so it
   is offered at UNCERTAIN and never asserted.
5. **Entity/address display retained OCR presentation defects** (`19/115,,KonguNagar,MuthurRoad`).
   A text-block normalizer tidies word joins, repeated commas and the PIN-code hyphen for display
   only; `raw_value` is asserted unchanged and ALL-CAPS fused strings are never split.
6. **Review candidates had no retained evidence region** — an UNCERTAIN value could be shown with
   no way to see where it came from. Evidence is now retained for any field that offers a value,
   with a `note` marking it a review candidate, exposed through the API and shown in the UI.

### Verification (all re-run in this session)
- Backend: **141/141 pass** (was 131; +10 regressions, none removed or weakened).
- Frontend: `tsc --noEmit` clean; production build passes.
- E2E: `scripts/e2e_check.py` full workflow OK, and its evidence check now reports
  "every detected field carries evidence: OK" (was reporting a gap before fix 6).
- Harness (seed 42, 3,600 samples): detection **47.7%**, false-assignment **4.94%**. Reading
  separator-less date codes cost +0.30pp false-assignment for +3.8pp detection — a documented
  trade, with the recovered dates routed to review rather than asserted.
- Golden panel INS-2026-000017: **11 DETECTED / 3 UNCERTAIN / 9 MISSING**, 91/91 evidence rows with
  valid bboxes. Live UI verified: manufacturer and address render normalized and separate, brand
  shows `GURUCHARA · UNCERTAIN · review candidate` with its real logo crop, and selecting brand
  draws the highlight rect at (3.180%, 21.0%, 10.544%, 2.5%) against the expected fractions from
  bbox (38,336,164,376) on the 1195×1600 original — all four within 0.001%.

## Extraction-quality pass (resume) — 2026-09-15

**Login 500 root cause found and fixed.** The frontend was up while the backend was not, and Vite's
dev proxy answers an unreachable target with an empty `500`, so the login screen could only say
"Request failed (500)". Reproduced by killing the backend and calling the login route through the
proxy. Fixed at three levels rather than papering over it: the proxy now returns a JSON `503` naming
the exact command to start the API; the login screen probes `/status` and shows a live backend banner
with a retry; `start.bat` waits for `GET /health` to answer before opening the browser, and a
lightweight `GET /health` probe was added. No auth behaviour was changed.

**Extraction-quality fixes (real-package driven, nothing hardcoded).**
- *Composite quantities were truncated to their first number.* `parse_quantity_expression` now parses
  the whole declaration (`28.4 g + 6.1 g EXTRA`, `… = 34.5 g`, `10 x 20 g`, `1 kg + 100 g`), keeps the
  printed expression, extra part, stated total and pack count, and refuses to sum across different
  measurement families (offered as an UNCERTAIN candidate instead).
- *A brand wordmark could win the product-name contest.* Digit-dominant lines were being rejected
  wholesale (killing names like `50-50 Classic Sweet & Salty`), scores were capped so two strong
  candidates collided, and ranking mixed OCR confidence with layout. Now: digit **dominance** test,
  single-token wordmark penalty, layout-first ranking (OCR confidence only breaks ties).
- *Alphanumeric batch codes were rewritten by numeric OCR correction.* `B07269L` became `807269L`
  (and leaked a bogus `807269 L` quantity reading). Correction is now identifier-aware: when a token
  is only numeric because its trailing letter was read as a unit, letters are left as printed; a
  genuine numeric misread inside a code (`AO13O` → `A0130`) is still repaired.

**Verified against the real package and the harness.** INS-2026-000017 now reports 11 DETECTED /
2 UNCERTAIN. A second golden fixture (`golden_britannia_ocr.json` + `test_golden_britannia.py`) locks
the reported Britannia failures down. Every extractor change was re-measured with the new behaviour
disabled: harness rates were identical, i.e. nothing was tuned to the synthetic numbers.

**Dataset + evaluation architecture.** `data/README.md` documents the layout, annotation schema,
product-level split policy (no image leakage) and the RPC policy (secondary visual-robustness only,
never declaration ground truth). `scripts/dataset.py validate|split|stats|ingest` implements it, with
15 tests covering leakage, empty ground truth, bbox sanity and non-fabricating ingestion. The first
real annotated product was ingested through the tool, and `scripts/run_benchmark.py` now also
evaluates annotated products over the union of their views — reporting per-product detail and
"sample too small for rates" instead of a percentage.

**Dashboard** now shows the product's actual method as a live-counted chain
(SCAN → EXTRACT → VERIFY → ALERT → REPORT), each stage linking to its page and reading real counts.

**Gates:** backend 174/174 passing, `tsc --noEmit` clean, production build clean, `e2e_check.py`
OK (rules, review, violation lifecycle, PDF/CSV/JSON, audit, auth), dataset validation clean.

---

## Pass 3 — PACKCHECK AI: product identity, optional vision layer, consumer tools

**Branding.** Every user-visible surface is now **PACKCHECK AI** (`Scan → Extract → Verify → Alert →
Report`); internal module/env naming (`pocket`, `POCKET_*`) is unchanged so existing configs keep
working. The design language of the supplied PACKCHECK AI screens was adopted as the single design
system: `#F9F9F8` base, white surfaces, `#E4E4E1` lines, `#1C1C1A` ink, `#2563EB` accent, `#0D9488`
secondary, Manrope / Inter / IBM Plex Mono, outlined icons (inline SVG, no CDN dependency so the
offline demo renders fully), grouped sidebar (MAIN / TOOLS / KNOWLEDGE / SYSTEM).

**Optional vision layer (`backend/ai/`).** A vision provider is a SECOND perception source, boxed in
by the existing deterministic pipeline: readings become ordinary candidates, are corroborated against
the on-device OCR, and a reading the OCR cannot match is capped in confidence and recorded as
UNCERTAIN ("offered for human confirmation"). No provider configured → the stage is reported as
`skipped` with a reason and the whole application works exactly as before. The provider is called
over HTTPS with the standard library (no new dependency) and the key never leaves the server.

**Consumer tools.** New `bills`, `grocery_items` and `complaints` tables with `backend/api/tools.py`:
Bill Scanner (Python arithmetic on stored values → potential price difference), Grocery (explicit-add
only; duration→date conversion refused without an anchor), Complaint Center (append-only timeline,
role-gated transitions). Dashboard gained nine live counter cards and three alert panels — all from
real rows.

**Frontend.** New pages Bill Scanner / Grocery / Complaint Center; New Inspection rebuilt as a
workspace (five-step rail, image tiles, 15-stage progress including the honest vision stage, critical
items, declarations with confidence, "not detected in supplied images", rule table, image quality,
explicit **Add to Grocery**); Dashboard upgraded to the design system's stat-card grid.

**Gates after this pass:** backend **231/231** passing (was 199; +32 new tests in `test_ai_layer.py`
and `test_tools.py`), `tsc --noEmit` clean, production build clean (287.8 kB JS / 26.4 kB CSS, 1.61s),
`e2e_check.py` OK, dataset validation clean (0 problems), no-hardcoding sweep clean — the golden
package's values no longer appear anywhere in production code, including prompts and comments.
