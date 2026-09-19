# Baseline & Current Measured Metrics — POCKET

**Measurement discipline:** real-image results and synthetic results are reported separately and
never blended. No accuracy claim in this file (or anywhere in the repo) generalizes beyond the
exact data it was measured on.

## 1. Backend test suite (functional correctness)

- Suite: `backend/tests` — **199/199 passing**
  (`OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 pytest -q`, ~59s). That is 174 tests after the
  extraction-quality pass, 141 before it, and 101 at the first baseline; every pre-existing test is
  retained and passing.
- Caveat measured while running the suite on a memory-starved host: without the thread limits the
  OCR-dependent `test_bottom_strip_bbox_mapping_preserves_original_coordinates` fails
  intermittently (the recogniser returns no lines when the machine has no free memory), and the run
  reports 198 passed / 1 failed. It passes in isolation and passes in the full run with the thread
  limits set — i.e. the flake is host resource pressure, not a code defect (see LIMITED
  ENVIRONMENT below).
- Added in the real-package pass (+25 tests): `test_golden_real_multiview.py` (8) — the golden
  regression built from the stored OCR of the **actual three-surface photo upload**, and
  `test_real_world_fixes.py` (17) — unit rules for company-entity recognition under OCR word
  fusion, composite quantity with a lost-unit printed total, value-column label association,
  chronological date-block re-pairing, stacked titles, identifier shape and email corroboration.

**LIMITED ENVIRONMENT (honest caveat, not a code property):** this machine has ~7.4 GB RAM of
which other desktop applications hold most; at times under ~350 MB was free, and in that state a
fresh Python process cannot initialise NumPy/OpenBLAS at all (`OpenBLAS error: Memory allocation
still failed after 10 retries`) and the recogniser silently reads nothing. That is why one
OCR-dependent test above is intermittent. Setting `OPENBLAS_NUM_THREADS=1` and processing one
image at a time is what made the runs above possible.
- Added in the extraction-quality pass: composite quantity declarations (`28.4 g + 6.1 g EXTRA`,
  `… = 34.5 g`, `10 x 20 g`, `1 kg + 100 g`, mixed-family parts never summed), the golden
  Britannia-shaped label (`test_golden_britannia.py` — manufacturer entity is never the product
  name, wordmark separated from the title, alphanumeric batch code keeps its letters, PKD/USE BY
  label association), and the dataset tooling suite (`test_dataset.py`, 15 tests: leakage, empty
  ground truth, bbox sanity, product-level reproducible splitting, ingestion without fabrication).
- Includes 10 regressions added in the extraction-robustness pass: entity/address display
  normalization (word joins, repeated commas, PIN hyphen — raw asserted unchanged and ALL-CAPS
  fused names asserted unsplit), cross-image aggregation (disagreeing images ⇒ CONFLICT with both
  candidates; agreeing images ⇒ no conflict; a weak second image never displaces strong evidence),
  and evidence completeness (review candidates carry a source region + `note`; no DETECTED value
  without retained evidence).
- Includes 6 new regressions: the violation human-decision lifecycle (CONFIRM/DISMISS/RESOLVE —
  reason-gated, role-gated, audited, decision-changing, durable across re-evaluation), the
  ADMIN-only append-only audit-log endpoint, additive `manufacturer_address` / clean batch-code
  behaviour, and a broadened no-hardcoding guard that reads the golden fixture and scans all
  production modules (plus `frontend/src`) for high-specificity identifying literals.
- Includes 23 upgrade regression tests (`test_upgrade.py`) covering the legal-safety invariants:
  OCR-miss → UNCERTAIN (never FAIL) for MRP/quantity/date/consumer-care/batch; human-confirmed
  absence → FAIL + NON-COMPLIANT + CONFIRMED violation; anchor-only manufacturer → UNCERTAIN;
  empty value → no confidence; conflicts → CONFLICT/manual review; physical quantity and font
  size never auto-verified.
- Includes 27 robustness regressions (`test_robustness2.py`): nutrition-flag/line-order alignment,
  batch label-never-value + code-vs-price guards + repeated-anchor stripping, junk-prefix quantity
  correction with provenance, fused-amount MRP decomposition, manufacturer column/fused-entity/
  dedupe guards, additive `manufacturer_address` grouping, product-name vs entity mis-attribution
  guards, bottom-strip bbox back-mapping, escalation policy, and the golden Sukku regression with
  fixture-only expectations plus a broadened no-hardcoding guard.
- Current per-file split (test functions; parameterised cases expand the totals):
  `test_robustness2.py` 32, `test_extraction.py` 28, `test_upgrade.py` 19, `test_api.py` 19,
  `test_dataset.py` 15, `test_rules.py` 8, `test_services.py` 8, `test_golden_britannia.py` 8.
- Frontend gates: `npx tsc --noEmit` clean, `npm run build` clean (50 modules, ~232 kB JS).
- `scripts/e2e_check.py` (real OCR through the HTTP API) passes: create → upload → process →
  evidence → rules → review (edit/confirm-absence/note) → violation confirm/dismiss → re-decision
  → PDF/CSV/JSON export → audit trail → auth guard.

## 2. Synthetic text-variation robustness harness (NOT real-world accuracy)

Source: `backend/testing/robustness.py` over `backend/testing/variations.py` (seeded, streaming,
reproducible). Latest run: **seed 7, 3,600 samples** (200 per field × 18 fields),
reproducible with `evaluate_robustness(seed=7, samples_per_field=200)`.

> **Corpus note:** the generator's own illustrative sample values (`vedhaproducts.com`, a demo
> batch code) were neutralised in the SIH readiness pass to remove golden-panel coupling. The
> corpus is seeded, so this re-drew the sample set: the figures below are measured on an
> equivalent but not identical synthetic corpus to the earlier run. The change is a **corpus
> change, not a claimed extractor improvement**, and no extractor was tuned against either run.

- Overall detection rate: **47.4%** (46.7% before the real-package pass)
- False-assignment rate: **5.19%** (4.92% before) — the deliberate trade-off for offering
  unanchored company entities (manufacturer 50.5% → 67.5% detection) while refusing digit-less
  batch values (batch 27.0% → 23.5%)
- False-assignment rate (hallucination guard): **4.92%**
- Raw-match rate: **~16%**
- Multilingual false-conflict count: **0** (same-field different-script duplicates never conflict)

Strongest fields (per-field detection, 200 samples each): mrp 77.5%, website 77.0%,
unit_sale_price 69.0%, net_quantity 61.0%, consumer_care_email 60.5%, manufacturer 50.5%. Weakest
(deliberately conservative — a plausible guess is counted as wrong_value, so these report "missed"
rather than guess): date_import 19.5%, country_of_origin 26.0%, batch_lot 27.0%,
consumer_care_phone 28.5%, date_packing 36.5%.

**Measured trade-off (previous pass, still true):** reading separator-less inkjet date codes raised
detection 43.9% → 47.7% while false-assignment moved 4.64% → 4.94% (+0.30pp). Recovered dates are
offered as UNCERTAIN candidates rather than asserted, which is why the false-assignment cost stays
bounded.

**Isolation check for this pass:** every extractor change made in the extraction-quality pass
(composite quantity parsing, entity/wordmark separation, identifier-aware numeric correction) was
re-measured with the new behaviour disabled. Overall detection, MRP, quantity and batch rates were
**identical** with and without each change, so none of them was tuned against this harness — the
figures above are the current reproducible baseline, drawn on a re-seeded corpus.

Dominant failure modes (by corruption): numeric-character corruption (O↔0, S↔5) causing both
NO_CANDIDATE and WRONG_VALUE; dropped characters; split/merged tokens. Clean samples still fail
for a small number of NO_CANDIDATE + WRONG_VALUE cases — genuine anchor-coverage gaps (e.g. phone
without a consumer-care keyword, unlabeled batch codes).

**Interpretation:** these numbers measure the extraction layer against systematically generated
label-text variations only. They are not OCR-on-photographs accuracy and must not be presented
as such. They exist to (a) find anchor/normalization gaps and (b) guard against regressions.

## 3. Combinatorial variation space

`SampleGenerator.total_space` for the current field set: **tens of millions** of enumerable
label × value-format × layout × corruption × neighbor combinations (verified > 10,000,000),
generated on demand with per-seed reproducibility. This is a test-case generation capability,
not a corpus that has been materialized or trained on.

## 4. Real images

- Golden/e2e path: `scripts/e2e_check.py` (happy path + negative path + human-confirm-absent
  flow verified end-to-end through the API; see PROGRESS_LOG entries).
- Golden panel `sukku kappi.jpeg` (inspection INS-2026-000017): **11 DETECTED / 2 UNCERTAIN /
  11 MISSING** — MRP, net quantity, batch, FSSAI, manufacturer, manufacturer address, consumer-care
  phone, consumer-care email, website, product name, best-before duration. UNCERTAIN: manufacturing
  date (review candidate from the inkjet code), brand (`GURUCHARA`, inferred from the logo, offered
  not asserted). MISSING fields are genuinely absent from the supplied (back-panel) photo. One
  panel photo is not a benchmark — no accuracy percentage is claimed from it.
- **First real annotated product** (`data/annotations/p0001.json`, the back-panel photo, 13 fields
  of human ground truth): `scripts/run_benchmark.py` reports **9 of 12 evaluated field cases
  correct** over the ingested view (`scripts/e2e_check.py`-style real OCR over the HTTP API). The
  three not-detected cases — MRP, batch/lot, manufacturing date — are printed on other faces of the
  package (front/bottom) that have not been ingested yet; this is exactly the multi-view principle,
  not an extractor failure. Per-product detail: `docs/SCANNING_ACCURACY_REPORT.md`.
- No statistically meaningful real-image benchmark has been run: **one product is a smoke check,
  not a rate**. The report says "sample too small for rates" and claims no percentage.
  `data/README.md` documents how to grow the dataset (product-level splits, schema, validation).

## 5. Frontend

- `tsc --noEmit` clean; production build passes (232 kB JS / 71 kB gzip, 16.4 kB CSS).
- The dashboard shows the product's actual method as a live-counted chain
  (SCAN → EXTRACT → VERIFY → ALERT → REPORT), each stage linking to its page; counts came from
  the live database (39 images, 249 declarations detected, 8 awaiting review, 7 conflicts, 15 open
  findings, 20 reports at the time of writing).
- Layout verified in-browser at 639px (mobile tier): zero horizontal overflow, sidebar→content
  gap 0px, topbar spans to viewport edge (post-hotfix).

## 6. Performance

- Full pipeline (quality → multi-pass OCR → extraction → rules → evidence) runs on CPU only;
  e2e inspection completes in seconds on the dev laptop. Multi-pass OCR is confidence-gated
  (targeted preprocessing only for uncertain fields), not unconditional.

## 7. Pass 3 — vision layer, consumer tools, design-system rebrand

### Test suite

| Metric | Before | After |
|---|---|---|
| pytest | 199 passed | **231 passed** (65–97s on the dev laptop) |
| New suites | — | `test_ai_layer.py` (13), `test_tools.py` (19) |
| TypeScript (`tsc --noEmit`) | clean | clean |
| Production build | clean | clean — 54 modules, 287.8 kB JS (84.8 kB gz), 26.4 kB CSS, 1.61s |
| `scripts/e2e_check.py` | OK | OK (rules, review, violation lifecycle, PDF/CSV/JSON, audit, auth) |
| `scripts/dataset.py validate` | 0 problems | 0 problems (1 product) |
| No-hardcoding sweep | clean | clean (golden values removed from production code **including prompts and comments**) |

### Vision layer (measured, no accuracy claim)

- With no `GEMINI_API_KEY`, `GET /ai/status` returns `enabled: false` and the pipeline records
  `vision_extraction = skipped`. No outbound request is attempted (asserted in `test_ai_layer.py`).
- Corroboration threshold: a reading is treated as corroborated at squashed-text similarity ≥ 0.82
  against the OCR text of the same image. Corroborated readings keep the model's confidence
  (capped at 0.97); uncorroborated readings are capped at 0.62 and recorded as UNCERTAIN.
- **No accuracy percentage is reported for the vision layer.** None has been measured against
  ground truth, and none is implied by the confidence values shown in the UI.

### Consumer tools

- Bill comparison is deterministic arithmetic: difference = billed − MRP, percentage of MRP, and a
  configurable tolerance (`BILL_PRICE_TOLERANCE_PCT`, default 0.5%) below which no flag is raised.
- Grocery alert thresholds: EXPIRED (< 0 days), EXPIRING_SOON (≤ 7 days), FRESH (> 7 days),
  NO_EXPIRY_DATA when no anchored date is tracked.
- Verified live in the browser during this pass: sample bill ₹45 vs MRP ₹40 → "⚠ Potential
  difference" (₹5, 12.5%), labelled `SAMPLE`; grocery item with "Best before 12 months" and no anchor
  → `NO EXPIRY DATE TRACKED` with the reason (the date was **not** invented); complaint lifecycle
  SUBMITTED → UNDER_REVIEW with an append-only timeline.

### Third-party calls

- Default configuration makes **zero** outbound calls. The vision provider is the only optional
  outbound call and requires an operator-set key. Package photographs are only sent when it is enabled.
- No new Python dependency was added for the vision layer: one HTTPS POST built with the standard
  library. The frontend added no icon font/CDN dependency either (inline SVG icons).
