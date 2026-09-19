# Deployment, operations and maintenance

Every command, variable and path below is taken from this repository (`backend/config.py`,
`frontend/vite.config.ts`, `start.bat`, `requirements.txt`). Nothing here is aspirational.

---

## 1. What the system is made of

| Piece | Implementation | Where |
|---|---|---|
| API + pipeline | FastAPI + SQLAlchemy + uvicorn | `backend/` |
| Vision/OCR | RapidOCR (ONNX Runtime), OpenCV, Pillow | `backend/ocr/`, `backend/preprocessing/` |
| Optional vision model | Gemini (`AI_PROVIDER=gemini`), **off by default** | `backend/ai/` |
| Rule data | Versioned JSON rule definitions | `backend/rules/definitions/` |
| Database | SQLite file | `storage/pocket.db` (default) |
| File storage | Originals, processed variants, evidence crops, reports | `storage/originals`, `storage/processed`, `storage/crops`, `storage/reports` |
| UI | React 19 + Vite + TypeScript | `frontend/` |
| Reports | PDF (ReportLab) with evidence attachments | `backend/services/report_service.py` |

Two processes only: the API and (for development) the Vite dev server. There is no message
broker, no cache server and no external service in the default configuration — the whole system
runs offline on one machine, which is the SIH constraint.

---

## 2. Local development

### 2.1 First run

```bash
# from the repository root
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt      # POSIX

cd frontend && npm install && cd ..
```

`start.bat` does all of the above automatically (creates the venv, installs both sides, starts
both processes, and waits until the API actually answers before opening the browser).

### 2.2 Running the two processes

```bash
# API on :8001
.venv/Scripts/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001

# UI on :5174 (proxies /api -> http://127.0.0.1:8001)
cd frontend && npm run dev
```

Notes that cost real debugging time:

* the first API start takes ~20–30 s while RapidOCR/ONNX initialise. The UI opening early shows a
  login error that is really "backend not up yet"; Vite answers `503` with that explanation.
* Vite binds `localhost` (IPv6 `[::1]:5174` on Windows). `http://localhost:5174` works,
  `http://127.0.0.1:5174` may not.
* seeding runs on startup: `Base.metadata.create_all` creates missing tables,
  `backend/migrations.py` adds missing columns to existing tables (additive only), and
  `backend/rules/registry.py` seeds/versions the rule library. No manual migration step exists.

### 2.3 Seeded accounts

`POCKET_DEMO_MODE=1` (the default) seeds the demo accounts used by the tests and the demo script:
`admin/admin123`, `inspector/inspector123`, `viewer/viewer123` plus the organisation-scoped roles.
**Set `POCKET_DEMO_MODE=0` for anything that is not a demonstration.**

---

## 3. Environment configuration

All backend settings are environment-overridable (`backend/config.py`). Copy the list into an
`.env` file at the repository root (loaded with `python-dotenv`) or export them in the service
manager. **No secret is committed to source control**; the defaults are development values only.

| Variable | Default | What it controls |
|---|---|---|
| `POCKET_HOST` / `POCKET_PORT` | `127.0.0.1` / `8001` | API bind |
| `PACKCHECK_APP_NAME` | `PACKCHECK AI` | Display name |
| `POCKET_FRONTEND_ORIGIN` | `http://localhost:5174` | CORS origin |
| `POCKET_DATABASE_URL` | `sqlite:///storage/pocket.db` | Database |
| `POCKET_SECRET_KEY` | dev placeholder | **MUST be replaced in production** (JWT signing) |
| `POCKET_ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | Access-token lifetime |
| `POCKET_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh-token lifetime |
| `POCKET_SECURE_COOKIES` | `0` | `1` behind HTTPS — sets the Secure flag on the session cookie |
| `POCKET_COOKIE_SAMESITE` / `POCKET_COOKIE_DOMAIN` | `lax` / empty | Session-cookie scope |
| `POCKET_LOGIN_MAX_FAILED`, `POCKET_LOGIN_LOCKOUT_MINUTES`, `POCKET_LOGIN_WINDOW_SECONDS`, `POCKET_LOGIN_IP_MAX_ATTEMPTS` | `5`, `15`, `300`, `20` | Progressive brute-force protection |
| `POCKET_PASSWORD_MIN_LENGTH` | `8` | Password policy |
| `POCKET_RESET_TOKEN_EXPIRE_MINUTES` | `30` | Password-reset token lifetime |
| `POCKET_MFA_ISSUER`, `POCKET_MFA_CHALLENGE_EXPIRE_MINUTES`, `POCKET_MFA_RECOVERY_CODE_COUNT` | `PACKCHECK AI`, `5`, `8` | MFA (TOTP) |
| `POCKET_STORAGE_DIR` | `storage` | Where originals/variants/crops/reports live |
| `POCKET_MAX_UPLOAD_MB` | `15` | Upload ceiling (enforced before decoding) |
| `POCKET_DEMO_MODE` | `1` | Seeds demo accounts |
| `POCKET_AI_COMPLIANCE_THRESHOLD` | `85` | **Application policy** review threshold (not a statutory value) |
| `POCKET_AI_COVERAGE_FLOOR` | `70` | Minimum evidence coverage before an automated pass is allowed |
| `AI_ENABLED` | `auto` | `auto` disables the vision model when no key is present; `0`/`1` force it |
| `AI_PROVIDER`, `AI_MODEL`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `AI_TIMEOUT_SECONDS` | `gemini`, `gemini-2.5-flash`, empty, `60` | Optional vision provider |
| `BILL_PRICE_TOLERANCE_PCT` | `0.5` | Bill-scanner price tolerance |
| `POCKET_API_TARGET` | `http://127.0.0.1:8001` | Frontend dev-server proxy target (`frontend/vite.config.ts`) |

The application runs with **none** of the AI variables set: the vision model is an optional
enhancement over deterministic OCR + rule evaluation, and every rule outcome is computed by
deterministic code regardless of whether it is enabled.

---

## 4. Production deployment

### 4.1 Architecture

```
browser
  │  HTTPS
  ▼
reverse proxy (nginx / Caddy / IIS ARR)
  ├── /            → static build of the UI  (frontend/dist)
  └── /api/*       → uvicorn (backend.main:app)          ← the proxy STRIPS the /api prefix
        │
        ├── SQLite file (storage/pocket.db)
        ├── object/file storage (storage/originals|processed|crops|reports)
        └── optional egress → Gemini API (only when a key is configured)
```

The UI in production is a static bundle; only the API is a service. Because the UI calls `/api`,
the reverse proxy must strip that prefix (this mirrors the dev proxy in `frontend/vite.config.ts`,
whose `rewrite` removes `/api`).

### 4.2 Build the frontend

```bash
cd frontend
VITE_API_BASE=/api npm run build      # emits frontend/dist (index.html + hashed assets)
```

Serve `frontend/dist` as static files. Route unknown paths to `index.html` (React Router). Cache the
hashed assets immutably; do not cache `index.html`.

### 4.3 Run the API

```bash
# single process, production-shaped (uvicorn with multiple workers needs the DB to be
# shared, not a per-process SQLite file — see §4.4)
POCKET_SECURE_COOKIES=1 \
POCKET_SECRET_KEY='<64+ random chars>' \
POCKET_DEMO_MODE=0 \
.venv/Scripts/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --workers 1
```

Register it as a service (Windows Service via NSSM, or a systemd unit on Linux). Requirements for
a production deployment:

* `POCKET_SECRET_KEY` replaced (otherwise sessions are forgeable);
* `POCKET_SECURE_COOKIES=1` and HTTPS terminated at the proxy;
* `POCKET_DEMO_MODE=0` (no demo accounts, no demo passwords);
* `POCKET_FRONTEND_ORIGIN` set to the real public origin;
* the storage directory on a persistent volume with a backup job (§7).

### 4.4 Database deployment

SQLite in WAL mode (the default here) is correct for a single-node inspection deployment and is
what the SIH setup runs. It supports **one** writer at a time, so:

* keep `--workers 1` unless you move to a shared database;
* for a multi-node or high-write deployment set
  `POCKET_DATABASE_URL=postgresql+psycopg://user:pass@host/pocketdb` and add the driver to
  `requirements.txt`. The models are plain SQLAlchemy 2.0 and the migration helper is
  dialect-aware, so no application code changes are required;
* never place the SQLite file on a network share.

### 4.5 File/image storage

`POCKET_STORAGE_DIR` is the single root:

```
storage/
  originals/   immutable uploads (never rewritten in place)
  processed/   preprocessing/OCR variants
  crops/       evidence regions (the crops the report embeds)
  reports/     generated PDFs
  pocket.db    database (when the default DATABASE_URL is used)
```

Files are addressed by *stored* filename, never by user-supplied path; every fetch goes through an
ownership check (`backend/api/tools.py`, `/files/{kind}/{filename}`). Point this directory at a
volume with real backups — the database stores only filenames.

### 4.6 AI model / service deployment

The vision model is optional. With `AI_ENABLED=auto` and no `GEMINI_API_KEY`, the pipeline runs
OCR-only and reports `vision_extraction: skipped` in `inspections.stage_status`; a candidate
produced by a vision model is never trusted over the deterministic extractors. If a key is
configured, the deployment needs outbound HTTPS to the provider (and nothing else changes).

### 4.7 Monitoring and logging

* `GET /status` — component health (database, OCR, storage, AI mode). Use it as the readiness
  probe; `GET /health` is the liveness probe.
* `.freebuff/preview-*.log` / `storage/backend.log` in development; in production send uvicorn's
  stdout/stderr to the platform's log collector.
* every state-changing action is additionally written to the **audit log** (`audit.log_action`) and
  is readable in the UI at `/audit`. This is the operational trail for who did what.
* the pipeline writes per-stage status (`inspections.stage_status`) and per-image quality figures,
  so a failure can be located to a stage without reading application logs.

### 4.8 Error handling

* API errors are returned as JSON `{"detail": …}` with a real status code; the UI renders the
  detail rather than a generic "request failed".
* a failed pipeline sets `inspection.status = FAILED` and records the stage; the scan row derives
  to report status `ERROR`.
* upload failures are rejected **before** decoding (size, extension, magic-byte sniffing), so a
  malformed file cannot take the process down.
* the Vite dev proxy converts "backend unreachable" into a `503` with an actionable message.

---

## 5. Backup and recovery

| What | How | Frequency |
|---|---|---|
| Database | copy `storage/pocket.db` **after** `PRAGMA wal_checkpoint(TRUNCATE)` (or use `sqlite3 storage/pocket.db ".backup 'backup.db'"`, which is safe while running) | daily, retained |
| Evidence files | snapshot `storage/originals`, `storage/crops`, `storage/reports` (they are append-only) | daily |
| Configuration | back up the environment/`.env` **outside** the repository (it holds `POCKET_SECRET_KEY`) | on change |

Recovery: restore the database file and the storage directories together — they are a matched pair
(rows reference stored filenames). If only the database is restored, scans keep their results but
their images/crops are missing, and each such row degrades to "no image stored for this scan"
rather than failing. `scripts/recompute_scores.py` can re-derive scores from stored evidence after
a rule change; it never invents evidence.

---

## 6. Testing before deploying

```bash
.venv/Scripts/python -m pytest backend/tests -q      # unit + API + golden fixtures
cd frontend && npx tsc --noEmit && npm run build     # typecheck + production build
.venv/Scripts/python scripts/e2e_check.py            # end-to-end pipeline walk
.venv/Scripts/python scripts/verify_roles.py         # role/permission matrix against a live API
```

`scripts/e2e_check.py` and `scripts/verify_roles.py` **write rows**; run them against a scratch
database (`POCKET_DATABASE_URL=sqlite:///storage/scratch.db`) or clean up afterwards.

---

## 7. Maintenance and extensibility

### 7.1 Change a legal requirement

Edit the rule JSON in `backend/rules/definitions/` (one entry = one requirement: `rule_id`,
`rule_number`, `requirement`, `source_reference`, `amendment`, `effective_from`, `applicability`,
`check_type`, `params`, `weight`, `critical`). On the next start the registry **creates a new rule
version** when the definition changed (the version fingerprint includes `requirement`,
`check_type`, `params`, `applicability`, `weight`, `critical`), and stored evaluations keep the
version they were evaluated against. No UI code changes: the Rule Library reads the rule data.

A new *kind* of check needs a function in `backend/rules/engine.py` registered in `CHECKS`. It must
return `(status, reason, observed, confidence[, detail])` and must never upgrade "not found in the
supplied evidence" into `FAIL`.

### 7.2 Re-score history deliberately

Weights are pinned per evaluation, so history is never silently rewritten. After a deliberate
weight/rule change:

```bash
.venv/Scripts/python scripts/recompute_scores.py --dry-run   # show the effect first
.venv/Scripts/python scripts/recompute_scores.py             # apply
```

### 7.3 Add a compliance check or a listing requirement

* package requirement → `backend/rules/definitions/lm_pcr_2011*.json`;
* online-listing requirement → `backend/rules/definitions/listings/`. That sub-directory is
  deliberately outside the inspection registry's `glob("*.json")`, so a listing requirement can
  never be scored against a photograph of a pack. Each entry carries its own `legal_status`
  (`VERIFIED_PRIMARY`, `VERIFIED_SECONDARY_TEXT`, `LEGAL_REFERENCE_REQUIRES_VERIFICATION`,
  `APPLICATION_POLICY_NOT_A_LEGAL_PROVISION`), which the UI renders verbatim.

Do not invent a legal requirement. If a citation cannot be verified from an authoritative source,
mark it `LEGAL_REFERENCE_REQUIRES_VERIFICATION` — the application surfaces it for the officer
instead of asserting it.

### 7.4 Add a user role

1. `backend/models/enums.py` → `UserRole`;
2. `backend/authz/permissions.py` → the role's explicit permission set, `ROLE_LABELS`, `ROLE_HOME`,
   and `ORG_SCOPED_ROLES` if the role may only see its own organisation;
3. `frontend/src/App.tsx` → a nav entry pointing at a page whose `Guard perm="…"` matches a
   permission the role holds.

Authorization is decided by the backend on every request; the front end only decides what to show.

### 7.5 Scale the product repository

`product_scans` is the durable record and `products` is the deduplicated identity (normalised
name + brand + manufacturer). Search/facets are computed from the same rows the UI lists, so a
large deployment should: move to PostgreSQL (§4.4), add an index on the columns the list filters
on (`review_status`, `ai_verdict`, `category`, `scanned_at`, `compliance_score`), and prefer the
paginated endpoints already in place (`?page`, `?page_size`, capped at 100).

### 7.6 What must not change without a decision

* the **85%** threshold and the evidence-coverage floor are **application policy**, not statutory
  values; presenting either as a legal threshold would be wrong;
* an AI verdict is never final. Finalisation requires `compliance.finalize`, a remark, and is
  recorded with the actor and timestamp in the audit log;
* "not detected in the supplied images" is never recorded as absence — only an inspector's
  confirmed absence may produce a `FAIL` of that kind.
