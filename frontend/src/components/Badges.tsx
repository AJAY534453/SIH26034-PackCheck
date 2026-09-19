import type { ReactNode } from 'react'

const DECISION_LABELS: Record<string, string> = {
  COMPLIANT: 'COMPLIANT',
  NON_COMPLIANT: 'NON-COMPLIANT',
  NEEDS_MANUAL_REVIEW: 'NEEDS MANUAL REVIEW',
}

export function Badge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="badge NOT_APPLICABLE">—</span>
  return <span className={`badge ${value}`}>{DECISION_LABELS[value] ?? value.replace(/_/g, ' ')}</span>
}

export function Conf({ value }: { value: number }) {
  const pct = Math.round((value || 0) * 100)
  const cls = pct >= 75 ? 'hi' : pct >= 45 ? 'mid' : 'lo'
  const mark = pct >= 75 ? '✓' : pct >= 45 ? '⚠' : '✗'
  return (
    <span className={`conf ${cls}`}>
      <span className="bar"><i style={{ width: `${pct}%` }} /></span>
      {pct}% {mark}
    </span>
  )
}

export function Card({ title, children, right }: { title?: string; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="card">
      {title && (
        <h3>
          {title}
          {right && <span className="right">{right}</span>}
        </h3>
      )}
      {children}
    </div>
  )
}
