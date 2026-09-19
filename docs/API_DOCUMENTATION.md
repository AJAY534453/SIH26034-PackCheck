# POCKET — API Documentation

Base URL (local): `http://127.0.0.1:8001`

A live OpenAPI/Swagger UI is served at **`/docs`** and the raw schema at **`/openapi.json`**.
This document is the human-readable companion: what each endpoint does, who may call it, and what
the payloads mean.

## Authentication

All endpoints except `/`, `/status`, `/auth/login` and the legacy `/upload` require a bearer token:

```
Authorization: Bearer <access_token>
```

Evidence files (`/files/...`) additionally accept `?token=<access_token>` because `<img>` and
direct downloads cannot set headers.

| Role | May do |
|---|---|
| `VIEWER` | Read-only: inspections, products, violations, reports, rules, dashboard, exports, evidence |
| `INSPECTOR` | Everything a VIEWER can, **plus** create inspections, upload images, run processing, review fields, decide violations, finalize, generate reports |
| `ADMIN` | Everything an INSPECTOR can, **plus** read the global audit log |

Unauthorized calls return `401` (no/invalid token) or `403` (authenticated, insufficient role).

---

## 1. System

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | none | Application name, version, OCR availability |
| GET | `/status` | none | **Honest health check** — reports `ok` only when every required subsystem (database, storage, OCR) is genuinely available; otherwise `degraded` with per-subsystem booleans |
| POST | `/upload` | none (legacy) | Back-compat: creates an inspection with one image and processes it immediately. New work should use the `/inspections` flow. |

---

## 2. Authentication — `/auth`

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| POST | `/auth/login` | none | `{"username": str, "password": str}` | `{"access_token": str, "token_type": "bearer", ...}` |
| POST | `/auth/logout` | any token | — | `{"message": ...}`; logout is audited |
| GET | `/auth/me` | any token | — | Current user (username, role) |

Every successful login and logout is appended to the audit trail.

---

## 3. Inspections — `/inspections`

### Lifecycle

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/inspections` | ADMIN, INSPECTOR | Create a new inspection; returns the inspection summary with its generated `inspection_number` |
| GET | `/inspections` | any token | Paged list. Query: `page`, `page_size`, `decision`, `search` |
| GET | `/inspections/stages` | any token | The ordered pipeline stage names (for progress UI) |
| GET | `/inspections/{id}` | any token | Full detail: images, fields, evidence, rules, violations, review actions, reports, classification signals, summary and final decision |
| POST | `/inspections/{id}/images` | ADMIN, INSPECTOR | Multipart upload: `file`, `role`. Validates extension, magic bytes, dimensions, size and duplicates. Stores the original with a checksum and returns quality assessment |
| POST | `/inspections/{id}/process` | ADMIN, INSPECTOR | Run the full pipeline (quality → preprocessing → multi-pass OCR → fusion → extraction → normalization → classification → applicability → deterministic evaluation → evidence). Idempotent: safe to re-run (reprocess) |
| GET | `/inspections/{id}/progress` | any token | Current `status`, per-stage `stage_status`, and the decision so far (used to poll long OCR runs without freezing the UI) |
| GET | `/inspections/{id}/export` | any token | `?format=json|csv|pdf`-style export. `json` = complete machine-readable record; `csv` = structured fields with state and confidence |

### Human review

| Method | Path | Auth | Body | Effect |
|---|---|---|---|---|
| POST | `/inspections/{id}/review/field` | ADMIN, INSPECTOR | `{"action": "ACCEPT"\|"EDIT_FIELD"\|"ADD_FIELD"\|"REJECT"\|"CONFIRM_ABSENT", "field_name": str, "corrected_value": str?, "reason": str}` | Applies the reviewer's decision, re-runs the deterministic evaluation, and appends an audit entry. **`CONFIRM_ABSENT` is the only image-side path that can turn a missing declaration into a rule FAIL** — and it requires a recorded reason and the inspector's identity |
| POST | `/inspections/{id}/review/note` | ADMIN, INSPECTOR | `{"note": str}` | Adds a free-text review note to the audit trail |
| POST | `/inspections/{id}/review/final` | ADMIN, INSPECTOR | `{"decision": "COMPLIANT"\|"NON_COMPLIANT"\|"NEEDS_MANUAL_REVIEW", "reason": str}` | The human's final decision, recorded as the human's call and superseding the system-proposed one |
| POST | `/inspections/{id}/review/violation/{violation_id}` | ADMIN, INSPECTOR | `{"action": "CONFIRM"\|"DISMISS"\|"RESOLVE", "reason": str}` | Human decision on one violation. **`DISMISS` and `RESOLVE` require a reason**; the deterministic engine is re-run so the final decision reflects the human call (never a silent override). `CONFIRM` keeps it driving NON_COMPLIANT. The decision is durable across later re-evaluations |

Responses from the violation endpoint: `{"violation_id": int, "rule_number": str, "status": str, "decision": str}`.
Errors: `422` (no reason supplied, unknown action, violation not on this inspection), `403` (insufficient role), `404` (unknown inspection).

---

## 4. Dashboard, catalogue and rules

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/dashboard/stats` | any token | **Action-oriented, live database figures only**: `needs_review`, `non_compliant`, `compliant`, totals, `conflicts`, `open_violations`, `confirmed_violations`, `common_violations`, `trend`, `recent_inspections`, `review_queue`, `conflict_queue` |
| GET | `/products` | any token | Tracked products with first/last seen and per-product inspection count |
| GET | `/products/{id}` | any token | One product record |
| GET | `/violations` | any token | All violation rows joined with inspection number and product |
| GET | `/reports` | any token | Generated reports with format, timestamp and author |
| POST | `/reports/generate/{inspection_id}` | ADMIN, INSPECTOR | Generate the PDF report (evidence-referenced) for an inspection |
| GET | `/rules` | any token | Rule library: number, title, description, applicability, source reference, current version, requirement |
| GET | `/rules/{rule_id}` | any token | One rule with its version history |
| GET | `/audit` | **ADMIN only** | Append-only audit trail, newest first. Query: `action`, `actor`, `inspection`, `limit` (≤500), `offset`. Returns `{items, total, actions}`. There is **no** write/update/delete method for the trail |

### Audit entry shape

```json
{
  "id": 122,
  "actor": "inspector",
  "action": "violation_dismiss",
  "inspection_id": "INS-2026-000017",
  "before": "CONFIRMED",
  "after": "DISMISSED",
  "reason": "rule 6(1)(e): MRP is present and legible on the physical label",
  "created_at": "2026-09-14 16:39:21"
}
```

Recorded actions include: `login`, `logout`, `inspection_created`, `image_uploaded`,
`processing_completed`, `field_accepted`, `field_corrected`, `absence_confirmed`,
`inspection_reviewed`, `violation_confirm`, `violation_dismiss`, `violation_resolve`,
`decision_finalized`, `report_generated`.

---

## 5. Evidence files

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/files/{kind}/{filename}` | any token (header **or** `?token=`) | Serve a stored file. `kind ∈ {originals, processed, crops, reports}`. The filename is reduced to its basename so path traversal is impossible; unknown kinds and missing files return `404`. Originals are never publicly exposed |

---

## 6. Errors

| Status | Meaning |
|---|---|
| 400 | Malformed request |
| 401 | Missing or invalid token |
| 403 | Authenticated but the role is not permitted |
| 404 | Unknown resource (also used for traversal attempts and unknown file kinds) |
| 422 | Semantic validation failure (bad action, missing required reason, failed upload validation) |
| 500 | Internal error — the client receives a generic message; stack traces are never leaked |

---

## 7. Field states in responses

| State | Meaning |
|---|---|
| `DETECTED` | Reliable evidence supports the value |
| `UNCERTAIN` | Possible evidence exists but association/confidence is insufficient |
| `CONFLICTING` | Credible sources disagree — never auto-resolved; requires human review |
| `MISSING` | Not found **in the supplied images**. This never implies absence from the package |
| `MANUALLY_CORRECTED` | A reviewer corrected the value; the original is preserved in the audit history |
| `HUMAN_CONFIRMED_ABSENT` | The inspector physically verified the declaration is absent (positive evidence for rule purposes) |

Rule results are `PASS`, `FAIL`, `UNCERTAIN`, `NOT_APPLICABLE`. Final decisions are `COMPLIANT`,
`NON_COMPLIANT`, `NEEDS_MANUAL_REVIEW`.

---

# Consumer tools & vision status

All of these require a valid bearer token like every other endpoint.

## Vision provider status

| Method | Path | Notes |
|---|---|---|
| GET | `/ai/status` | `{enabled, provider, model, reason, mode, fields}`. `enabled=false` means the on-device OCR pipeline is running alone — the `reason` says so in words. **The API key is never returned, in any field, under any status.** |

`GET /status` additionally reports `components.vision_provider` (informational only: a vision provider
is optional and never makes the service "degraded").

## Bill Scanner

| Method | Path | Body / params | Returns |
|---|---|---|---|
| POST | `/bills/scan` | multipart `file` (+ optional `store_name`) | `{bill, readings, ai, message}`. The image is validated and stored immutably in the `bills` evidence folder. With no provider configured, `readings` is empty and `message` explains why — no values are invented. |
| POST | `/bills` | `{source, product_name, billed_price, mrp, quantity, store_name, bill_number, notes, inspection_id?, product_id?}` | `{bill, message}`. `source` must be `manual` or `demo`; `demo` marks sample values that are labelled as sample data everywhere they appear. |
| PATCH | `/bills/{id}` | any subset of the same fields | `{bill, message}` — re-runs the comparison deterministically; a corrected reading is recorded as `vision+manual` |
| GET | `/bills`, `/bills/{id}` | — | list / single bill |
| GET | `/bills/{id}/evidence` | — | `{kind: "bills", stored_filename}` for the protected evidence viewer |

`comparison_status` is one of `POTENTIAL_PRICE_DIFFERENCE`, `PRICE_AT_OR_BELOW_MRP`,
`INSUFFICIENT_DATA`. It is arithmetic, computed in Python from the stored values — the vision
provider only reads printed text.

## Grocery

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/grocery` | `{product_name*, brand, quantity, mrp, batch_lot, purchase_date, expiry_date, best_before_text, mfg_date, inspection_id?, product_id?, notes}` | The **only** way an item starts being expiry-tracked. Returns `{item, expiry_note, message}`; `expiry_note` explains a duration that could not be anchored. |
| GET | `/grocery` | — | `{items, total, alerts}` with live `days_remaining` and `alert` per item |
| PATCH | `/grocery/{id}` | `{expiry_date?, purchase_date?, notes?}` | Setting a printed expiry switches the basis to `PRINTED_ON_PACKAGE` |
| DELETE | `/grocery/{id}` | — | Owner or ADMIN only |

`alert` ∈ `FRESH`, `EXPIRING_SOON` (≤7 days), `EXPIRED`, `NO_EXPIRY_DATA`.
`expiry_basis` ∈ `PRINTED_ON_PACKAGE`, `DURATION_FROM_MANUFACTURING_DATE` (provisional),
`DURATION_ONLY_NO_ANCHOR`, `NOT_DECLARED`, `NOT_PROVIDED`.

## Complaint Center

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/complaints` | `{issue* (≥10 chars), product_name, store_name, category, severity, inspection_id?, bill_id?, product_id?}` | Creates the complaint with the first timeline entry |
| GET | `/complaints`, `/complaints/{id}` | — | `{items, total, by_status, stages}` |
| POST | `/complaints/{id}/advance` | `{status, note?}` — **ADMIN/INSPECTOR only** | Appends to the timeline; `RESOLVED` also stores the resolution text |

Status machine: `SUBMITTED → UNDER_REVIEW → EVIDENCE_VERIFIED → RESOLVED`. The timeline is
append-only history (actor + timestamp + note per entry) and every transition is audited.

## Dashboard additions

`GET /dashboard/stats` now also returns live counts for the consumer tools — `bills`,
`bills_total`, `potential_price_differences[]`, `grocery_items`, `grocery_alert_count`,
`grocery_alerts[]`, `complaints`, `open_complaints`, `recent_complaints[]` — plus `ai` (the same
shape as `/ai/status`). No counter on the dashboard is static or illustrative.
