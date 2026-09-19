import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
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

interface EntityDash {
  user: { role: string; permissions: string[]; organization: { id: number; name: string; kind: string } | null }
  counts: Record<string, number>
  recent: Brief[]
}

/** ROLE 2 — a business subject to regulation. Sees ONLY its own organization's records. */
export default function EntityDashboard() {
  const navigate = useNavigate()
  const [data, setData] = useState<EntityDash | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const { show, node: toast } = useToast()

  function load() {
    api
      .get<EntityDash>('/entity/dashboard')
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  async function submit() {
    setBusy(true)
    setError('')
    try {
      const res = await api.post<{ submission: Brief; message: string }>('/entity/submissions', { notes })
      show(res.message)
      setNotes('')
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not file the submission')
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
          <div className="page-title">Entity Dashboard</div>
          <div className="page-sub">
            {data.user.organization
              ? <>Compliance status for <b>{data.user.organization.name}</b>. You see only your own organization's records.</>
              : 'Your account is not linked to an organization — contact an administrator.'}
          </div>
        </div>
        <div className="actions">
          <Link className="btn secondary" to="/analysis">Scan &amp; analyse an image</Link>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {toast}

      <div className="grid cols-4" style={{ marginBottom: 18 }}>
        <StatCard label="My records" value={data.counts.records} icon="package" sub="submissions & inspections" />
        <StatCard label="Compliant" value={data.counts.compliant} icon="check-circle" tone="ok" sub="passed the deterministic rules" />
        <StatCard label="Needs attention" value={data.counts.non_compliant} icon="alert-triangle" tone="alert" sub="confirmed non-compliance" />
        <StatCard label="Open findings" value={data.counts.open_findings} icon="bell" tone="warn" sub="awaiting resolution" />
      </div>

      <Card title="File a compliance submission">
        <div className="field">
          <label htmlFor="entity-notes">What are you submitting?</label>
          <textarea
            id="entity-notes"
            rows={3}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. Monthly packaged-goods declaration for the Ahmedabad unit — all labels updated."
          />
        </div>
        <button className="btn" onClick={submit} disabled={busy}>
          {busy ? 'Filing…' : 'Create submission'}
        </button>
        <p className="muted" style={{ marginTop: 10, lineHeight: 1.5 }}>
          A submission creates a record owned by your organization for review. It is not a
          self-declaration of compliance — the rules are evaluated by the platform, not by you.
        </p>
      </Card>

      <Card title="My records">
        {data.recent.length === 0 ? (
          <EmptyState
            glyph="◌"
            headline="No records yet"
            how="File a submission above, or open the Image Analysis tool to scan a package."
          />
        ) : (
          <table className="tbl">
            <thead><tr><th>Record</th><th>Product</th><th>Status</th><th>Decision</th><th>Date</th><th></th></tr></thead>
            <tbody>
              {data.recent.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.inspection_number}</td>
                  <td>{r.product || '—'}</td>
                  <td>{r.status}</td>
                  <td><StatusBadge value={r.decision} /></td>
                  <td>{r.created_at.slice(0, 10)}</td>
                  <td>
                    <button className="btn sm secondary" onClick={() => navigate(`/inspections/${r.id}`)}>Open</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  )
}
