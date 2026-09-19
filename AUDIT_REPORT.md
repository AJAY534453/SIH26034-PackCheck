# Repository Audit — POCKET (Phase 0, v2 master prompt)

Baseline document: later phases reference this ("per AUDIT_REPORT.md this was WEAK because X,
now improved by Y"). Classifications: **WORKS** / **WEAK** / **MISSING** / **DO NOT TOUCH**.

## Frontend (React + Vite + TS)

| Area | Verdict | Notes |
|---|---|---|
| App shell (sidebar + topbar) | WORKS | Fixed sidebar; column shell after layout hotfix; content fills width. |
| Dashboard / Inspections / Violations / Products / Reports / RuleLibrary / Settings | WORKS | Real data only, honest empty states, skeletons, role-gated actions. |
| Inspection Detail + EvidenceViewer | WORKS | 7-section layout, field↔region highlight, conflict pairs, honest status/confidence. |
| Badges.tsx legacy exports | WEAK | `Badge`/`Conf` superseded by `components/ui.tsx`; mechanical migration pending. |
| Keyboard table navigation | MISSING | Tab/click accessible; arrow-key review flow not implemented. |

## Backend (FastAPI + SQLAlchemy + SQLite)

| Area | Verdict | Notes |
|---|---|---|
| API contracts (auth, inspections, review, files, meta) | DO NOT TOUCH | Stable; frontend depends on exact shapes. |
| Authentication / RBAC / audit logging | DO NOT TOUCH | scrypt hashing, JWT, role checks, full audit trail — verified intact by tests. |
| Rule engine (`rules/engine.py`, `decision.py`) | WORKS | Deterministic; non-detection → UNCERTAIN; human-confirmed absence → FAIL; version-pinned evaluations. |
| Rule definitions (`rules/definitions/*.json`) | WORKS | Data-driven, versioned; extend, never hardcode. |
| Inspection pipeline (`services/inspection_service.py`) | WORKS | Stage statuses, MISSING rows, conflict evidence, FK-order cleanup, marketer promotion. |
| Upload/storage/evidence (`services/image_service.py`, `evidence_service.py`) | DO NOT TOUCH | Originals preserved byte-for-byte; crops + bbox provenance; path-safe file access. |
| Reports (`services/report_service.py`) | WORKS | PDF distinguishes all five evidence classes; violation-status legend. |
| `/status` endpoint | WORKS | Honest per-component health. |

## OCR / CV / extraction

| Area | Verdict | Notes |
|---|---|---|
| RapidOCR engine + preprocessing variants | WORKS | Local, offline, CPU-only. |
| Multi-pass confidence-gated OCR (`ocr/multipass.py`) | WORKS | Targeted passes only for uncertain fields; pass provenance logged per line. |
| Numeric error recovery (`ocr/textnorm.py`) | WORKS | Field-scoped; raw always preserved; partial raw parses superseded correctly. |
| Label-value association (`extraction/association.py`) | WORKS | bbox/proximity based; currency-bridge lines. |
| Field schema knowledge layer (`extraction/field_schema.py`) | WORKS | Anchors derived from schema — generalizes, no product hard-coding. |
| Manufacturer/identity semantic extraction | WORKS (was WEAK) | Anchor-vs-entity fix; brand/product/common-name separation. |
| Date duration semantics + consistency engine | WORKS | `{type: DURATION_FROM_REFERENCE}`; mfg↔BB↔use-by coherence check. |
| Field-specific validators (`normalization/validators.py`) | WORKS | Phone/email/website/FSSAI/quantity structure validation. |
| Multilingual duplicate-evidence policy (`ocr/multilingual.py`) | WORKS | Cross-script duplicates reinforce; 0 false conflicts in harness. |
| Weak synthetic detection (phone-no-keyword, unlabeled batch, plain-value dates, origin variants) | WEAK | Documented in BASELINE_METRICS.md §2; anchor-coverage gaps, some by design (evidence-first). |

## Testing & benchmarking

| Area | Verdict | Notes |
|---|---|---|
| Backend suite (incl. 23 safety-invariant regressions) | WORKS | 101/101 green. |
| Synthetic variation generator + robustness harness (`backend/testing/`) | WORKS | Seeded, streaming, >10M enumerable space, failure classification, markdown report. |
| Real-image benchmark execution | MISSING | Manifest + ground-truth scaffolding exists (`data/benchmark/`); no measured run, so no accuracy claims. |
| Active-learning loop from human corrections | MISSING | Corrections are fully audited (raw material exists); no dataset-accumulation pipeline yet. |

## Documentation

WORKS: `docs/ARCHITECTURE.md`, `docs/LEGAL_RULE_ENGINE.md`, `docs/LIMITATIONS.md`,
`docs/TESTING.md`, `docs/SCANNING_ACCURACY_REPORT.md` (methodology), `README.md`,
`BASELINE_METRICS.md`, `PROGRESS_LOG.md`, `UI_AUDIT_REPORT.md`, `UI_PROGRESS_LOG.md`.

## Summary of standing risks

1. Weakest extraction areas are known and measured (see BASELINE_METRICS.md §2); fixes must
   stay general (schema/anchor widening), never test-product-specific.
2. No real-image accuracy claim is possible yet — the benchmark must be executed before any
   field-level real-world number is reported.
3. Security-sensitive modules are classified DO NOT TOUCH unless a defect is demonstrated.
