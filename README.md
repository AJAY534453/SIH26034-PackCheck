# PACKCHECK AI — AI-Assisted Legal Metrology Inspection System for Packaged Commodities

**SIH 2026 · Problem ID SIH26034 · Team T_IDEA SUPER KINGS · Theme: Miscellaneous · Category: Software**

> **SCAN → EXTRACT → VERIFY → ALERT → REPORT**

PACKCHECK AI is an AI-assisted inspection platform for packaged commodities under the **Legal
Metrology Act, 2009** and the **Legal Metrology (Packaged Commodities) Rules, 2011** (as amended).
It scans package images, extracts declarations, normalizes them, classifies the product, determines
which rules apply, evaluates them deterministically, preserves visual evidence, and produces
explainable inspection reports for human review — plus the consumer-facing tools from the SIH
proposal: **Bill Scanner** (billed price vs printed MRP), **Grocery** (expiry tracking for items the
user explicitly adds) and a **Complaint Center**.

**Engineering principle (enforced throughout):**

> **AI perceives → Python validates → Rules decide → Evidence explains**

Extraction ≠ Validation ≠ Legal decision. The vision model and the OCR engine only *perceive*: both
produce candidate readings that the deterministic pipeline normalizes, validates, ranks and — where
sources disagree — hands to a human as a conflict. Uncertainty is surfaced as **NEEDS MANUAL
REVIEW**, never forced into pass/fail. This is inspection-assistance software, **not** a legal
authority and **not** a replacement for an authorized Legal Metrology officer.

> The backend module namespace and env-var prefix remain `pocket`/`POCKET_*` (internal naming).
> Every user-visible surface is branded **PACKCHECK AI**.

---

## Quick start (₹0, offline, Windows laptop)

```bash
# 1. Python environment (3.14 or 3.11+)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Frontend
cd frontend
npm install
cd ..

# 3. Run backend (port 8001) and frontend (port 5174)
.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
cd frontend && npm run dev
```

Or just run **`start.bat`**, which starts both and waits until the backend actually answers
(`GET http://127.0.0.1:8001/health`) before opening the browser.

> **If the login screen says "backend unreachable":** the API is not running (or is still
> starting — RapidOCR/ONNX can take ~30s cold). The dev server answers such requests with a
> distinguishable `503` and the UI names the exact command to run; a bare `Request failed (500)`
> with no explanation is never shown. Check `http://127.0.0.1:8001/health`.

Open **http://localhost:5174** and log in:

| Username | Password | Role |
|---|---|---|
| admin | admin123 | ADMIN |
| inspector | inspector123 | INSPECTOR |
| viewer | viewer123 | VIEWER |

Everything runs locally by default: OCR is RapidOCR (bundled ONNX models, CPU), the database is
SQLite, the evidence store is a local folder. No key, no internet and no paid API is required —
the whole pipeline, the rule engine, the reports and every consumer tool work fully offline.

### Optional vision provider (off by default, never required)

Set `GEMINI_API_KEY` (and optionally `AI_MODEL`) in the **server** environment to add a vision model
as a second perception source. Its readings join the candidate pool and are:

- validated and normalized by the **same** deterministic code as OCR candidates,
- **corroborated against the on-device OCR** — a reading the OCR cannot match is capped in
  confidence and recorded as UNCERTAIN ("offered for human confirmation, not asserted as a fact"),
- never allowed to bypass ranking, conflict detection, the rule engine or human review.

The key is read server-side only, is never sent to the browser, never logged, and no endpoint
returns it. No new Python dependency is needed for this: the provider is called over HTTPS with the
standard library. With no key configured the UI states plainly that it is running OCR-only.

## The 60-second demo

1. Log in as **inspector** → **New Inspection**
2. Drag in package photos — front, back and side belong to ONE inspection
3. Assign roles (FRONT / BACK / LEFT_SIDE / CLOSE_UP…) → **Start scan**
4. Watch the 15-stage pipeline progress (vision extraction is listed and marked *skipped* when no
   provider is configured — the stage list never claims work that did not run)
5. The result panel shows: critical items, the declarations detected with confidence, what was
   **not detected in the supplied images** (never "absent"), rule-by-rule results and image quality
6. Open the inspection: fields with confidence bars, evidence crops, rule evaluations with reasons
7. Review: edit a field, add a note, finalize a decision, generate the **PDF report**
8. **Bill Scanner** → upload/add a bill, compare billed price with MRP, raise a complaint
9. **Grocery** → explicitly add the scanned product, then see expiry alerts for it only
10. **Complaint Center** → file, follow the timeline, move it along (INSPECTOR/ADMIN)

## What Pocket does (and deliberately does not)

| It does | It does not |
|---|---|
| Detect declarations present on label images | Prove the physical quantity inside the package |
| Extract MRP **separately from unit sale price** | Verify the price actually charged at sale |
| Check versioned Rule 6/7/9/13 requirements with evidence | Measure font size in millimetres without calibration |
| Compare package declarations with an online listing (potential inconsistency) | Declare a package/listing difference a breach of any provision |
| Report where each declaration was photographed (placement evidence) | Certify a photographed surface as the principal display panel |
| Report compliance **and** evidence coverage separately, itemised | Present one blended percentage as if it measured compliance alone |
| Flag conflicts for human resolution (never auto-pick) | Silently merge disagreeing OCR results |
| Degrade to NEEDS MANUAL REVIEW when uncertain | Turn missing OCR into an automatic violation |

## Roles and the audit trail

| Capability | ADMIN | INSPECTOR | VIEWER |
|---|---|---|---|
| View inspections, products, violations, reports, rules | ✓ | ✓ | ✓ |
| Create inspections / upload images / run processing | ✓ | ✓ | — |
| Review fields, resolve conflicts, decide violations, finalize | ✓ | ✓ | — |
| Read the global append-only **Audit Log** | ✓ | — | — |
| Scan a bill, track grocery items, file a complaint | ✓ | ✓ | ✓ |
| Move a complaint through its lifecycle (under review → resolved) | ✓ | ✓ | — |

Every mutating action (login, upload, processing, field correction, absence confirmation,
violation CONFIRM/DISMISS/RESOLVE, review note, final decision, report generation) is appended
to an immutable audit trail with actor, timestamp, before/after and reason. There is no API path
that edits or deletes an audit entry.

## Keeping existing inspections corrected (reprocessing & migration)

An inspection analysed by an older engine keeps that analysis until it is deliberately regenerated.
The engine version is stamped on every processed record, so the ones that need it are listed rather
than guessed at, and regeneration happens **in place** — the record is updated, never duplicated.

```bash
.venv\Scripts\python scripts/reprocess_legacy.py                 # dry run: what would change
.venv\Scripts\python scripts/reprocess_legacy.py --apply         # migrate every legacy record
.venv\Scripts\python scripts/reprocess_legacy.py --apply --ids 54,56,58
```

Each run stores a full BEFORE / AFTER snapshot on an `inspection_revisions` row (actor, reason,
engine version, rule-set fingerprint) and appends the event to the audit log. Human corrections and
any recorded official decision are preserved. In the application:

- **Inspection page → “Analysis provenance & reprocessing”** — which engine produced the active
  result, whether it is still current, the revision history and the before/after comparison, plus a
  controlled *Reprocess inspection* action for anyone who may run the pipeline.
- **Settings → “Reprocessing & migration”** (administrator) — the count of records still on an older
  engine, a background bulk run with live progress and per-record outcomes. The API enforces the same
  capability (`users.manage` for the bulk run, `inspections.manage` for a single record).

## Evidence you can point at

Every assertion in the review is traceable to the exact print it came from: each detected
declaration keeps its source image, bounding box, retained crop, raw OCR text, confidence and
method, and the rule evaluations name the evidence they rest on. In the inspection page,
**View evidence** opens the image with the region highlighted (use it from a declaration row or from
a rule conclusion). Where the recogniser fused words (`AYURVEDIC SOAPWITH18HERBS`), the display value
is repaired from a packaging vocabulary while the raw OCR text stays untouched beside it.

## PRO — the in-application assistant

PRO answers from the application's own knowledge base, its versioned rule data and the caller's own
records. It greets the signed-in user by name and role, answers about the record that is currently
open (“why does this need finalization?”, “what evidence supports the MRP?”, “what changed in the last
reprocess?”), cites its sources, separates application guidance from legal references, never
describes a record outside the caller's scope, and signs off briefly (“Have a good day, Admin.”).

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system design (incl. online listings, placement analysis, the report-status model, reprocessing and PRO)
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — local setup, environment variables, production deployment, storage, backup, monitoring, maintenance and extensibility
- [`docs/API_DOCUMENTATION.md`](docs/API_DOCUMENTATION.md) — endpoint reference (auth, roles, payloads)
- [`docs/LEGAL_RULE_ENGINE.md`](docs/LEGAL_RULE_ENGINE.md) — rules, applicability, decision logic, the compliance/coverage split and the pass gate
- [`docs/SIH26034_TRACEABILITY.md`](docs/SIH26034_TRACEABILITY.md) — requirement → feature → test → screen
- [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) — legal & technical limitations
- [`docs/TESTING.md`](docs/TESTING.md) — test & benchmark methodology
- [`data/README.md`](data/README.md) — dataset architecture, annotation schema, split policy, RPC policy
- [`docs/SCANNING_ACCURACY_REPORT.md`](docs/SCANNING_ACCURACY_REPORT.md) — measured results only
- Interactive API reference: run the backend and open `http://127.0.0.1:8001/docs` (OpenAPI)

## Tests

```bash
.venv\Scripts\python -m pytest backend/tests -q          # full suite
.venv\Scripts\python -m pytest backend/tests -m "not slow" -q   # fast subset
.venv\Scripts\python scripts/e2e_check.py                # full pipeline on a rendered label
.venv\Scripts\python scripts/run_benchmark.py            # real-image benchmark (manifest + dataset)
.venv\Scripts\python scripts/dataset.py validate          # dataset integrity + leakage check
.venv\Scripts\python scripts/dataset.py split             # product-level train/val/test splits
cd frontend && npx tsc --noEmit && npm run build          # frontend gates
```

`scripts/e2e_check.py` walks the whole demo: login → create → upload → quality → process →
fields → evidence bboxes → rules → human review → violation confirm/dismiss → final decision →
PDF → CSV → JSON → audit trail → auth-gated evidence access.
