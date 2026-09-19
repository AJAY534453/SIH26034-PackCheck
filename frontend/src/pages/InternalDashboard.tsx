import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { EmptyState, Skeleton, StatCard, StatusBadge, useToast } from '../components/ui'

interface Brief {
  id: number
  inspection_number: string
  product: string
  status: string
  decision: string | null
  category: string
  created_at: string
}

interface InternalDash {
  user: { role: string; permissions: string[]; organization: { id: number; name: string; kind: string } | null }
  counts: Record<string, number>
  recent: Brief[]
}

/** ROLE 3 — internal corporate compliance. Own-organization records and review workflows. */
export default function InternalDashboard() {
  const [data, setData] = useState<InternalDash | null>(null)
  const [reviews, setReviews] = useState<Brief[]>([])
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const { show, node: toast } = useToast()

  function load() {
    api
      .get<InternalDash>('/internal/dashboard')
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
    api
      .get<{ items: Brief[] }>('/internal/reviews')
      .then((d) => setReviews(d.items))
      .catch(() => setReviews([]))
  }

  useEffect(load, [])

  async function addNote(id: number) {
    const text = (notes[id] || '').trim()
    if (!text) return
    setBusy(true)
    setError('')
    try {
      await api.post(`/internal/reviews/${id}/note`, { notes: text })
      setNotes({ ...notes, [id]: '' })
      show('Internal review note recorded')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not record the note')
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <Card><Skeleton lines={6} /></Card>
  if (error && !data) return <div className="alert error">{error}</div>
  if (!data) return null

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="page-title">Compliance Dashboard</div>
          <div className="page-sub">
            Internal review workflows for{' '}
            {data.user.organization ? <b>{data.user.organization.name}</b> : 'your organization'}.
            Organisation-level records only — this portal has no enforcement capability.
          </div>
        </div>
        <div className="actions">
          <Link className="btn secondary" to="/reports">Prepare audit report</Link>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {toast}

      <div className="grid cols-4" style={{ marginBottom: 18 }}>
        <StatCard label="Org records" value={data.counts.organization_records} icon="database" sub="within your organization" />
        <StatCard label="Awaiting review" value={data.counts.awaiting_review} icon="clock" tone="warn" sub="need an internal decision" />
        <StatCard label="Non-compliant" value={data.counts.non_compliant} icon="alert-triangle" tone="alert" sub="confirmed non-compliance" />
        <StatCard label="Confirmed findings" value={data.counts.confirmed_findings} icon="shield-check" sub={`${data.counts.open_findings} still open`} />
      </div>

      <Card title="Records awaiting internal review">
        {reviews.length === 0 ? (
          <EmptyState
            glyph="✓"
            headline="Nothing awaiting review"
            how="Records that reach AWAITING_REVIEW for your organization will appear here with a place to record an internal observation."
          />
        ) : (
          reviews.map((r) => (
            <div key={r.id} style={{ borderBottom: '1px solid var(--border)', padding: '12px 0' }}>
              <div className="flex">
                <b className="mono">{r.inspection_number}</b>
                <span className="right flex" style={{ gap: 8 }}>
                  <StatusBadge value={r.decision} />
                  <Link className="btn sm secondary" to={`/inspections/${r.id}`}>Open</Link>
                </span>
              </div>
              <div className="muted" style={{ margin: '4px 0 8px' }}>
                {r.product || 'Unnamed product'} · {r.category || 'Uncategorised'} · {r.created_at.slice(0, 10)}
              </div>
              <div className="flex" style={{ alignItems: 'flex-end', gap: 8 }}>
                <div className="field" style={{ flex: 1, marginBottom: 0 }}>
                  <label htmlFor={`note-${r.id}`}>Internal review note</label>
                  <input
                    id={`note-${r.id}`}
                    value={notes[r.id] ?? ''}
                    onChange={(e) => setNotes({ ...notes, [r.id]: e.target.value })}
                    placeholder="e.g. Label reprint scheduled; evidence attached to audit pack."
                  />
                </div>
                <button className="btn sm" onClick={() => addNote(r.id)} disabled={busy || !(notes[r.id] || '').trim()}>
                  Record note
                </button>
              </div>
            </div>
          ))
        )}
      </Card>
    </>
  )
}
