// Shared UI primitives — the component library. No page may style these ad hoc.
// Status is always icon + text (+ color), never color alone.
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Icon } from './Icon'

// ---------- status semantics: one icon per meaning, reused everywhere ----------
export const STATUS_GLYPH: Record<string, string> = {
  // decisions
  COMPLIANT: '✓',
  NON_COMPLIANT: '✕',
  NEEDS_MANUAL_REVIEW: '!',
  // rule results
  PASS: '✓',
  FAIL: '✕',
  UNCERTAIN: '!',
  NOT_APPLICABLE: '–',
  // field states
  DETECTED: '✓',
  MISSING: '–',
  CONFLICTING: '⚠',
  HUMAN_CONFIRMED_ABSENT: '✕',
  MANUALLY_CORRECTED: '✎',
  // image quality / violations
  GOOD: '✓',
  ACCEPTABLE: '~',
  POOR: '!',
  UNUSABLE: '✕',
  OPEN: '●',
  CONFIRMED: '✕',
  NEEDS_REVIEW: '!',
  HIGH: '↑',
  MEDIUM: '→',
  LOW: '↓',
}

const STATUS_LABELS: Record<string, string> = {
  MISSING: 'NOT DETECTED IN SUPPLIED IMAGES',
  NON_COMPLIANT: 'NON-COMPLIANT',
  NEEDS_MANUAL_REVIEW: 'NEEDS MANUAL REVIEW',
  HUMAN_CONFIRMED_ABSENT: 'CONFIRMED ABSENT (HUMAN)',
  MANUALLY_CORRECTED: 'HUMAN-VERIFIED',
  CONFLICTING: 'CONFLICT',
}

export function StatusBadge({ value, title }: { value: string | null | undefined; title?: string }) {
  if (!value) return <span className="badge NOT_APPLICABLE">–</span>
  const label = STATUS_LABELS[value] ?? value.replace(/_/g, ' ')
  const glyph = STATUS_GLYPH[value]
  return (
    <span className={`badge ${value}`} title={title ?? label} aria-label={`${label} status`}>
      {glyph && <span className="bdot" aria-hidden="true">{glyph}</span>}
      {label}
    </span>
  )
}

/** Compact variant for dense tables. */
export function StatusChip({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="badge NOT_APPLICABLE">–</span>
  const glyph = STATUS_GLYPH[value] ?? '·'
  return (
    <span className={`badge ${value}`} title={STATUS_LABELS[value] ?? value.replace(/_/g, ' ')}>
      <span className="bdot" aria-hidden="true">{glyph}</span>
      {STATUS_LABELS[value] ?? value.replace(/_/g, ' ')}
    </span>
  )
}

export function Confidence({ value, showMark = true }: { value: number; showMark?: boolean }) {
  const pct = Math.round((value || 0) * 100)
  const cls = pct >= 75 ? 'hi' : pct >= 45 ? 'mid' : 'lo'
  const mark = pct >= 75 ? '✓' : pct >= 45 ? '⚠' : '✗'
  return (
    <span className={`conf ${cls}`} title={`Confidence ${pct}% — derived from OCR confidence, cross-pass agreement and pattern validity`}>
      <span className="bar" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span>
      {pct}%{showMark && <span aria-hidden="true">{mark}</span>}
    </span>
  )
}

// ---------- layout & states ----------
export function SectionHead({ no, title, hint }: { no: string; title: string; hint?: string }) {
  return (
    <div className="sec-head">
      <span className="no">{no}</span>
      <h2>{title}</h2>
      {hint && <span className="hint">{hint}</span>}
    </div>
  )
}

export function Skeleton({ lines = 4, block = false }: { lines?: number; block?: boolean }) {
  return (
    <div role="status" aria-label="Loading content">
      {block ? (
        <div className="skel skel-block" />
      ) : (
        Array.from({ length: lines }).map((_, i) => (
          <div key={i} className={`skel skel-line ${i % 3 === 1 ? 'w60' : i % 3 === 2 ? 'w40' : ''}`} />
        ))
      )}
    </div>
  )
}

export function EmptyState({ glyph = '◌', headline, how, action }: { glyph?: string; headline: string; how?: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="glyph" aria-hidden="true">{glyph}</div>
      <div className="headline">{headline}</div>
      {how && <div className="how">{how}</div>}
      {action}
    </div>
  )
}

// ---------- toast (sparing, useful confirmations only) ----------
export function useToast() {
  const [msg, setMsg] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  function show(m: string) {
    setMsg(m)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setMsg(''), 2600)
  }
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  const node = msg ? (
    <div className="toast" role="status" aria-live="polite">{msg}</div>
  ) : null
  return { show, node }
}

// ---------- stat card (label + icon tile + tabular number) ----------
export function StatCard({
  label,
  value,
  icon,
  sub,
  tone,
}: {
  label: string
  value: number | string
  icon: string
  sub?: string
  tone?: 'alert' | 'warn' | 'ok'
}) {
  return (
    <div className={`stat-card ${tone ?? ''}`}>
      <div className="top">
        <span className="lbl">{label}</span>
        <span className="tile"><Icon name={icon} /></span>
      </div>
      <div className="num">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}

// ---------- decision banner ----------
export function DecisionBanner({
  decision,
  why,
  children,
}: {
  decision: string | null
  why?: string
  children?: ReactNode
}) {
  const cls = (decision || 'pending').toLowerCase().replace(/-/g, '_')
  const label =
    decision === 'COMPLIANT'
      ? 'COMPLIANT'
      : decision === 'NON_COMPLIANT'
        ? 'NON-COMPLIANT'
        : decision === 'NEEDS_MANUAL_REVIEW'
          ? 'NEEDS MANUAL REVIEW'
          : 'PENDING DECISION'
  const glyph = STATUS_GLYPH[decision || ''] ?? '?'
  return (
    <div className={`decision-banner ${cls}`} role="region" aria-label="Final decision">
      <div>
        <div className="dlabel">Final decision</div>
        <div className="dvalue">
          <span aria-hidden="true">{glyph} </span>{label}
        </div>
        {why && <div className="dwhy">{why}</div>}
      </div>
      {children && <div className="right flex">{children}</div>}
    </div>
  )
}
