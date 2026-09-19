import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { EmptyState, Skeleton, StatCard, StatusBadge } from '../components/ui'

interface Brief {
  id: number
  inspection_number: string
  product: string
  status: string
  decision: string | null
  category: string
  created_at: string
}

interface EnforcementDash {
  user: {
    portal: string
    role: string
    permissions: string[]
    organization: { id: number; name: string; kind: string } | null
  }
  counts: Record<string, number>
  recent: Brief[]
}

/** ROLE 1 — regulatory / enforcement personnel. Reads only what the server authorizes. */
export default function EnforcementDashboard() {
  const [data, setData] = useState<EnforcementDash | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api
      .get<EnforcementDash>('/enforcement/dashboard')
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <Card><Skeleton lines={6} /></Card>
  if (error) return <div className="alert error">{error}</div>
  if (!data) return null

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="page-title">Enforcement Dashboard</div>
          <div className="page-sub">
            Regulatory oversight: inspections, flagged products, violations and decisions.
            {data.user.organization && <> Operating as <b>{data.user.organization.name}</b>.</>}
          </div>
        </div>
        <div className="actions">
          <Link className="btn secondary" to="/inspections/new"><span>New inspection</span></Link>
        </div>
      </div>

      <div className="grid cols-4" style={{ marginBottom: 18 }}>
        <StatCard label="Inspections" value={data.counts.inspections} icon="clipboard-list" sub="all recorded inspections" />
        <StatCard label="Non-compliant" value={data.counts.non_compliant} icon="alert-triangle" tone="alert" sub="confirmed non-compliance" />
        <StatCard label="Needs review" value={data.counts.needs_review} icon="search-check" tone="warn" sub="awaiting a human decision" />
        <StatCard label="Flagged findings" value={data.counts.flagged_violations} icon="bell" tone="alert" sub={`${data.counts.confirmed_violations} confirmed`} />
      </div>

      <Card title="Recent inspections" right={<Link to="/violations">View flagged products →</Link>}>
        {data.recent.length === 0 ? (
          <EmptyState headline="No inspections yet" how="Start an inspection to populate the regulatory record." />
        ) : (
          <table className="tbl">
            <thead>
              <tr><th>Inspection</th><th>Product</th><th>Category</th><th>Status</th><th>Decision</th><th>Date</th></tr>
            </thead>
            <tbody>
              {data.recent.map((r) => (
                <tr key={r.id}>
                  <td className="mono"><Link to={`/inspections/${r.id}`}>{r.inspection_number}</Link></td>
                  <td>{r.product || '—'}</td>
                  <td>{r.category || '—'}</td>
                  <td>{r.status}</td>
                  <td><StatusBadge value={r.decision} /></td>
                  <td>{r.created_at.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  )
}
