import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { EmptyState, Skeleton, StatCard, StatusChip } from '../components/ui'
import type { ProductHistory as ProductHistoryData } from '../types'

/** Minimal inline score trend — no chart dependency, and the numbers are always printed too. */
function Trend({ points }: { points: { scanned_at: string; score: number }[] }) {
  if (points.length < 2) {
    return <div className="muted" style={{ fontSize: 12.5 }}>At least two scans are needed to show a trend.</div>
  }
  const w = 320
  const h = 64
  const step = w / (points.length - 1)
  const path = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${(i * step).toFixed(1)} ${(h - (Math.max(0, Math.min(100, p.score)) / 100) * h).toFixed(1)}`)
    .join(' ')
  return (
    <div>
      <svg width={w} height={h} role="img" aria-label={`Compliance score trend across ${points.length} scans`} style={{ maxWidth: '100%' }}>
        <line x1="0" y1={h - 0.85 * h} x2={w} y2={h - 0.85 * h} stroke="#e4e4e1" strokeDasharray="4 4" />
        <path d={path} fill="none" stroke="#2563EB" strokeWidth="2" />
        {points.map((p, i) => (
          <circle key={i} cx={i * step} cy={h - (Math.max(0, Math.min(100, p.score)) / 100) * h} r="3" fill="#2563EB" />
        ))}
      </svg>
      <div className="muted" style={{ fontSize: 11.5 }}>
        Dashed line = the 85% finalization threshold. Scores: {points.map((p) => `${p.score}%`).join(' → ')}
      </div>
    </div>
  )
}

export default function ProductHistory() {
  const { id } = useParams()
  const productId = Number(id)
  const [data, setData] = useState<ProductHistoryData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!productId) return
    setLoading(true)
    api
      .repositoryProduct(productId)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load this product'))
      .finally(() => setLoading(false))
  }, [productId])

  if (loading) return <Card><Skeleton lines={6} /></Card>
  if (error) return <div className="alert error">{error}</div>
  if (!data) {
    return (
      <Card>
        <EmptyState
          headline="Product not found"
          how="The product may belong to another organization, or it has no scans in your data scope."
          action={<Link className="btn sm secondary" to="/repository">Back to the repository</Link>}
        />
      </Card>
    )
  }

  const { product, scans, trend } = data

  return (
    <>
      <div className="page-title">{product.name || '(product name not read)'}</div>
      <div className="page-sub">
        {product.brand ? `${product.brand} · ` : ''}{product.category}
        {product.manufacturer ? ` · ${product.manufacturer}` : ''}
      </div>

      <div className="stat-grid">
        <StatCard label="Scans of this product" value={product.scan_count} icon="layers" />
        <StatCard
          label="Latest score"
          value={product.latest_score === null ? '—' : `${product.latest_score}%`}
          icon="trending-up"
          sub={product.latest_ai_verdict ? `AI verdict: ${product.latest_ai_verdict}` : ''}
        />
        <StatCard
          label="Awaiting finalization"
          value={product.pending_finalization_count}
          icon="clock"
          tone={product.pending_finalization_count > 0 ? 'warn' : 'ok'}
        />
        <StatCard
          label="Latest official decision"
          value={product.latest_official_decision || 'Pending'}
          icon="badge-check"
          sub={product.latest_official_decision ? '' : 'Pending finalization by the higher officials'}
        />
      </div>

      <Card title="Declared values seen across scans">
        <dl className="kv">
          <dt>Known net quantities</dt><dd>{product.known_net_quantities || '—'}</dd>
          <dt>MRP values seen</dt><dd>{product.mrp_values || '—'}</dd>
          <dt>First seen</dt><dd>{product.first_seen.slice(0, 19).replace('T', ' ')}</dd>
          <dt>Last seen</dt><dd>{product.last_seen.slice(0, 19).replace('T', ' ')}</dd>
        </dl>
      </Card>

      <Card title="Compliance score trend">
        <Trend points={trend} />
      </Card>

      <Card title={`Compliance history (${scans.length} scan${scans.length === 1 ? '' : 's'})`}>
        {scans.length === 0 ? (
          <EmptyState headline="No scans recorded" how="This product has no stored scans in your data scope." />
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="measure-table">
              <thead>
                <tr>
                  <th>Scanned</th><th>Reference</th><th>Score</th><th>AI verdict</th>
                  <th>Status</th><th>Official decision</th><th>Checks</th><th></th>
                </tr>
              </thead>
              <tbody>
                {scans.map((s) => (
                  <tr key={s.id}>
                    <td>{s.scanned_at.slice(0, 19).replace('T', ' ')}</td>
                    <td><Link to={`/repository/scans/${s.id}`}>{s.inspection_number}</Link></td>
                    <td className="num">{s.compliance_score}%</td>
                    <td>{s.ai_verdict}</td>
                    <td><StatusChip value={s.review_status} /></td>
                    <td>{s.official_decision || '—'}</td>
                    <td className="num">{s.checks_passed} passed · {s.checks_failed} failed</td>
                    <td><Link to={`/inspections/${s.inspection_id}`}>Inspection</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
          Every entry keeps its own score, verdict and decision — a later scan never overwrites an earlier one.
        </p>
      </Card>
    </>
  )
}
