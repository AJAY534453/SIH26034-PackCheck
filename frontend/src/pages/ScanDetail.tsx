import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { Confidence, EmptyState, Skeleton, StatusChip, useToast } from '../components/ui'
import { CheckList, FontSizePanel, ScoreBasis, VerdictSplit } from '../components/CompliancePanels'
import type { FontSizeCompliance, ScanDetailPayload } from '../types'

type Tab = 'checks' | 'evidence' | 'history'

export default function ScanDetail() {
  const { id } = useParams()
  const scanId = Number(id)
  const [scan, setScan] = useState<ScanDetailPayload | null>(null)
  const [fontSize, setFontSize] = useState<FontSizeCompliance | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<Tab>('checks')
  const [decision, setDecision] = useState('COMPLIANT')
  const [remarks, setRemarks] = useState('')
  const [busy, setBusy] = useState(false)
  const [finalizeError, setFinalizeError] = useState('')
  const { show, node: toast } = useToast()

  const load = useCallback(() => {
    if (!scanId) return
    setLoading(true)
    setError('')
    api
      .repositoryScan(scanId)
      .then((data) => {
        setScan(data)
        setRemarks(data.remarks || '')
        if (data.official_decision) setDecision(data.official_decision)
        return api.fontSizeCompliance(data.inspection_id).then(setFontSize).catch(() => setFontSize(null))
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load this scan'))
      .finally(() => setLoading(false))
  }, [scanId])

  useEffect(() => {
    load()
  }, [load])

  async function saveRemarks() {
    if (!scan) return
    setBusy(true)
    setFinalizeError('')
    try {
      const res = await api.repositoryRemarks(scan.id, remarks)
      setScan(res.scan)
      show('Remarks saved.')
    } catch (e) {
      setFinalizeError(e instanceof Error ? e.message : 'Could not save the remarks')
    } finally {
      setBusy(false)
    }
  }

  async function finalize() {
    if (!scan) return
    if (!remarks.trim()) {
      setFinalizeError('A remark is required: it is recorded as the reason for the official decision.')
      return
    }
    setBusy(true)
    setFinalizeError('')
    try {
      const res = await api.repositoryFinalize(scan.id, decision, remarks)
      setScan(res.scan)
      show('Official decision recorded.')
    } catch (e) {
      setFinalizeError(e instanceof Error ? e.message : 'Could not record the decision')
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <Card><Skeleton lines={8} /></Card>
  if (error) return <div className="alert error">{error}</div>
  if (!scan) {
    return (
      <Card>
        <EmptyState headline="Scan not found" how="It may belong to another organization or have been removed." action={<Link className="btn sm secondary" to="/repository">Back to the repository</Link>} />
      </Card>
    )
  }

  const imageUrl = scan.image_url ? api.fileUrl('originals', scan.image_filename) : ''
  const structured = Object.entries(scan.structured_fields || {})

  return (
    <>
      {toast}
      <div className="flex" style={{ justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 10 }}>
        <div>
          <div className="page-title">{scan.product_name || '(product name not read)'}</div>
          <div className="page-sub" style={{ marginBottom: 8 }}>
            {scan.brand ? `${scan.brand} · ` : ''}{scan.category || 'OTHER'}
            {scan.manufacturer ? ` · ${scan.manufacturer}` : ''}
          </div>
          {scan.report_status && (
            <span className={`badge ${scan.report_status}`} title="Derived from the pipeline state and the review state — the report status model is documented in docs/ARCHITECTURE.md">
              <i className="bdot" />
              <span>{scan.report_status_label || scan.report_status}</span>
            </span>
          )}
        </div>
        <div className="flex" style={{ gap: 8 }}>
          <Link className="btn sm secondary" to={`/inspections/${scan.inspection_id}`}>Open inspection</Link>
          <Link className="btn sm secondary" to={`/listings?inspection=${scan.inspection_id}`}>
            Check online listing
          </Link>
          {scan.product_id && (
            <Link className="btn sm secondary" to={`/repository/products/${scan.product_id}`}>Product history</Link>
          )}
        </div>
      </div>

      <VerdictSplit
        aiVerdict={scan.ai_verdict}
        score={scan.compliance_score}
        threshold={scan.threshold}
        coverage={scan.coverage_score}
        coverageFloor={scan.coverage_floor}
        reviewStatus={scan.review_status}
        officialDecision={scan.official_decision}
        finalizedBy={scan.finalized_by}
        finalizedAt={scan.finalized_at}
        remarks={scan.remarks}
        summary={scan.ai_summary}
        recommendedAction={scan.recommended_action}
        statusMessage={scan.status_message}
      />

      <div className="tabs" role="tablist" aria-label="Scan sections">
        <button role="tab" aria-selected={tab === 'checks'} className={`tab ${tab === 'checks' ? 'on' : ''}`} onClick={() => setTab('checks')}>
          Checks ({scan.checks.length})
        </button>
        <button role="tab" aria-selected={tab === 'evidence'} className={`tab ${tab === 'evidence' ? 'on' : ''}`} onClick={() => setTab('evidence')}>
          Evidence &amp; extracted text
        </button>
        <button role="tab" aria-selected={tab === 'history'} className={`tab ${tab === 'history' ? 'on' : ''}`} onClick={() => setTab('history')}>
          Compliance history ({scan.history?.length ?? 0})
        </button>
      </div>

      {tab === 'checks' && (
        <>
          <Card title="How this percentage was computed">
            <ScoreBasis scoring={scan.scoring} />
          </Card>
          <Card title="Requirement checks (automated, evidence-bound)">
            <CheckList checks={scan.checks} />
          </Card>
          {scan.violations.length > 0 && (
            <Card title={`Detected violations and findings (${scan.violations.length})`}>
              {scan.violations.map((v) => (
                <div className={`check-row ${v.status === 'OPEN' ? 'FAIL' : 'UNCERTAIN'}`} key={v.id}>
                  <div className="head">
                    <span className="rule">Rule {v.rule_number}</span>
                    <StatusChip value={v.status} />
                    <StatusChip value={v.severity} />
                    <span className="t">{v.title}</span>
                  </div>
                  <div className="why">{v.description}</div>
                  {v.legal_reference && <div className="ref">Legal reference: {v.legal_reference}</div>}
                </div>
              ))}
            </Card>
          )}
          {fontSize && <FontSizePanel data={fontSize} imageUrl={imageUrl} onCalibrated={load} />}
        </>
      )}

      {tab === 'evidence' && (
        <>
          <Card title="Scan evidence">
            <div className="split">
              <div>
                {imageUrl ? (
                  <img src={imageUrl} alt={`Scanned product ${scan.product_name}`} style={{ width: '100%', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)' }} />
                ) : (
                  <EmptyState headline="No image stored for this scan" how="The scan was created without an image file (for example from an older record)." />
                )}
                <div className="kv" style={{ marginTop: 12 }}>
                  <dt>Scanned</dt><dd>{scan.scanned_at.slice(0, 19).replace('T', ' ')} by {scan.scanned_by || '—'}</dd>
                  <dt>Scan reference</dt><dd className="ident">{scan.inspection_number}</dd>
                  <dt>Images</dt><dd>{scan.image_count}</dd>
                  <dt>AI confidence</dt><dd><Confidence value={scan.ai_confidence} /></dd>
                  <dt>Threshold</dt><dd>{scan.threshold}%</dd>
                </div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>Extracted text (OCR, as stored with the scan)</div>
                <div className="text-view">{scan.extracted_text || 'No text was recognised for this scan.'}</div>
                <div className="muted" style={{ fontSize: 12, margin: '14px 0 6px' }}>Structured declarations</div>
                <div style={{ overflowX: 'auto' }}>
                  <table className="measure-table">
                    <thead><tr><th>Declaration</th><th>Value</th><th>State</th><th>Confidence</th></tr></thead>
                    <tbody>
                      {structured.length === 0 && (
                        <tr><td colSpan={4}>No declarations were extracted for this scan.</td></tr>
                      )}
                      {structured.map(([name, field]) => (
                        <tr key={name}>
                          <td>{name}</td>
                          <td>{field.value || '—'}</td>
                          <td>{field.state}</td>
                          <td className="num">{field.value ? `${Math.round((field.confidence || 0) * 100)}%` : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </Card>
          {scan.analyses && scan.analyses.length > 0 && (
            <Card title="Related image analyses">
              <ul style={{ margin: '0 0 0 18px', lineHeight: 1.7, fontSize: 13 }}>
                {scan.analyses.map((a) => (
                  <li key={a.id}>
                    #{a.id} — {a.state} · {a.message} · {a.created_at.slice(0, 19).replace('T', ' ')}
                    {' · '}
                    <a href={api.fileUrl('processed', a.enhanced_text_url.split('/').pop() || '')} target="_blank" rel="noreferrer">text-enhanced image</a>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}

      {tab === 'history' && (
        <Card title="Previous compliance history for this product">
          {scan.history && scan.history.length > 0 ? (
            scan.history.map((h) => (
              <div className="history-item" key={h.id}>
                <div className="when">{h.scanned_at.slice(0, 19).replace('T', ' ')}</div>
                <div className="what">
                  <div>
                    <Link to={`/repository/scans/${h.id}`}>{h.inspection_number}</Link>{' '}
                    — score {h.compliance_score}% · AI verdict {h.ai_verdict}
                  </div>
                  <div className="muted" style={{ fontSize: 12.5 }}>
                    {h.status_message}
                    {h.official_decision ? ` (final: ${h.official_decision})` : ''}
                  </div>
                </div>
                <StatusChip value={h.review_status} />
              </div>
            ))
          ) : (
            <EmptyState
              headline="No earlier scans of this product"
              how="When the same product is scanned again, its previous results appear here so the trend is auditable."
            />
          )}
        </Card>
      )}

      {scan.can_finalize ? (
        <Card title="Official finalization">
          <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
            Recording the official decision is a human determination. It is stored next to — never instead
            of — the automated verdict, and the remark is the recorded reason.
          </p>
          <div className="field">
            <label htmlFor="fin-decision">Official decision</label>
            <select id="fin-decision" value={decision} onChange={(e) => setDecision(e.target.value)}>
              <option value="COMPLIANT">Compliant</option>
              <option value="NON_COMPLIANT">Non-compliant</option>
              <option value="NEEDS_MANUAL_REVIEW">Needs further review</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="fin-remarks">Remarks (required)</label>
            <textarea
              id="fin-remarks"
              rows={3}
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              placeholder="e.g. Verified the label against the physical package on 18 Sep; batch code and MRP match."
            />
          </div>
          {finalizeError && <div className="alert error">{finalizeError}</div>}
          <div className="flex">
            <button className="btn" onClick={finalize} disabled={busy || !remarks.trim()}>
              {busy ? 'Recording…' : scan.review_status === 'FINALIZED' ? 'Update official decision' : 'Record official decision'}
            </button>
            <button className="btn sm secondary" onClick={saveRemarks} disabled={busy || !remarks.trim()}>
              Save remarks only
            </button>
          </div>
        </Card>
      ) : (
        <div className="alert info">
          You can read the result but not finalize it. Finalization is limited to authorized officials
          (inspector, enforcement officer, internal compliance or administrator).
        </div>
      )}
    </>
  )
}
