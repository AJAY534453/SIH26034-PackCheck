# Limitations

Pocket is **AI-assisted inspection support**. It is not a legal authority, not a replacement for an
authorized Legal Metrology officer, and not a substitute for formal physical inspection or sampling.

## What an image cannot prove

| Claim | Why Pocket cannot make it |
|---|---|
| "The package contains 500 g" | Only the *declaration* is visible; actual mass requires calibrated weighing (Rule 7 permissible error is a physical measurement). |
| "It was sold at the MRP" | Labels declare prices; transactions happen elsewhere. |
| "Font size is legally compliant" | Rules specify millimetres; photographs provide pixels unless a scale reference is in frame. Pocket reports VISIBLE / POSSIBLY NOT LEGIBLE / MANUAL VERIFICATION REQUIRED only. |
| "The declaration is authentic" | Print quality says nothing about tampering or authenticity. |
| "Dimensions are correct" | No calibration reference → physical size cannot be measured. |
| "This package is imported" | Import status is rarely provable from common images; import-specific rules stay NOT_APPLICABLE without explicit evidence. |

## What OCR cannot guarantee

- Curved, glare-covered, inkjet-coded, multilingual (Tamil/Hindi) or heavily compressed text may
  fail OCR. Pocket responds with preprocessing variants and region re-OCR, and ultimately
  **UNCERTAIN / NEEDS MANUAL REVIEW** — never fabricated text.
- A field that was not detected is **not proof of absence** on the package. `MISSING_FROM_IMAGE ≠
  NOT_PRESENT_ON_PACKAGE`: non-detection yields UNCERTAIN/NEEDS MANUAL REVIEW regardless of image
  quality. A rule FAIL is produced **only** from positive evidence or an inspector-confirmed
  absence (audited `CONFIRM_ABSENT` action).
- Field confidence is composed from OCR confidence, pattern strength and context agreement. It is
  an evidence-quality indicator, not a probability of legal correctness.
- **A syntactically valid contact is not a correct contact.** `name@domain.tld` can be an OCR
  corruption of the real address and still pass every structural check ('britindia' → 'briindio').
  Where the package's other declarations give something to compare against, an email domain that
  matches none of them (and is not a public mail provider) is reported **UNCERTAIN** with the
  reason stated, rather than published as a detected contact.
- **A printed value can be truncated.** OCR reads what is legible: a stated total may arrive as
  `34` where the pack prints `34.5`. Where a composite declaration's printed total disagrees with
  its own printed parts, the parts are combined and the mismatch is flagged for review instead of
  publishing a number the package cannot support.
- **Batch/lot values with no digit are never offered.** A word beside a `LOT No.` label is not a
  code, and a code printed as letters only is unverifiable. The field stays MISSING/UNCERTAIN
  rather than carrying a confident-looking wrong identifier.
- **A company entity with no role word is offered, not asserted.** If a pack prints a legal entity
  with no readable `Manufactured by` / `Packed by` / `Marketed by` anchor, the entity is reported
  under `manufacturer` as **UNCERTAIN** with the role explicitly unresolved — which legal role it
  fills is the inspector's determination.
- **Label/value geometry is inferred, not known.** Declaration blocks are two columns of print and
  OCR row offsets between them can exceed a line height. Pocket therefore associates a value with
  a label by row band *and* chronological sanity (a packing date cannot post-date its own use-by
  date), and records in the candidate's reason when a value was re-associated. A block that was
  already coherent is left untouched.

## Reprocessing and OCR evidence reuse

- Recognised OCR lines are stored per image as evidence, and the original image is immutable
  (content-hashed on upload). Reprocessing therefore **reuses the stored OCR evidence by default**:
  the same evidence yields the same declarations (deterministic, auditable) and the pipeline's
  slowest step is not repeated. `POST /inspections/{id}/process?refresh_ocr=true` recognises the
  images again when that is genuinely wanted (e.g. after an OCR-engine change).
- Consequence to be aware of: improving the *recogniser* alone will not change existing inspection
  results until OCR is refreshed explicitly; improving the extraction/rule layers does.
- The recogniser needs a few hundred MB of RAM per image and CPU-only inference. On a host whose
  free memory is exhausted by other applications, recognition can silently return no lines; the
  pipeline then reports every declaration as MISSING-from-image (never as absent, never as FAIL)
  and the stage detail says OCR produced nothing.

## Legal-source caveats

- Rule text in `backend/rules/definitions/` is an **engineering summary** of the Legal Metrology
  (Packaged Commodities) Rules, 2011 and selected requirements. The Official Gazette text and
  Department of Consumer Affairs notifications are authoritative.
- The 2011 Rules have been amended; definitions carry version metadata, and the registry creates
  new versions rather than rewriting history. **The shipped seed set is a verification starting
  point, not a complete or guaranteed-current legal encoding.** ADMINs must verify against
  official sources before relying on any rule in production use.
- Exemptions (e.g. Rule 4 contexts) depend on package type, intended use and institutional
  context that images generally cannot establish; they are never auto-applied.

## Deliberate exclusions

- No automated standard-pack (Rule 5) validation — requires the current notified quantity list per
  commodity; marked manual-only.
- No food-safety enforcement (FSSAI presence is extracted as supporting evidence only).
- No cloud services, no paid APIs, no telemetry. All processing is local.

## Extraction robustness (measured, not claimed)

After the second-generation robustness upgrade (canonical line ordering, bottom-strip region pass,
fused-amount decomposition, entity/product separation guards):

- **Real image (INS-2026-000017, Sukku Kaapi panel photo):** 11 DETECTED · 3 UNCERTAIN · 9 MISSING.
  Detected: FSSAI, phone, email, website, MRP, net quantity, batch code, best-before duration,
  manufacturer, manufacturer address, product name. Manufacturing date is offered as an
  UNCERTAIN **review candidate** (`2025-02-04` from the separator-less inkjet code `040225`);
  the pipeline refuses to promote it because DD/MM vs MM/DD ordering is unprovable from the
  image alone. `date_expiry` is UNCERTAIN with the explicit observation "'use by' present but
  date not parseable". `brand` is UNCERTAIN `GURUCHARA`, read from the logo badge and marked
  *inferred* — typography is not a declaration, so it is never asserted as a detected fact.
- **Brand semantics:** a brand is only ever offered (marked `inferred`, state UNCERTAIN) and only
  when the logo/masthead reading is corroborated by a repeat of the same token elsewhere on the
  label. An explicit `Brand:` label is the only path to a DETECTED brand. All-caps fused strings
  (e.g. a fused company name) are never split into words, because the split would be a guess.
- **Synthetic harness (seed 42, 3,600 samples):** overall detection **47.7%**, false-assignment
  **4.94%**, raw-match ~16%. Anchor-driven fields are strongest (MRP 80.0%, website 77.0%, unit
  price 68.0%, net quantity 63.0%, manufacturer 58.5%); date/batch/phone fields are deliberately
  conservative (a plausible guess is penalized as `wrong_value`). Reading separator-less date
  codes raised detection from 43.9% → 47.7% at a cost of +0.30pp false-assignment (4.64% → 4.94%) —
  a deliberate trade, documented rather than hidden. These numbers are SYNTHETIC and are never
  blended with real-image results.
- Known remaining gaps: phone-only-without-keyword detections, unlabeled batch codes, packer-
  registration-number extraction (no dedicated field yet — the value is often OCR-readable but  is not routed to its own tracked field), country-of-origin variants, packed-by/imported-by role
  disambiguation when several role anchors share one line, and ambiguous date ordering (stays
  UNCERTAIN by design).
- **Dates on low-contrast inkjet print:** dot-matrix/inkjet date codes photographed at panel scale
  are frequently at the edge of OCR capability (on the golden panel the best reproducible read of
  the manufacturing date row is partial). Targeted bottom-strip passes at 2× upscale recover some
  rows but cannot manufacture legibility that the pixels do not contain — such fields stay
  UNCERTAIN and are routed to manual review rather than guessed.
- **Entity/address display normalization is presentational only:** word joins (`KonguNagar` →
  `Kongu Nagar`), repeated commas and PIN-code hyphens are tidied for *display*; `raw_value` and
  the evidence crop always keep the original OCR string, and no word is ever added, reordered or
  corrected.
- **Composite quantity declarations are combined only when the units agree.** `28.4 g + 6.1 g
  EXTRA = 34.5 g`, `10 x 20 g` and `1 kg + 100 g` are combined and the printed expression, extra
  part and stated total are all retained. A declaration mixing measurement families
  (`500 g + 1 N`) is **not** summed: the base part is offered as an UNCERTAIN candidate for human
  review, because inventing a total across unlike units would be a fabricated measurement.
- **Alphanumeric batch/lot codes are never digit-corrected.** OCR character correction is applied
  to numeric declarations (MRP, quantity, dates, FSSAI, phone). A code such as `B07269L` is left as
  printed: rewriting its letters (`807269L`) would fabricate a different printed code. A genuine
  numeric misread inside a code (`AO13O` → `A0130`) is still corrected, because no unit letter is
  involved there.
- **A brand mark is offered, not asserted.** A logo/wordmark is typography rather than a labelled
  declaration, so a brand read from it is reported as an inferred UNCERTAIN candidate requiring
  corroboration (an explicit `Brand:` label or repetition elsewhere on the label). If corroboration
  is absent, no brand is claimed.
- **Not extracted / not auto-decided (by design):** physical quantity and permissible error
  (Rule 7), standard pack sizes (Rule 5), exemption applicability (Rule 4) and physical font
  height in millimetres. These are declared as MANUAL_ONLY rules and routed to the inspector,
  never auto-passed or auto-failed.

## Vision provider (optional, and what it does not change)

- **It is optional and off by default.** With no `GEMINI_API_KEY` the product runs the complete
  on-device OCR pipeline, the rule engine, the reports and every consumer tool. The UI states which
  mode it is in; the pipeline records the stage as `skipped`.
- **A provider reading is a candidate, not a fact.** If the on-device OCR cannot corroborate a
  reading, it is capped in confidence and offered for human confirmation — it is never published as a
  DETECTED declaration, and it never becomes a rule FAIL on its own.
- **Corroboration is fuzzy text agreement, not independent verification.** Two readers agreeing is
  stronger evidence than one, but both can be wrong on the same pixel pattern. The confidence cap
  and the review flag exist precisely because agreement is not proof.
- **It does not raise the ceiling on what an image can prove.** Physical quantity, actual charged
  price, millimetre font height and transaction facts remain outside image-based verification.
- **Sending images to a provider is a disclosure decision.** It is off unless an operator sets a key,
  and the key stays server-side; but an operator who enables it should treat package photographs as
  leaving the machine.
- **No accuracy rate is claimed for it.** No benchmark of provider readings exists in this repository,
  and none is implied by the confidence numbers shown in the UI.

## Consumer tools (Bill Scanner / Grocery / Complaint Center)

- The Bill Scanner compares a **printed MRP** with a **billed price**. It cannot tell whether a
  discount, tax or a different pack size explains the gap — that is what the human verification step
  is for, which is why the wording is "potential price difference", never "overcharge".
- A bill's MRP column is only filled when the bill actually prints one; with no vision provider the
  values are whatever the user typed, and the record says so (`extraction_source`).
- Grocery expiry alerts exist **only** for items the user explicitly added. A duration such as
  "Best before 12 months" is not converted into a calendar date unless there is a real date to count
  it from; anchoring it to the purchase date would be an invention, so the item is tracked as
  date-unknown instead and the reason is shown.
- Complaint status and severity are the **complainant's and reviewer's** framing, not a legal
  finding. The timeline is append-only history and is what makes the complaint auditable.
- Sample/demo values are labelled SAMPLE everywhere and are never mixed into a real reading.

## When in doubt

The system's bias is explicit: **prefer NEEDS MANUAL REVIEW over a wrong confident answer.** A
provider reading the OCR cannot corroborate is treated exactly the same way.
