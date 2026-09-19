# UI Progress Log — POCKET enterprise UI/UX upgrade

## Hotfix — full-width shell (reported 1366×768 defect)
- **Root cause:** `.app-shell` was a flex ROW while the sidebar is `position: fixed` (out of
  flow). The in-flow `.topbar` and `.main` therefore became side-by-side flex items: the topbar
  shrank to its content width and `.main` got only the leftover sliver (compounded by its
  `max-width: 1400px` cap) → huge blank area between sidebar and content; topbar stopped halfway.
- **Fix (2 shell rules only, no component changes):** `.app-shell { flex-direction: column }`;
  removed `.main` max-width. Topbar keeps its own `margin-left: var(--sidebar-w)` so it spans
  from the sidebar edge to the viewport right edge at every width.
- **Verified live:** gap between sidebar and content = 0px; topbar right edge = viewport right
  edge; dashboard + inspection detail fill available width; no horizontal scroll; build green.

## Phase 0 — Audit (complete)
- Output: `UI_AUDIT_REPORT.md` (verdicts per page, 10 located anti-patterns, protected behaviors list).
- Files read: all 10 pages, `Badges.tsx`, `App.tsx`, `styles.css`, `services/api.ts`, `types.ts`.
- Backend contracts verified: evidence bbox string format + `image_id`, `/status` payload,
  violations endpoint shape, `fileUrl` token-auth for `<img>`.

## Phase 1 — Design tokens + component library (complete)
- `styles.css`: documented token block (semantic status colors, spacing scale `--s1..--s8`,
  radius tiers `--r-sm/md/lg`, typography scale, `--shadow-overlay`); `:focus-visible` global style.
- New `components/ui.tsx`: `StatusBadge`/`StatusChip` (icon+text, never color alone),
  `Confidence`, `SectionHead`, `Skeleton`, `EmptyState`, `useToast`, `DecisionBanner`.
- New `components/Modal.tsx`: Escape-to-close, backdrop click, autofocus, `role=dialog aria-modal`.
- Badge de-pilled (small radius, border tiers, dot marker); buttons/inputs/cards use radius tiers.

## Phase 2 — App shell (complete)
- `App.tsx`: emoji nav removed → text-only sidebar with left-bar active indicator (3 signals:
  background + weight + bar). Added top bar with real page context, signed-in identity, role chip.
- Mobile tier (<640px) hides topbar; tablet (≤1024px) collapses sidebar to 64px rail.

## Phase 3 — Dashboard (complete)
- Reordered to attention priority: Pending review stats → Review queue + Common violations →
  Recent inspections → Compliance overview + Repository. Decorative trend chart removed.
- Skeleton while loading; `EmptyState` with actionable "New Inspection" button for empty data.

## Phase 4 — New Inspection (complete)
- Capture-guidance strip (4 short tips). Pipeline stages keep honest done/running/failed states;
  `aria-live=polite` added. Result uses `StatusBadge`.

## Phase 5+6 — Inspection Detail (complete — most important page)
- Numbered document sections 1–7: Package evidence → Extracted declarations →
  Classification → Rule evaluations → Potential violations → Manual review → History.
- `DecisionBanner`: prominent final decision with evidence-based "why" line; no red/green takeover.
- New `components/EvidenceViewer.tsx`: split view — package image (zoom 100–400%, Fit, image
  switcher) + field list. Selecting a field highlights its bbox region on the image (dimming
  overlay, blue rect), shows the crop, raw OCR, interpreted value, source/pass, confidence.
  No confidence displayed for empty values; MISSING explained honestly.
- Conflict UX: `CONFLICTING` fields render a side-by-side conflict-pair block (both crops +
  raw text) — never silently resolved.
- Confirm-absent / Edit / Add-field dialogs preserved (logic untouched) and restyled;
  Finalize now requires a written reason (was hardcoded "Confirmed by reviewer").

## Phase 7 — Rule results + Violations (complete)
- Rule rows: `StatusBadge` (icon+color+text) + reason + observed/expected.
- Violations page: table-first, status/severity filters (fields the API already returns),
  skeleton loading, honest empty states, filtered "most frequent types".

## Phase 10 — States sweep (complete)
- Skeleton loading added to Dashboard, Inspections, Violations, Reports, Products, RuleLibrary, Settings.
- Empty states everywhere explain what's missing + how to fix it (with action buttons where applicable).

## Phase 11 — Accessibility (complete)
- Modal: focus management + Escape + aria-modal + labelled.
- Forms: real `<label htmlFor>` on all inputs (search/filter/dialogs/review note).
- `aria-live` on pipeline stages, zoom %, toast; `aria-selected` listbox semantics in EvidenceViewer.
- `:focus-visible` outlines globally.

## Phase 14/16 — Consistency + stress (complete)
- Fixed DOM-nesting warning (`<td>` inside `<dl>`).
- Stress-tested against inspection INS-2026-000009 (23 fields, long uncertainty reasons,
  long badge labels, 1 image): found and fixed 7 overflow defects:
  `.main` min-width, `.ev-field-row .fval` shrink, `.ev-split` 1fr→minmax(0,1fr),
  `.decision-banner` wrap, badge nowrap in table cells, `.kv` dd overflow, `.ev-tools`/`.flex` wrap.
- Result: zero elements beyond viewport at 639px; `body.scrollWidth == clientWidth`.

## Verification
- `tsc --noEmit` clean; `npm run build` passes (15.51 kB css / 221 kB js gzip 68 kB).
- Backend suite: 101/101 pass (unchanged — UI work touched no backend file).
- Live browser verification: dashboard, inspection detail (evidence highlight, conflict absent
  path, dialog gating, Escape close), screenshots captured.

## Remaining known issues / limitations
- `Badges.tsx` legacy `Badge`/`Conf` still exported for backwards compatibility (Login page and
  some fallbacks); full migration to `ui.tsx` primitives is mechanical and safe to do later.
- Evidence highlight uses percentage-positioned overlay — pixel-accurate at all zooms relative to
  the rendered image box; sub-pixel drift possible with extreme zoom (>4x) — capped at 400%.
- Preview harness could not resize beyond 656px viewport; desktop-1440 layout verified via
  dev-tools-free DOM audit + build, not a full-size screenshot.
- Keyboard table row navigation (arrow keys across field rows) not implemented — listbox is
  tab/click accessible.
