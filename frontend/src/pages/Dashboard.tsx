import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, getRole } from '../services/api'
import type { DashboardStats } from '../types'
import { Card } from '../components/Badges'
import { Icon } from '../components/Icon'
import { EmptyState, SectionHead, Skeleton, StatusBadge } from '../components/ui'

/** Stat card with an icon tile — the design system's summary-card pattern. */
function StatCard({
  label,
  value,
  sub,
  icon,
  tone = '',
  to,
}: {
  label: string
  value: string | number
  sub?: string
  icon: string
  tone?: '' | 'ok' | 'warn' | 'alert'
  to?: string
}) {
  const body = (
    <div className={`stat-card ${tone}`}>
      <div className="top">
        <span className="lbl">{label}</span>
        <span className="tile">
          <Icon name={icon} />
        </span>
      </div>
      <div className="num">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
  return to ? (
    <Link to={to} style={{ textDecoration: 'none', color: 'inherit' }}>
      {body}
    </Link>
  ) : (
    body
  )
}

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [error, setError] = useState('')
  const role = getRole() || 'VIEWER'

  useEffect(() => {
    api
      .get<DashboardStats>('/dashboard/stats')
      .then(setStats)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load dashboard'))
  }, [])

  if (error) return <div className="alert error">{error}</div>
  if (!stats)
    return (
      <>
        <div className="page-title">Dashboard</div>
        <div className="page-sub">What needs attention right now.</div>
        <Card>
          <Skeleton lines={5} />
        </Card>
      </>
    )

  const total = stats.compliant + stats.non_compliant + stats.needs_review
  const dist = [
    { label: 'Compliant', n: stats.compliant, cls: 'green' },
    { label: 'Non-compliant', n: stats.non_compliant, cls: 'red' },
    { label: 'Needs review', n: stats.needs_review, cls: 'amber' },
  ]

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="page-title">Dashboard</div>
          <div className="page-sub">
            What needs attention right now. Every figure is a live database count — nothing here is
            decorative.
          </div>
        </div>
        <div className="actions">
          {stats.ai && (
            <span className={`mode-pill ${stats.ai.enabled ? 'vision' : 'offline'}`} title={stats.ai.reason}>
              <Icon name={stats.ai.enabled ? 'sparkles' : 'cpu'} size={13} />
              {stats.ai.enabled ? `Vision ${stats.ai.model}` : 'On-device OCR'}
            </span>
          )}
          {role !== 'VIEWER' && (
            <Link className="btn" to="/inspections/new">
              <Icon name="scan-line" /> New Inspection
            </Link>
          )}
        </div>
      </div>

      {/* The product's actual method, each stage carrying a live count from the database. */}
      <div className="chain" aria-label="Inspection pipeline">
        <Link className="chain-step" to="/inspections/new">
          <span className="chain-n">SCAN</span>
          <span className="chain-v">{stats.pipeline.images_scanned}</span>
          <span className="chain-d">package images captured</span>
        </Link>
        <Link className="chain-step" to="/inspections">
          <span className="chain-n">EXTRACT</span>
          <span className="chain-v">{stats.pipeline.fields_detected}</span>
          <span className="chain-d">declarations detected</span>
        </Link>
        <Link className="chain-step" to={`/inspections/${stats.review_queue[0]?.id ?? ''}`}>
          <span className="chain-n">VERIFY</span>
          <span className="chain-v">{stats.pipeline.awaiting_review}</span>
          <span className="chain-d">
            awaiting review · {stats.pipeline.fields_uncertain} uncertain · {stats.conflicts} conflicting
          </span>
        </Link>
        <Link className="chain-step" to="/violations">
          <span className="chain-n">ALERT</span>
          <span className="chain-v">{stats.open_violations}</span>
          <span className="chain-d">findings unconfirmed</span>
        </Link>
        <Link className="chain-step" to="/reports">
          <span className="chain-n">REPORT</span>
          <span className="chain-v">{stats.reports}</span>
          <span className="chain-d">reports generated</span>
        </Link>
      </div>

      {/* 1. Pending review — the operator's primary work queue */}
      <SectionHead no="1" title="Pending review" hint="inspections awaiting a human decision" />
      <div className="grid cols-3" style={{ marginBottom: 6 }}>
        <StatCard label="Needs Manual Review" value={stats.needs_review} sub="awaiting an inspector" icon="search-check" tone="warn" to="/inspections" />
        <StatCard label="Non-Compliant" value={stats.non_compliant} sub="confirmed findings" icon="x-circle" tone="alert" to="/violations" />
        <StatCard label="Compliant" value={stats.compliant} sub="all automated checks passed" icon="badge-check" tone="ok" to="/inspections" />
        <StatCard label="Total Inspections" value={stats.total_inspections} sub={`${stats.products} products tracked`} icon="clipboard-list" to="/inspections" />
        <StatCard label="Conflicting Declarations" value={stats.conflicts} sub="fields whose sources disagree" icon="alert-triangle" tone={stats.conflicts > 0 ? 'warn' : ''} to="/inspections" />
        <StatCard label="Open Findings" value={stats.open_violations} sub="raised, not yet confirmed" icon="shield-check" tone={stats.open_violations > 0 ? 'alert' : ''} to="/violations" />
        <StatCard label="Bills Scanned" value={stats.bills} sub={`${stats.potential_price_differences.length} with a price difference`} icon="receipt" tone={stats.potential_price_differences.length > 0 ? 'warn' : ''} to="/bills" />
        <StatCard label="Grocery Alerts" value={stats.grocery_alert_count} sub={`${stats.grocery_items} tracked items`} icon="bell" tone={stats.grocery_alert_count > 0 ? 'warn' : ''} to="/grocery" />
        <StatCard label="Open Complaints" value={stats.open_complaints} sub={`${stats.complaints} filed in total`} icon="message-square-warning" to="/complaints" />
      </div>

      {/* 1b. Automated compliance review — the repository's score and finalization state */}
      <SectionHead
        no="1b"
        title="Automated compliance review"
        hint={`compliance threshold ${stats.compliance_threshold}%, evidence-coverage floor ${stats.coverage_floor}%, official finalization`}
      />
      <div className="grid cols-3" style={{ marginBottom: 6 }}>
        <StatCard
          label="Scans in repository"
          value={stats.product_scans}
          sub={`${stats.products} distinct products`}
          icon="layers"
          to="/repository"
        />
        <StatCard
          label="Pending Official Finalization"
          value={stats.pending_finalization}
          sub="awaiting an authorized official"
          icon="clock"
          tone={stats.pending_finalization > 0 ? 'warn' : 'ok'}
          to="/repository"
        />
        <StatCard
          label="Below Threshold"
          value={stats.scans_below_threshold}
          sub={`under ${stats.compliance_threshold}% compliance — flagged for review`}
          icon="trending-up"
          tone={stats.scans_below_threshold > 0 ? 'warn' : 'ok'}
          to="/repository"
        />
        <StatCard
          label="Thin Evidence Coverage"
          value={stats.scans_below_coverage_floor}
          sub={`under ${stats.coverage_floor}% of the rules verified from the images`}
          icon="search"
          tone={stats.scans_below_coverage_floor > 0 ? 'warn' : 'ok'}
          to="/repository"
        />
        <StatCard
          label="Finalized Scans"
          value={stats.finalized_scans}
          sub="official decision recorded"
          icon="badge-check"
          tone="ok"
          to="/repository"
        />
        <StatCard
          label="Average Compliance Score"
          value={stats.average_compliance_score === null ? '—' : `${stats.average_compliance_score}%`}
          sub={`compliance across scans in your scope · coverage ${stats.average_coverage_score === null ? '—' : `${stats.average_coverage_score}%`}`}
          icon="database"
          to="/repository"
        />
        <StatCard
          label="AI Preliminary Pass"
          value={stats.scan_facets?.ai_verdict?.COMPLIANT ?? 0}
          sub="preliminary — not final decisions"
          icon="check-circle"
          to="/repository"
        />
      </div>

      {stats.pending_finalization_queue.length > 0 && (
        <Card title="Compliance results awaiting official finalization">
          {stats.pending_finalization_queue.map((r) => (
            <div key={r.id} className="flex" style={{ marginBottom: 8, gap: 10, alignItems: 'center' }}>
              <Link to={`/repository/scans/${r.id}`}>{r.inspection_number}</Link>
              <span style={{ flex: 1, minWidth: 0 }}>{r.product}</span>
              <b>{r.score}%</b>
              <span className="muted" style={{ fontSize: 12 }}>{r.status_text}</span>
            </div>
          ))}
          <Link className="btn sm secondary" to="/repository">Open the repository</Link>
        </Card>
      )}

      <div className="row2">
        <Card title="Review queue">
          {stats.review_queue.length === 0 ? (
            <EmptyState glyph="✓" headline="Review queue is clear" how="No inspections are awaiting manual review." />
          ) : (
            stats.review_queue.map((r) => (
              <div key={r.id} className="flex" style={{ marginBottom: 8 }}>
                <Link to={`/inspections/${r.id}`}>{r.inspection_number}</Link>
                <span className="right"><StatusBadge value={r.decision} /></span>
              </div>
            ))
          )}
        </Card>

        <Card title="Conflicts requiring resolution">
          {stats.conflict_queue.length === 0 ? (
            <EmptyState glyph="✓" headline="No conflicting evidence" how="Fields whose sources disagree will be listed here for human resolution." />
          ) : (
            stats.conflict_queue.map((r) => (
              <div key={r.id} className="flex" style={{ marginBottom: 8 }}>
                <div style={{ flex: 1 }}>
                  <Link to={`/inspections/${r.id}`}>{r.inspection_number}</Link>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {r.fields.map((f) => f.replace(/_/g, ' ')).join(', ')}
                  </div>
                </div>
                <StatusBadge value="CONFLICTING" />
              </div>
            ))
          )}
        </Card>

        <Card title="Common violation types">
          {stats.common_violations.length === 0 ? (
            <EmptyState glyph="✓" headline="No violations recorded" how="Failed rule evaluations with sufficient evidence will appear here." />
          ) : (
            stats.common_violations.map((v) => (
              <div key={v.type} className="flex" style={{ marginBottom: 8 }}>
                <div style={{ flex: 1, fontSize: 13 }}>{v.type}</div>
                <b>{v.count}</b>
              </div>
            ))
          )}
        </Card>
      </div>

      {/* 2. Recent inspections */}
      <SectionHead no="2" title="Recent inspections" />
      <Card>
        {stats.recent_inspections.length === 0 ? (
          <EmptyState
            headline="No inspections yet"
            how={
              role !== 'VIEWER'
                ? 'Start your first inspection by uploading package images.'
                : 'An inspector must create the first inspection.'
            }
            action={
              role !== 'VIEWER' ? (
                <Link className="btn sm" to="/inspections/new">New Inspection</Link>
              ) : undefined
            }
          />
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Inspection ID</th><th>Product</th><th>Date</th><th>Inspector</th><th>Status</th><th></th>
              </tr>
            </thead>
            <tbody>
              {stats.recent_inspections.map((r) => (
                <tr key={r.id}>
                  <td><Link to={`/inspections/${r.id}`}>{r.inspection_number}</Link></td>
                  <td>{r.product}</td>
                  <td>{r.date}</td>
                  <td>{r.inspector}</td>
                  <td><StatusBadge value={r.decision ?? r.status} /></td>
                  <td><Link to={`/inspections/${r.id}`}>Open</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* 3. Compliance overview — compact, real data only */}
      <SectionHead no="3" title="Compliance overview" />
      <div className="row2">
        <Card title="Status distribution">
          {total === 0 ? (
            <EmptyState headline="Nothing to summarize yet" how="Run your first inspection to build the compliance overview." />
          ) : (
            dist.map((d) => (
              <div key={d.label} className="flex" style={{ marginBottom: 10 }}>
                <div style={{ width: 130, fontSize: 13 }}>{d.label}</div>
                <div className="conf" style={{ flex: 1 }}>
                  <span className="bar" style={{ width: '100%' }}>
                    <i style={{ width: `${(d.n / total) * 100}%` }} />
                  </span>
                </div>
                <b style={{ fontSize: 13, minWidth: 24, textAlign: 'right' }}>{d.n}</b>
              </div>
            ))
          )}
        </Card>

        <Card title="Repository">
          <div className="kv">
            <dt>Products tracked</dt><dd>{stats.products}</dd>
            <dt>Violations recorded</dt><dd>{stats.violations}</dd>
            <dt>Reports generated</dt><dd>{stats.reports}</dd>
            <dt>Bills scanned</dt><dd>{stats.bills_total}</dd>
            <dt>Grocery items</dt><dd>{stats.grocery_items}</dd>
            <dt>Complaints</dt><dd>{stats.complaints}</dd>
          </div>
          <div className="flex" style={{ marginTop: 14 }}>
            <Link className="btn sm secondary" to="/bills"><Icon name="receipt" /> Scan a bill</Link>
            <Link className="btn sm secondary" to="/grocery"><Icon name="shopping-cart" /> Add a grocery item</Link>
            <Link className="btn sm secondary" to="/complaints"><Icon name="message-square-warning" /> Create a complaint</Link>
          </div>
        </Card>
      </div>

      {/* 4. Consumer tools — real rows from the bills / grocery / complaint tables */}
      <SectionHead no="4" title="Alerts &amp; consumer tools" hint="price differences, expiry alerts and open complaints" />
      <div className="row2">
        <Card title="Potential price differences">
          {stats.potential_price_differences.length === 0 ? (
            <EmptyState
              glyph="✓"
              headline="No price differences flagged"
              how="Scan a bill to compare the charged price with the product's printed MRP."
              action={<Link className="btn sm secondary" to="/bills">Open Bill Scanner</Link>}
            />
          ) : (
            stats.potential_price_differences.map((b) => (
              <div key={b.id} className="flex" style={{ marginBottom: 10 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b style={{ fontSize: 13 }}>{b.product}</b>
                  {b.source === 'demo' && <span className="sample-flag" style={{ marginLeft: 6 }}>SAMPLE</span>}
                  <div className="muted">
                    {b.store} · billed ₹{b.billed} vs MRP ₹{b.mrp}
                  </div>
                </div>
                <span className="badge UNCERTAIN"><span className="bdot" aria-hidden="true">⚠</span>verify</span>
              </div>
            ))
          )}
        </Card>

        <Card title="Grocery expiry alerts">
          {stats.grocery_alerts.length === 0 ? (
            <EmptyState
              glyph="◌"
              headline="No expiry alerts"
              how="Alerts apply only to products you explicitly add to Grocery."
              action={<Link className="btn sm secondary" to="/grocery">Open Grocery</Link>}
            />
          ) : (
            stats.grocery_alerts.map((g) => (
              <div key={g.id} className="flex" style={{ marginBottom: 10 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b style={{ fontSize: 13 }}>{g.product_name}</b>
                  <div className="muted">{g.detail}</div>
                </div>
                <span className={`badge ${g.alert === 'EXPIRED' ? 'NON_COMPLIANT' : 'UNCERTAIN'}`}>
                  <span className="bdot" aria-hidden="true">{g.alert === 'EXPIRED' ? '✕' : '⚠'}</span>
                  {g.alert === 'EXPIRED' ? 'expired' : 'expiring'}
                </span>
              </div>
            ))
          )}
        </Card>

        <Card title="Recent complaints">
          {stats.recent_complaints.length === 0 ? (
            <EmptyState
              glyph="◌"
              headline="No complaints filed"
              how="Create a complaint from a bill, a grocery item or an inspection."
              action={<Link className="btn sm secondary" to="/complaints">Open Complaint Center</Link>}
            />
          ) : (
            stats.recent_complaints.map((c) => (
              <div key={c.id} className="flex" style={{ marginBottom: 10 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b className="mono" style={{ fontSize: 12.5 }}>{c.complaint_number}</b>
                  <div className="muted">{c.product_name || '—'} · {c.category.replace(/_/g, ' ')}</div>
                </div>
                <span className="badge NEEDS_REVIEW">
                  <span className="bdot" aria-hidden="true">●</span>
                  {c.status.replace(/_/g, ' ')}
                </span>
              </div>
            ))
          )}
        </Card>
      </div>
    </>
  )
}
