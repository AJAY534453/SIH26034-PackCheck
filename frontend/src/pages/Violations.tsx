import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { EmptyState, SectionHead, Skeleton, StatusBadge } from '../components/ui'
import type { ViolationRow } from '../types'

export default function Violations() {
  const [items, setItems] = useState<ViolationRow[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState('')
  const [severity, setSeverity] = useState('')

  useEffect(() => {
    api
      .get<{ items: ViolationRow[] }>('/violations')
      .then((d) => setItems(d.items))
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(
    () =>
      items.filter(
        (v) =>
          (!status || v.status === status) &&
          (!severity || v.severity === severity),
      ),
    [items, status, severity],
  )

  const byTitle = useMemo(() => {
    const m = new Map<string, number>()
    for (const v of filtered) m.set(v.title, (m.get(v.title) || 0) + 1)
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
  }, [filtered])

  return (
    <>
      <div className="page-title">Violations</div>
      <div className="page-sub">
        Potential violations only — derived from rule evaluations that failed with sufficient evidence.
        Missing OCR evidence alone never creates a violation.
      </div>
      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <div className="flex">
          <label className="muted" htmlFor="v-status">Status</label>
          <select id="v-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="OPEN">Open</option>
            <option value="CONFIRMED">Confirmed</option>
            <option value="NEEDS_REVIEW">Needs review</option>
            <option value="DISMISSED">Dismissed</option>
          </select>
          <label className="muted" htmlFor="v-severity">Severity</label>
          <select id="v-severity" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">All severities</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>
          <span className="right muted">{filtered.length} of {items.length}</span>
        </div>
      </div>

      {loading ? (
        <Card><Skeleton lines={6} /></Card>
      ) : (
        <>
          <SectionHead no="1" title="Violation records" />
          <Card>
            <table className="tbl">
              <thead>
                <tr><th>Rule</th><th>Title</th><th>Product</th><th>Inspection</th><th>Severity</th><th>Status</th><th>Date</th></tr>
              </thead>
              <tbody>
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={7}>
                      <EmptyState
                        glyph="✓"
                        headline={items.length === 0 ? 'No potential violations recorded' : 'No violations match the current filters'}
                        how={
                          items.length === 0
                            ? 'Violations appear only when rule evaluations fail with sufficient evidence.'
                            : 'Adjust or clear the status/severity filters.'
                        }
                      />
                    </td>
                  </tr>
                )}
                {filtered.map((v) => (
                  <tr key={v.id}>
                    <td>{v.rule_number}</td>
                    <td>{v.title}</td>
                    <td>{v.product}</td>
                    <td><Link to={`/inspections/${v.inspection_id}`}>{v.inspection_number}</Link></td>
                    <td><StatusBadge value={v.severity} /></td>
                    <td><StatusBadge value={v.status} /></td>
                    <td>{v.created_at.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          {byTitle.length > 0 && (
            <>
              <SectionHead no="2" title="Most frequent types" hint="within the current filter" />
              <Card>
                {byTitle.map(([title, count]) => (
                  <div key={title} className="flex" style={{ marginBottom: 8 }}>
                    <div style={{ flex: 1, fontSize: 13 }}>{title}</div>
                    <b>{count}</b>
                  </div>
                ))}
              </Card>
            </>
          )}
        </>
      )}
    </>
  )
}
