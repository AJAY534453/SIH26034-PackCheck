# Legal Rule Engine

The rule engine is **deterministic and data-driven**. No LLM ever decides a legal outcome —
structured evidence + applicable rule + rule conditions = evaluation. AI/OCR only answers
*"what information appears to be present in the package?"*; the engine answers *"what can
legitimately be established from that evidence?"*

## The central safety policy

```
MISSING_FROM_IMAGE  ≠  NOT_PRESENT_ON_PACKAGE
NOT FOUND BY OCR    ≠  ABSENCE PROVEN
```

An ordinary photograph of (part of) a package cannot prove that a declaration is absent.
OCR failure, poor lighting, an unphotographed side, or a missed line are all
**evidence insufficiency**, not legal non-compliance. Therefore:

| Situation | Rule result |
|---|---|
| Declaration detected with substantive evidence | `PASS` |
| Inspector confirms (after physical examination) the declaration is absent | `FAIL` |
| OCR simply did not find the declaration | `UNCERTAIN` (manual verification required) |
| Candidates conflict (e.g. ₹200 vs ₹250 across images) | `UNCERTAIN` (both retained for review) |
| Rule does not apply to this package/category | `NOT_APPLICABLE` |

`FAIL` is produced **only** from positive, sufficient evidence — most importantly the
human-confirmed-absence path below. Uncertainty is never converted into failure.

## Field evidence classes

Every extracted field carries an explicit `state`:

| State | Meaning | Confidence |
|---|---|---|
| `DETECTED` | Extracted from the supplied images with evidence (crop, bbox, source text) | numeric (OCR+extraction based) |
| `MISSING` | Not found in the **supplied images** — says nothing about the physical package | `null` (never a number) |
| `UNCERTAIN` | Observed but unreliable/unparseable (e.g. date anchor without a date) | `null` or low |
| `CONFLICTING` | Multiple values detected; all candidates retained | best candidate's confidence |
| `MANUALLY_CORRECTED` | Inspector corrected the value (original AI output preserved in audit) | 1.0 (human) |
| `HUMAN_CONFIRMED_ABSENT` | Inspector physically examined the package and confirmed absence | 1.0 (human) |

A missing field row stores `display_value = ""`, `confidence = 0/null`, and an
`uncertainty_reason` explaining what manual verification must establish.

## Semantic extraction guarantees

- **Anchors are never values.** "Manufactured by", "Marketed by", "Packed by", "Imported by"
  are labels; the extractor locates the *entity after/beside* the label. An anchor with no
  entity yields `UNCERTAIN`/`MISSING` — never `manufacturer = "Marketed by"` and never a `PASS`.
- **Brand ≠ product name ≠ common name** unless evidence supports each meaning
  (explicit "Brand:" label, multi-token title split, category indicators).
- **MRP vs unit sale price**: `₹0.40/g` is a unit price, never MRP. Negative patterns
  (nutrition values, phones, FSSAI, PIN codes, batch numbers, dates) are excluded by
  candidate scoring, not by "largest number wins".

## Rule evaluation model

Rules are versioned JSON (`backend/rules/definitions/`). Each evaluation is pinned to the
rule version used and is never retroactively changed when rules are updated.

```
check_type ∈ { declaration_present, net_quantity, mrp_declaration,
               legibility, manual_only }
```

- `manual_only` rules (standard pack sizes, exemptions applicability, permissible errors,
  physical font size) always return `UNCERTAIN` with an explicit reason — they require
  calibrated physical measurement and are never automated.
- `legibility` can claim **visibility** from pixels, never physical font height in mm.

## Human-confirmed absence (the only OCR-miss → FAIL path)

`POST /inspections/{id}/review/field` with `action: "CONFIRM_ABSENT"`, a mandatory reason,
and the reviewer's identity:

1. Field state becomes `HUMAN_CONFIRMED_ABSENT` (row created if none exists).
2. Applicable rules are re-evaluated deterministically (`evaluate_and_persist_rules`).
3. Rules for that field return `FAIL` citing the inspector's confirmation.
4. A violation is created with status `CONFIRMED` (severity `HIGH` for critical rules).
5. The inspection decision is recomputed — see below.
6. Everything is audit-logged (who, when, reason) and visible in the review history.

## Final decision logic (`backend/rules/decision.py`)

```
NON_COMPLIANT      any applicable rule FAILed with sufficient evidence
NEEDS_MANUAL_REVIEW  no confirmed failure, but any applicable rule is UNCERTAIN,
                     any field is CONFLICTING, or classification is uncertain
COMPLIANT          all applicable required rules PASS and nothing needs attention
```

Criticality (`net_quantity`, `mrp_declaration`) raises violation severity, not the
decision class. `manual_only` rules are surfaced as notes and never block a decision.

## Violation statuses

| Status | Meaning |
|---|---|
| `NEEDS_REVIEW` | Uncertainty recorded — **not** a violation; manual verification pending |
| `OPEN` | Evidence-backed potential violation awaiting human confirmation |
| `CONFIRMED` | Confirmed by an inspector (or via human-confirmed absence) |
| `DISMISSED` | Rejected by a reviewer |

## Compliance percentage and evidence coverage (`backend/services/compliance_service.py`)

A stored scan reports **two** percentages, because one number cannot honestly say both things:

| Figure | Definition |
|---|---|
| **Compliance %** | `100 × Σ(weight × credit) / Σ(weight)` over **decided** checks only — `PASS` = 1.0, `FAIL` = 0.0. Unverified requirements earn nothing here, and they are not counted as violations either. No decided checks at all ⇒ 0%, never a pass. |
| **Evidence coverage %** | `100 × Σ(weight of decided) / Σ(weight of applicable, machine-checkable)` — how much of the rule set the images actually allowed the engine to decide. |

The two figures separate two very different situations that a single weighted score used to blur: a
confirmed failure (high compliance loss, full coverage) and an unreadable label (full compliance,
low coverage). `UNCERTAIN` is deliberately **not** granted half credit — "we could not read it" is
not partial compliance.

### Where the weight comes from

`weight` and `critical` are declared **per rule** in `backend/rules/definitions/*.json`, seeded onto
the rule version (`rules.weight/critical`, `rule_versions.weight/critical`) and **pinned onto each
`rule_evaluations` row at evaluation time**. Consequences:

- re-prioritising a requirement is a data edit (or an admin rule update), not a code change;
- raising a weight creates a **new rule version** (the fingerprint includes it), so history is not
  silently re-scored;
- a stored scan's percentage is reproducible from the evaluations alone — a later library edit
  cannot move it.

Fallbacks exist only for rules that declare nothing: weight 2.0 for a critical requirement
(`net_quantity`, `mrp_declaration`) and 1.0 otherwise.

### What is excluded, and what is merely unverified

| Case | Effect on the maths |
|---|---|
| `manual_only` check (Rules 4, 5, 7 — physical verification) | excluded from both figures; listed with its reason |
| `NOT_APPLICABLE`, kind **`INAPPLICABLE`** (packaging scope; category confirmed as out of scope) | excluded from both figures |
| `NOT_APPLICABLE`, kind **`EVIDENCE_MISSING`** (import-only rule with no import evidence; category UNCONFIRMED/CONFLICTING) | **counted as unverified** — it lowers coverage and suppresses a pass-oriented verdict instead of silently raising the score |
| `UNCERTAIN` | unverified — lowers coverage |

`EVIDENCE_MISSING` outcomes also count as unresolved uncertainty in `decide_inspection`, so a package
whose import status could not be established cannot end in an unqualified `COMPLIANT` decision.

### The gate

A pass-oriented **preliminary** verdict requires all three: no `FAIL`, compliance ≥
`POCKET_AI_COMPLIANCE_THRESHOLD` (default 85), and coverage ≥ `POCKET_AI_COVERAGE_FLOOR`
(**default 70** — calibrated so the gate is reachable by a well-evidenced scan; the best evidence
coverage observed on the seeded corpus is 76.9%, so a floor of 80 made a pass impossible for every
scan). A confirmed `FAIL` always forces `NON_COMPLIANT`. Everything else is flagged for official
finalization and shown to non-reviewers as *"pending finalization by the higher officials"*.

### Changing the numbers deliberately

`scripts/recompute_scores.py` re-derives every stored scan from its stored evaluations (no re-OCR,
no re-decision). It is a dry run by default; `--apply` writes. A recorded human finalization is
never discarded by a recompute.

## UX language

The UI and reports use careful language:

- Instead of "MRP is missing" → *"MRP could not be reliably verified from the supplied
  images."*
- Instead of "violates Rule 6(1)(e)" on OCR miss → *"Rule 6(1)(e) requires manual
  verification because sufficient evidence was not obtained."*
- Every MISSING field row shows: *"Not found in the supplied images by OCR/extraction.
  This does NOT imply the declaration is absent from the package."*
