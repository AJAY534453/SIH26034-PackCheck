# Testing & Benchmark Methodology

## Test suite (pytest — 231 tests)

```bash
.venv\Scripts\python -m pytest backend/tests -q
```

Coverage by layer:

| Suite | What it proves |
|---|---|
| `test_extraction.py` | MRP positives (`MRP ₹200`, `M.R.P. : ₹200.00`, `Maximum Retail Price ₹200`, `M R P Rs 200.00`, `Retail Sale Price: Rs. 500`) and the full negative matrix (`₹0.40/g`, `₹120/kg`, `₹10/100g`, nutrition, phone, FSSAI, batch, PIN, date). Unit-price separation. Quantity units incl. `10 N`. Date formats incl. ambiguity flags. Batch/FSSAI structural checks. Contact extraction. Conflict flagging vs numeric-format agreement. Manufacturer/address extraction with nutrition exclusion and address-number preservation. |
| `test_rules.py` | Decision aggregation: COMPLIANT only when all applicable PASS; NON_COMPLIANT on any rule FAILed with sufficient evidence (critical or not); any UNCERTAIN or conflicting field ⇒ NEEDS_MANUAL_REVIEW; manual-only rules never block. |
| `test_upgrade.py` | Correctness-upgrade regressions: OCR-miss on MRP/net-quantity/mfg-date/consumer-care/batch ⇒ rule UNCERTAIN + NEEDS_MANUAL_REVIEW (never FAIL); human-confirmed absence ⇒ FAIL + NON_COMPLIANT; `Marketed by:` anchor never becomes a value; `Marketed by: ABC Foods Pvt Ltd` ⇒ entity extracted; brand/product-name semantic separation; empty-value candidates carry no confidence; conflicting OCR ⇒ CONFLICT; physical quantity/font-size never auto-verified. |
| `test_api.py` | Auth (bad password, missing token), role enforcement (viewer cannot create), full workflow (create→upload→process→detail→export JSON/CSV), upload rejection (extension, corruption, duplicates), process-without-image rejection, review actions + audit trail, rules/dashboard endpoints, file-endpoint auth and traversal blocking. Also: the **violation human-decision lifecycle** (CONFIRM/DISMISS/RESOLVE — reason-gated, role-gated, audited, and decision-changing: dismiss ⇒ NEEDS_MANUAL_REVIEW, confirm ⇒ NON_COMPLIANT, dismissal durable across re-evaluation), the **audit log** endpoint (ADMIN-only read, filters, append-only — no write method exposed), and **evidence completeness** (a review candidate — an uncertain value offered for confirmation — exposes its source region with a `note`, and no DETECTED value may appear without retained evidence). |
| `test_services.py` | Normalizers (MRP/quantity/date/website), quality assessment (dark, blurred, and clean-rendered labels), and a `@slow` end-to-end OCR test on a rendered label. |
| (in `test_robustness2.py`) | **Entity/address display normalization**: OCR word joins (`KonguNagar` → `Kongu Nagar`), repeated commas, PIN-code hyphens — with the raw OCR value asserted unchanged, and ALL-CAPS fused names asserted *unsplit*. |
| `test_robustness2.py` | Second-generation robustness regressions: nutrition-flag/line-order alignment (the image-17 root cause), batch label-never-the-value + numeric-code vs price guard, quantity junk-prefix correction with provenance (`i5o0g` → 500 g, raw preserved) and no-guessing on fused digits, MRP fused-amount decomposition (`200.0010.4091` → 200.00) with anchor binding, manufacturer column-guard/fused-entity recognition/anti-duplication, product-name vs entity mis-attribution guards (role-anchor lookback, narrative/sentence/contact/quoted-line exclusions, title height prior), bottom-strip bbox back-mapping to original coordinates, escalation policy (no extra passes when PASS-1 is complete), and the **golden Sukku regression** driven by `backend/tests/fixtures/golden_sukku_ocr.json` (expected values live in the fixture only; a no-hardcoding test asserts extraction code contains no product-specific constants). |

| `test_golden_britannia.py` | Second golden case, driven by `backend/tests/fixtures/golden_britannia_ocr.json` (a biscuit label photographed across faces). Locks down the semantic failures reported from the real package: the manufacturer **entity** is never taken as the product name, `BRITANNIA`-style wordmarks are separated from the product title as *reviewable* brand evidence, the **composite quantity is reported whole** (`28.4 g + 6.1 g EXTRA = 34.5 g`), the alphanumeric batch code keeps its letters (`B07269L`, never `807269L`), and `PKD`/`USE BY` map to packing/expiry without cross-contamination. |
| `test_golden_real_multiview.py` | **Golden regression built from the REAL three-surface photo set** (`backend/tests/fixtures/golden_real_multiview_ocr.json` — the stored OCR lines of the actual upload, misreads included). Locks down what that case exposed: the company entity with OCR-fused words (`BRITANNIA INDUSTRIESLTD.`) is never the product name; a STACKED title interrupted by a promo tag is read as one block; the two-column date block is not cross-wired (`PKD.` must not take the date printed beside `USE BY`); the composite quantity is reported whole when the printed total lost its unit; the alphanumeric batch code is taken from the value column instead of the adjacent word `OTHER`. |
| `test_real_world_fixes.py` | Focused unit regressions for the same defects stated as general rules: company-suffix recognition under OCR word fusion (and ordinary product text *not* matching it), composite quantity with a lost-unit printed total, quantity units run into the following word (`500gEXTRA`), mixed measurement families never summed, identifier value lines never yielding a quantity, batch candidates rejecting words/dates/PINs/prices, chronological re-pairing of a printed date block (and a coherent block left untouched), stacked-title merging, low-confidence lines never published as titles, and email-domain corroboration. |
| `test_ai_layer.py` | The optional vision layer's contract: with no provider configured **nothing is fabricated and nothing is called out to the network** (the provider function is monkeypatched to fail the test if invoked); disabling it with `AI_ENABLED=0` wins even when a key exists; model output is parsed strictly (JSON fences and surrounding prose recovered, malformed payloads rejected); **null values and unknown field names are dropped** so a model cannot invent a declaration; observations are clamped to `0..1`; bounding boxes are only accepted in normalised form (pixel values are ignored, never guessed); OUT-OF-RANGE → the `marketed_by` alias maps to the pipeline's `marketer` field; a reading that OCR can corroborate keeps its confidence, while an uncorroborated one is capped and flagged; a bbox maps into the source image's pixels; and end-to-end, a vision candidate flows through the deterministic pipeline (normalized by the same validator, recorded with its own provenance, rule-evaluated) with the stage recorded as `done` — and `skipped` with an explanation when no provider is configured. |
| `test_tools.py` | The consumer tools: bill comparison arithmetic (over/at-or-below/insufficient, zero-MRP rejection, printed-price parsing), manual and `demo` bills (sample values labelled as such and asserted **not** to be readings), a bill scan with no provider storing evidence and saying exactly why nothing was read, non-image rejection, grocery explicit-add semantics (nothing enters the tracker implicitly), a declared duration **not** converted into a date without an anchor and the anchored case computed and marked provisional, README `NO_EXPIRY_DATA`/`EXPIRED`/`FRESH` grouping, owner-scoped deletion, the complaint lifecycle (append-only timeline, viewer forbidden from advancing, inspector/admin allowed, resolution text captured), and the dashboard counters reflecting real rows rather than invented ones. |
| `test_dataset.py` | The dataset tooling that guards every accuracy claim: missing images, unannotated images, duplicate image content, invalid JSON, empty field values, unknown field names, degenerate/incomplete bboxes, **split leakage**, product-level splitting (reproducible, every product exactly once), split refusal on an invalid dataset, ingest copying evidence without fabricating values, and a check that the shipped real dataset passes its own validation. |

Also: `scripts/e2e_check.py` runs the **real pipeline** (real RapidOCR on a rendered label) through
the HTTP API and asserts a full inspection completes with report + export.

Also: `scripts/dataset.py validate|split|stats` validates the real dataset, and
`scripts/run_benchmark.py` measures the real photo in `data/annotations/` (see DATASET section of
SCANNING_ACCURACY_REPORT.md).

**Test-count is never presented as accuracy.** Tests verify behaviour, not recognition rates.

## Synthetic vs real data

- Synthetic labels (rendered at runtime by tests and `e2e_check.py`) exercise pipeline mechanics.
  They are labeled DEMO/SYNTHETIC and their results are never reported as real-world accuracy.
- The variation harness (`backend/testing/robustness.py`, seed=7, 3,600 samples/report) measures
  extraction behavior on systematically generated declaration variations: overall detection 47.4%,
  false-assignment 5.19%, multilingual false conflicts 0. Strong fields are anchor-driven (MRP
  77.5%, website 77.0%, unit price 69.0%, manufacturer 67.5%, net quantity 61.0%, consumer-care
  email 60.5%); date/batch/phone fields remain deliberately conservative (batch 23.5%, country of
  origin 26.0%, phone 28.5%, date import 19.5%) — a plausibly wrong guess is counted as
  wrong_value, so low detection there is honest caution, not hidden failure. These are SYNTHETIC
  numbers and never substitute for real-image accuracy.

  **Measured trade-off from the real-package pass (documented, not hidden):** the fixes were
  driven by observed real-photo failures, and on this synthetic corpus they moved detection
  46.7% → 47.4% and false-assignment 4.92% → 5.19%. Two movements are deliberate: `manufacturer`
  50.5% → 67.5% (a company entity printed with no role anchor is now offered, marked UNCERTAIN),
  and `batch_lot` 27.0% → 23.5% (a batch value with no digit — or with an embedded `:` such as a
  machine/time code — is no longer published at all). Fewer wrong codes for fewer detections is
  the intended direction for an inspection record.

  **Corpus note:** the generator is seeded, and its illustrative sample values were neutralised in
  the SIH-readiness pass to remove golden-panel coupling; that re-drew the sample set. The figures
  above are the current reproducible run (`evaluate_robustness(seed=7, samples_per_field=200)`).
  The extractor changes made in this pass were measured against the harness with the new behaviour
  disabled and produced **identical** rates, i.e. they are not tuned to these numbers.
- **Measured trade-off (documented, not hidden):** reading separator-less inkjet date codes
  (`040225`) raised overall detection from 43.9% → 47.7% while false-assignment moved 4.64% → 4.94%
  (+0.30pp). More dates are recovered; a small number of ambiguous codes are now also offered as
  candidates — which is why those fields are routed to review rather than asserted.
- Real-image accuracy comes **only** from human-entered ground truth under `data/` — either the
  single-image manifest (`data/benchmark/`) or the annotated multi-view products
  (`data/annotations/` + `data/raw/`).

## Real-image benchmark

Two real sources are evaluated, reported separately:

* **`data/benchmark/`** — single images, one ground-truth JSON each (below).
* **`data/annotations/`** — annotated products, evaluated over the **union of their views**
  (the strongest reading of each field wins, exactly as a multi-image inspection is fused).
  Dataset layout, schema and split policy: `data/README.md`. Validate and split with:

```bash
.venv\Scripts\python scripts/dataset.py validate
.venv\Scripts\python scripts/dataset.py split --seed 20260915
.venv\Scripts\python scripts/dataset.py stats
```

### Single-image manifest

1. Place real package photos in `data/benchmark/`.
2. Add a row per image to `data/benchmark/manifest.csv`:
   `my_photo_01.jpg,ground_truth/my_photo_01.json,<hint>,<notes>`
3. Create `ground_truth/my_photo_01.json` from `_schema_example.json`, entering values **you**
   read from the image. Omit fields not visible.
4. Run:

```bash
.venv\Scripts\python scripts\run_benchmark.py
```

Per field, the runner measures: exact match, normalized match (200 == 200.00, phone/date
formatting), wrong, not-detected, and mean confidence on correct fields. A text field also counts
as correct when the observed value **contains** the human reading, because two production contracts
deliberately report more than the bare declaration: `manufacturer` carries the entity together with
its address (the address is also offered separately), and a duration is reported with its reference.
A candidate whose value is empty is counted as *not detected*, never as a wrong answer.
It regenerates `docs/SCANNING_ACCURACY_REPORT.md`. If no ground truth is present, the report says
**"INSUFFICIENT DATA — no accuracy is claimed"** rather than inventing numbers, and when fewer than
three products are annotated it prints per-product detail with an explicit "sample too small for
rates" notice instead of a percentage.

## Frontend gates

```bash
cd frontend
npx tsc --noEmit     # strict TypeScript
npm run build        # production build (tsc + vite build)
```

Both are part of the definition of done; the build must pass with zero errors. The build is
verified against the design-system rebrand: the typefaces are loaded as progressive enhancement and
the CSS falls back to `system-ui`, so the offline demo laptop renders a complete interface.

## Vision provider testing without a provider

The suite must pass with **no API key present** — that is the shipping default and the CI condition.
The provider is therefore never stubbed to "succeed": the tests assert that with no key
(`backend/tests/test_ai_layer.py`)

- `provider_status()` reports disabled and the reason names the on-device OCR fallback,
- `extract_label_fields` / `extract_bill_fields` return `used=False` with an empty observation list,
- and `run_vision_json` is monkeypatched to raise, proving the pipeline never reaches for the
  network when no provider is configured.

The corroboration, cap-and-flag, bbox-mapping and prompt-strictness behaviours are tested directly
on synthetic model output, so they stay covered whether or not a key ever exists on the machine.
