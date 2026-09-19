# UI Audit Report — POCKET (Phase 0)

Scope: `frontend/src` — 10 pages, 1 component file, 1 stylesheet, app shell, api client.
Method: full read of every page/component plus backend contract checks (evidence bbox format,
violations endpoint, /status payload, fileUrl auth). Everything below was verified against the
running system (backend :8001, vite :5174).

## Verdict per area

| Area | Verdict | Notes |
|---|---|---|
| `App.tsx` shell | REFINE | Sidebar works; emoji-as-icons in nav; no top bar; brand block fine but tagline wraps awkwardly; logout link uses emoji. |
| `Login.tsx` | KEEP (minor refine) | Real labels, autofill attrs, honest disclaimer. Demo credentials paragraph is dev-only noise for a gov-style UI — move to a `<details>`. |
| `styles.css` tokens | REFINE | Good semantic status palette exists but flat/undocumented; pill-radius on badges (anti-pattern per spec); no focus-visible styling; single 860px breakpoint (no tablet tier). |
| `Badges.tsx` | REFINE | Badge/Conf/Card are functional. Color is often the sole signal (spec: color+icon+text). Pill radius 999px everywhere. |
| `Dashboard.tsx` | REFINE | Real counts only (good, no fake data). Priority is wrong: Review queue and violations sit below trend/status charts which have lower operational value. Trend chart is honest but low-value. |
| `NewInspection.tsx` | KEEP/REFINE | Drag-drop, role assignment, honest staged pipeline (done/running/pending/failed — never fake %). Upload screen lacks the short capture guidance required by spec. |
| `Inspections.tsx` | KEEP | Backend-supported search/filter/pagination only. Empty state too bare ("No inspections match."). |
| `InspectionDetail.tsx` | REDESIGN (highest value) | Content is correct and honest but evidence opens crop in a NEW TAB — no field↔region highlighting, no split-view. No numbered sections; "Final decision" is a dl row rather than a hierarchy-prominent block. Review history/report cards ok. Dialogs (confirm-absent/edit/add) are proper modals with required reasons — KEEP logic, restyle. |
| `RuleLibrary.tsx` | KEEP | Versioned, sourced, honest disclaimer. |
| `Products.tsx` | KEEP | Honest empty state. |
| `Violations.tsx` | REFINE | Honest copy ("Missing OCR evidence alone never creates a violation"). No severity/status filter; "common types" analytics block is fine but ordering puts analytics before the actionable table. |
| `Reports.tsx`, `Settings.tsx` | KEEP | Settings limitations list is excellent and honest. |
| Component library | BUILD | No shared EmptyState/Skeleton/Toast/EvidenceViewer/SectionHeader; every page re-implements inline styles (dozens of ad hoc `style={{...}}` values). |

## Anti-patterns found (with locations)

1. **Emoji-as-UI** — `App.tsx` nav (📊 🆕 🗂️ 📦 ⚠️ 📄 ⚖️ ⚙️ ⏻). Spec: eliminate. → Replace with no
   icons or a consistent minimal SVG set; nav labels alone are unambiguous.
2. **Pill radius everywhere** — `.badge { border-radius: 999px }` — all status chips are pills.
   → Two-tier radius: badges/buttons get small radius; cards/inputs medium.
3. **Color as sole signal** — `.stat` red/green/amber numbers; `Conf` uses ✓/⚠/✗ but stats don't.
   → Add text labels (already present) + small status dot/icon pairing; ensure contrast.
4. **Inline ad hoc styles** — 40+ occurrences across Dashboard/InspectionDetail (margins, widths,
   font sizes). → Move to utility classes during redesign of each page.
5. **No keyboard/ARIA affordances** — modal has no focus trap/Escape handling; table rows not
   selectable via keyboard; no `aria-live` for async status changes.
6. **No skeleton/loading states** — pages render "Loading…" text instead of content skeletons.
7. **Search box styling** — `Inspections.tsx` applies `.field` class to an input outside a
   `.field` wrapper; inconsistent with form styles.
8. **Accessibility contrast** — `--slate-light #64748b` on white is 4.6:1 (ok), but `.muted` at
   12.5px is used for legally significant explanations; bump hierarchy usage, not color.
9. **No top bar / page context** — spec asks for page context + user identity in a top bar;
   user identity currently only in sidebar bottom.
10. **Review-queue priority** — dashboard shows trend/status distribution above review queue;
    spec priority: pending review > non-compliant > recent > overview.

## Things explicitly NOT to break (verified working)

- Honest evidence semantics (MISSING ≠ absent; no confidence on empty fields; CONFIRM_ABSENT modal).
- Real pipeline stage statuses from `/inspections/{id}/progress` polling.
- Real data only on dashboard; honest empty states already present on most tables.
- RBAC gating (canEdit) on all mutating actions; VIEWER gets read-only UI.
- Backend contracts used: `/dashboard/stats`, `/inspections` (+search/decision/page),
  `/inspections/:id` (fields/rules/violations/evidence/review_actions/reports),
  `/review/field` (CONFIRM_ABSENT, EDIT_FIELD, ADD_FIELD), `/review/note`, `/review/final`,
  `/reports/generate/:id`, `/files/{kind}/{name}` (token query param), `/violations`, `/products`,
  `/rules`, `/status`. Evidence `bbox` is a parseable "(x1, y1, x2, y2)" string with `image_id`
  — sufficient for the split-view highlight overlay without backend changes.

## Redesign scope (Phase order)

REDESIGN: InspectionDetail (split-view evidence workspace + sectioned layout + decision block).
REFINE: App shell (sidebar + top bar, no emoji), Dashboard (reorder by attention priority),
NewInspection (capture guidance strip), Violations (filters + order), styles (tokens, radius,
focus, skeletons), Badges (semantic statuses with icon+text), Login (minor).
BUILD: components — EmptyState, Skeleton, Toast, EvidenceViewer (zoom/pan/highlight),
SectionHeader, StatusDot. All additive; no page is deleted; no API contract changes.
