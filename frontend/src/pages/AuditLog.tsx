import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { EmptyState, SectionHead, Skeleton } from '../components/ui'
import type { AuditLogRow } from '../types'

const PAGE_SIZE = 100

/** Append-only audit trail. Read-only by design: the backend never exposes an edit path. */
export default function AuditLog() {
  const [items, setItems] = useState<AuditLogRow[]>([])
  const [actions, setActions] = useState<string[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [action, setAction] = useState('')
  const [actor, setActor] = useState('')
  const [inspection, setInspection] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams()
    qs.set('limit', String(PAGE_SIZE))
    qs.set('offset', String(offset))
    if (action) qs.set('action', action)
    if (actor.trim()) qs.set('actor', actor.trim())
    if (inspection.trim()) qs.set('inspection', inspection.trim())
    api
      .get<{ items: AuditLogRow[]; total: number; actions: string[] }>(`/audit?${qs.toString()}`)
      .then((d) => {
        setItems(d.items)
        setTotal(d.total)
        setActions(d.actions)
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }, [action, actor, inspection, offset])

  useEffect(() => { load() }, [load])

  // Changing a filter restarts paging from the newest record.
  function changeFilter(fn: () => void) {
    setOffset(0)
    fn()
  }

  const lastPage = total === 0 ? 0 : Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)
  const page = Math.floor(offset / PAGE_SIZE)

  return (
    <>
      <div className="page-title">Audit Log</div>
      <div className="page-sub">
        Append-only record of every mutating action — logins, uploads, processing, field reviews,
        violation decisions and report generation. Entries can never be edited or deleted.
      </div>
      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <div className="flex">
          <label className="muted" htmlFor="a-action">Action</label>
          <select
            id="a-action"
            value={action}
            onChange={(e) => changeFilter(() => setAction(e.target.value))}
          >
            <option value="">All actions</option>
            {actions.map((a) => (
              <option key={a} value={a}>{a.replace(/_/g, ' ')}</option>
            ))}
          </select>
          <label className="muted" htmlFor="a-actor">Actor</label>
          <input
            id="a-actor"
            value={actor}
            onChange={(e) => changeFilter(() => setActor(e.target.value))}
            placeholder="username"
          />
          <label className="muted" htmlFor="a-insp">Inspection</label>
          <input
            id="a-insp"
            value={inspection}
            onChange={(e) => changeFilter(() => setInspection(e.target.value))}
            placeholder="INS-2026-000017"
          />
          <span className="right muted">{total} entr{total === 1 ? 'y' : 'ies'}</span>
        </div>
      </div>

      {loading ? (
        <Card><Skeleton lines={8} /></Card>
      ) : (
        <>
          <SectionHead
            no="1"
            title="Entries"
            hint="newest first"
          />
          <Card>
            <table className="tbl audit-tbl">
              <thead>
                <tr>
                  <th>Time</th><th>Actor</th><th>Action</th><th>Inspection</th><th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && (
                  <tr>
                    <td colSpan={5}>
                      <EmptyState
                        glyph="≡"
                        headline="No audit entries match the current filters"
                        how="Every mutating action is recorded here automatically as operators use the system."
                      />
                    </td>
                  </tr>
                )}
                {items.map((r) => (
                  <tr key={r.id}>
                    <td>{r.created_at.slice(0, 16).replace('T', ' ')}</td>
                    <td>{r.actor || '—'}</td>
                    <td>{r.action.replace(/_/g, ' ')}</td>
                    <td>
                      {r.inspection_id ? (
                        <Link to={`/inspections?search=${encodeURIComponent(r.inspection_id)}`}>
                          {r.inspection_id}
                        </Link>
                      ) : '—'}
                    </td>
                    <td className="muted">
                      {(r.before || r.after) && (
                        <div>
                          {r.before && <span>{r.before.slice(0, 60)}</span>}
                          {r.before && r.after && <span> → </span>}
                          {r.after && <span>{r.after.slice(0, 60)}</span>}
                        </div>
                      )}
                      {r.reason && <div>{r.reason.slice(0, 90)}</div>}
                      {!r.before && !r.after && !r.reason && '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {total > PAGE_SIZE && (
              <div className="flex mt">
                <button
                  className="btn sm secondary"
                  disabled={page === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  ← Newer
                </button>
                <span className="muted">Page {page + 1} of {lastPage + 1}</span>
                <button
                  className="btn sm secondary"
                  disabled={page >= lastPage}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Older →
                </button>
              </div>
            )}
          </Card>
        </>
      )}
    </>
  )
}
