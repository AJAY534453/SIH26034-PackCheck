import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, hasPermission } from '../services/api'
import { Icon } from '../components/Icon'
import { Card } from '../components/Badges'
import { EmptyState, Skeleton, useToast } from '../components/ui'
import type { BillOut, ComplaintListResponse, ComplaintOut, InspectionSummary } from '../types'

const STAGE_LABEL: Record<string, string> = {
  SUBMITTED: 'Submitted',
  UNDER_REVIEW: 'Under review',
  EVIDENCE_VERIFIED: 'Evidence verified',
  RESOLVED: 'Resolved',
}

const STATUS_BADGE: Record<string, string> = {
  SUBMITTED: 'OPEN',
  UNDER_REVIEW: 'NEEDS_REVIEW',
  EVIDENCE_VERIFIED: 'PASS',
  RESOLVED: 'COMPLIANT',
}

/** Category/severity are the complainant's own framing — they do not imply a legal finding. */
const CATEGORIES = ['PRICE', 'EXPIRY', 'DECLARATION', 'MRP', 'NET_QUANTITY', 'PACKAGING', 'OTHER']

export default function ComplaintCenter() {
  // Advancing a complaint is a managed action — decided by the shared permission, server-checked.
  const canAdvance = hasPermission('complaints.manage')
  const [data, setData] = useState<ComplaintListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState<ComplaintOut | null>(null)
  const [inspections, setInspections] = useState<InspectionSummary[]>([])
  const [bills, setBills] = useState<BillOut[]>([])
  const [form, setForm] = useState({
    product_name: '',
    store_name: '',
    category: 'PRICE',
    severity: 'MEDIUM',
    issue: '',
    inspection_id: '',
    bill_id: '',
  })
  const { show, node: toast } = useToast()

  function load() {
    api
      .complaints()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // A complaint can be filed against a real inspection and/or a scanned bill — link by id.
    api.get<{ items: InspectionSummary[] }>('/inspections').then((d) => setInspections(d.items.slice(0, 50))).catch(() => setInspections([]))
    api.get<{ items: BillOut[] }>('/bills').then((d) => setBills(d.items.slice(0, 50))).catch(() => setBills([]))
  }, [])

  async function submit() {
    if (form.issue.trim().length < 10) {
      setError('Please describe the issue (at least 10 characters).')
      return
    }
    setBusy(true)
    setError('')
    try {
      const res = await api.post<{ complaint: ComplaintOut }>('/complaints', {
        ...form,
        inspection_id: form.inspection_id ? Number(form.inspection_id) : null,
        bill_id: form.bill_id ? Number(form.bill_id) : null,
      })
      setSelected(res.complaint)
      setForm({ product_name: '', store_name: '', category: 'PRICE', severity: 'MEDIUM', issue: '', inspection_id: '', bill_id: '' })
      load()
      show('Complaint submitted')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not submit the complaint')
    } finally {
      setBusy(false)
    }
  }

  async function advance(c: ComplaintOut, status: string) {
    setBusy(true)
    try {
      const res = await api.post<{ complaint: ComplaintOut }>(`/complaints/${c.id}/advance`, {
        status,
        note: status === 'RESOLVED' ? 'Resolved after evidence review.' : '',
      })
      setSelected(res.complaint)
      load()
      show(`Complaint moved to ${STAGE_LABEL[status] ?? status}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not update the complaint')
    } finally {
      setBusy(false)
    }
  }

  const items = data?.items ?? []
  const stages = data?.stages ?? ['SUBMITTED', 'UNDER_REVIEW', 'EVIDENCE_VERIFIED', 'RESOLVED']

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="page-title">Complaint Center</div>
          <div className="page-sub">
            File a complaint against a product, an inspection or a bill, and follow it through its
            lifecycle. Every status change is kept as append-only history.
          </div>
        </div>
        <div className="actions">
          {(data?.by_status?.SUBMITTED || data?.by_status?.UNDER_REVIEW) && (
            <span className="mode-pill">
              <Icon name="clock" size={13} />
              {(data?.by_status?.SUBMITTED ?? 0) + (data?.by_status?.UNDER_REVIEW ?? 0)} awaiting action
            </span>
          )}
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {toast}

      <div className="ws">
        <div>
          <Card title="Complaints">
            {loading ? (
              <Skeleton lines={5} />
            ) : items.length === 0 ? (
              <EmptyState
                glyph="◌"
                headline="No complaints yet"
                how="Create a complaint from a bill, a grocery item, an inspection — or file one below."
              />
            ) : (
              <table className="tbl">
                <thead>
                  <tr><th>Complaint</th><th>Product</th><th>Category</th><th>Filed</th><th>Status</th><th></th></tr>
                </thead>
                <tbody>
                  {items.map((c) => (
                    <tr key={c.id}>
                      <td className="mono">{c.complaint_number}</td>
                      <td>
                        {c.product_name || '—'}
                        {c.store_name && <div className="muted">{c.store_name}</div>}
                      </td>
                      <td>{c.category.replace(/_/g, ' ')}</td>
                      <td>{c.created_at.slice(0, 10)}</td>
                      <td>
                        <span className={`badge ${STATUS_BADGE[c.status] ?? 'NOT_APPLICABLE'}`}>
                          <span className="bdot" aria-hidden="true">●</span>
                          {STAGE_LABEL[c.status] ?? c.status}
                        </span>
                      </td>
                      <td>
                        <button className="btn sm secondary" onClick={() => setSelected(c)}>Open</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          {selected && (
            <Card title={`${selected.complaint_number} — ${selected.product_name || 'Complaint'}`}>
              <div className="grid cols-2">
                <div>
                  <dl className="meta-list">
                    <dt>Status</dt>
                    <dd>
                      <span className={`badge ${STATUS_BADGE[selected.status] ?? 'NOT_APPLICABLE'}`}>
                        <span className="bdot" aria-hidden="true">●</span>
                        {STAGE_LABEL[selected.status] ?? selected.status}
                      </span>
                    </dd>
                    <dt>Filed by</dt><dd>{selected.created_by}</dd>
                    <dt>Category</dt><dd>{selected.category.replace(/_/g, ' ')}</dd>
                    <dt>Severity</dt><dd>{selected.severity}</dd>
                    {selected.store_name && (<><dt>Store</dt><dd>{selected.store_name}</dd></>)}
                    {selected.inspection_id && (
                      <>
                        <dt>Inspection</dt>
                        <dd><Link to={`/inspections/${selected.inspection_id}`}>Open inspection #{selected.inspection_id}</Link></dd>
                      </>
                    )}
                    {selected.bill_id && (<><dt>Bill</dt><dd>#{selected.bill_id}</dd></>)}
                  </dl>
                  <p style={{ marginTop: 12, lineHeight: 1.5 }}>{selected.issue}</p>
                  {selected.resolution && <div className="alert success">{selected.resolution}</div>}
                </div>
                <div>
                  <div className="card-head" style={{ padding: '0 0 10px' }}>
                    <div className="grow"><span className="title">Timeline</span></div>
                    <span className="hint">append-only history</span>
                  </div>
                  <ul className="timeline">
                    {stages.map((s) => {
                      const entry = selected.timeline.find((t) => t.status === s)
                      return (
                        <li key={s} className={entry ? 'done' : ''}>
                          <div className="st">{STAGE_LABEL[s] ?? s}</div>
                          {entry ? (
                            <>
                              <div className="meta">{entry.actor} · {entry.at.replace('T', ' ')}</div>
                              {entry.note && <div className="note">{entry.note}</div>}
                            </>
                          ) : (
                            <div className="meta">Not reached</div>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                  {canAdvance && selected.status !== 'RESOLVED' && (
                    <div className="flex" style={{ marginTop: 14 }}>
                      {stages
                        .filter((s) => stages.indexOf(s) > stages.indexOf(selected.status))
                        .slice(0, 1)
                        .map((next) => (
                          <button key={next} className="btn" onClick={() => advance(selected, next)} disabled={busy}>
                            <Icon name="arrow-right" /> Move to {STAGE_LABEL[next] ?? next}
                          </button>
                        ))}
                      <button className="btn secondary" onClick={() => advance(selected, 'RESOLVED')} disabled={busy}>
                        <Icon name="check" /> Resolve
                      </button>
                    </div>
                  )}
                  {!canAdvance && selected.status !== 'RESOLVED' && (
                    <p className="muted" style={{ marginTop: 12 }}>
                      Moving a complaint along requires an INSPECTOR or ADMIN role. You can still
                      follow its progress here.
                    </p>
                  )}
                </div>
              </div>
            </Card>
          )}
        </div>

        <div>
          <Card title="File a complaint">
            <div className="field">
              <label htmlFor="c-product">Product</label>
              <input id="c-product" value={form.product_name} onChange={(e) => setForm({ ...form, product_name: e.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="c-store">Store / seller</label>
              <input id="c-store" value={form.store_name} onChange={(e) => setForm({ ...form, store_name: e.target.value })} />
            </div>
            <div className="grid cols-2">
              <div className="field">
                <label htmlFor="c-cat">Category</label>
                <select id="c-cat" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                  {CATEGORIES.map((c) => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="c-sev">Severity (your assessment)</label>
                <select id="c-sev" value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>
                  {['LOW', 'MEDIUM', 'HIGH'].map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="c-insp">Link to inspection</label>
                <select id="c-insp" value={form.inspection_id} onChange={(e) => setForm({ ...form, inspection_id: e.target.value })}>
                  <option value="">— none —</option>
                  {inspections.map((i) => (
                    <option key={i.id} value={i.id}>{i.inspection_number}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="c-bill">Link to bill</label>
                <select id="c-bill" value={form.bill_id} onChange={(e) => setForm({ ...form, bill_id: e.target.value })}>
                  <option value="">— none —</option>
                  {bills.map((b) => (
                    <option key={b.id} value={b.id}>#{b.id} {b.product_name}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="field">
              <label htmlFor="c-issue">What is the issue? *</label>
              <textarea
                id="c-issue"
                rows={5}
                value={form.issue}
                onChange={(e) => setForm({ ...form, issue: e.target.value })}
                placeholder="Describe what you observed, with the value printed on the package or bill where you can."
              />
            </div>
            <button className="btn" onClick={submit} disabled={busy}>
              <Icon name="message-square-warning" /> Submit complaint
            </button>
            <p className="muted" style={{ marginTop: 12, lineHeight: 1.5 }}>
              A complaint records what a person observed. It is not a legal determination, and the
              status history shows exactly who changed what and when.
            </p>
          </Card>
        </div>
      </div>
    </>
  )
}
