import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { EmptyState, Skeleton, StatusBadge } from '../components/ui'
import type { InspectionSummary } from '../types'

interface Row extends InspectionSummary {
  product?: string
}

export default function Inspections() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [decision, setDecision] = useState('')
  // Deep-linkable: /inspections?search=INS-2026-000017 (e.g. from the audit log)
  const [urlParams] = useSearchParams()
  const [search, setSearch] = useState(urlParams.get('search') || '')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)

  const load = useCallback(() => {
    const params = new URLSearchParams({ page: String(page), page_size: '20' })
    if (decision) params.set('decision', decision)
    if (search) params.set('search', search)
    setLoading(true)
    api
      .get<{ items: Row[]; total: number }>(`/inspections?${params}`)
      .then((d) => { setRows(d.items); setTotal(d.total) })
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }, [decision, search, page])

  useEffect(() => { load() }, [load])

  return (
    <>
      <div className="page-title">Inspections</div>
      <div className="page-sub">Search, filter and open complete inspection records.</div>

      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <div className="flex">
          <div className="field" style={{ marginBottom: 0, flex: 1 }}>
            <label htmlFor="insp-search" className="muted">Search</label>
            <input
              id="insp-search"
              placeholder="Inspection number or product…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor="insp-decision" className="muted">Decision</label>
            <select id="insp-decision" value={decision} onChange={(e) => { setDecision(e.target.value); setPage(1) }}>
              <option value="">All decisions</option>
              <option value="COMPLIANT">Compliant</option>
              <option value="NON_COMPLIANT">Non-compliant</option>
              <option value="NEEDS_MANUAL_REVIEW">Needs manual review</option>
            </select>
          </div>
        </div>
      </div>

      {loading ? (
        <Card><Skeleton lines={6} /></Card>
      ) : (
      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>Inspection ID</th><th>Decision</th><th>Category</th><th>Quality</th><th>Inspector</th><th>Date</th><th></th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={7}>
                <EmptyState
                  headline={total === 0 ? 'No inspections yet' : 'No inspections match'}
                  how={total === 0 ? 'Create one from “New Inspection” by uploading package images.' : 'Try a different search term or decision filter.'}
                />
              </td></tr>
            )}
            {rows.map((r) => (
              <tr key={r.id}>
                <td><Link to={`/inspections/${r.id}`}>{r.inspection_number}</Link></td>                  <td><StatusBadge value={r.final_decision ?? r.status} /></td>
                <td>{r.category || '—'}</td>
                <td>{r.overall_quality || '—'}</td>
                <td>{r.inspector}</td>
                <td>{r.created_at.slice(0, 19).replace('T', ' ')}</td>
                <td><Link to={`/inspections/${r.id}`}>Open</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex mt">
          <button className="btn sm secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>← Prev</button>
          <span className="muted">{total} total</span>
          <button className="btn sm secondary right" disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>Next →</button>
        </div>
      </div>
      )}
    </>
  )
}
