import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { Icon } from '../components/Icon'
import { Confidence, DecisionBanner, EmptyState, StatusBadge, useToast } from '../components/ui'
import type { AIStatus, ImageOut, InspectionDetail, InspectionSummary } from '../types'

const ROLES = ['FRONT', 'BACK', 'LEFT_SIDE', 'RIGHT_SIDE', 'TOP', 'BOTTOM', 'CLOSE_UP', 'ADDITIONAL_EVIDENCE']

/**
 * The pipeline as executed by the backend. `vision_extraction` is an OPTIONAL stage: it runs
 * only when a vision provider is configured, and it is reported as `skipped` otherwise so the
 * operator can see exactly which perception sources produced the result.
 */
const STAGES: [string, string][] = [
  ['upload_validation', 'Upload validation'],
  ['original_preservation', 'Original preservation'],
  ['quality_assessment', 'Quality assessment'],
  ['preprocessing', 'Preprocessing'],
  ['text_detection_ocr', 'Text detection / OCR'],
  ['region_ocr', 'Region OCR'],
  ['vision_extraction', 'Vision extraction (optional)'],
  ['candidate_generation', 'Candidate generation'],
  ['field_extraction', 'Field extraction'],
  ['normalization', 'Normalization'],
  ['classification', 'Classification'],
  ['rule_applicability', 'Rule applicability'],
  ['rule_validation', 'Rule validation'],
  ['evidence_generation', 'Evidence generation'],
  ['inspection_result', 'Inspection result'],
]

/** SCAN → EXTRACT → VERIFY → ALERT → REPORT: the stages this page walks through. */
const RAIL = ['Product Images', 'AI Extraction', 'Validation', 'Compliance', 'Report']

const FIELD_LABELS: Record<string, string> = {
  product_name: 'Product Name',
  brand: 'Brand',
  common_name: 'Common Name',
  manufacturer: 'Manufacturer',
  manufacturer_address: 'Manufacturer Address',
  packer: 'Packer',
  importer: 'Importer',
  marketer: 'Marketed By',
  country_of_origin: 'Country of Origin',
  net_quantity: 'Net Quantity',
  mrp: 'MRP',
  unit_sale_price: 'Unit Sale Price',
  date_manufacturing: 'Manufacturing Date',
  date_packing: 'Packing Date',
  date_import: 'Import Date',
  date_expiry: 'Expiry / Use By',
  date_best_before: 'Best Before',
  batch_lot: 'Batch / Lot',
  consumer_care_phone: 'Consumer Care Phone',
  consumer_care_email: 'Consumer Care Email',
  website: 'Website',
  fssai_license: 'FSSAI Licence',
}

interface Pending {
  file: File
  role: string
  preview: string
}

export default function NewInspection() {
  const navigate = useNavigate()
  const [ai, setAi] = useState<AIStatus | null>(null)
  const [pending, setPending] = useState<Pending[]>([])
  const [inspection, setInspection] = useState<InspectionSummary | null>(null)
  const [detail, setDetail] = useState<InspectionDetail | null>(null)
  const [images, setImages] = useState<ImageOut[]>([])
  const [stageStatus, setStageStatus] = useState<Record<string, string>>({})
  const [status, setStatus] = useState('')
  const [decision, setDecision] = useState<string | null>(null)
  const [summary, setSummary] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [groceryBusy, setGroceryBusy] = useState(false)
  const [groceryAdded, setGroceryAdded] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const { show, node: toast } = useToast()

  useEffect(() => {
    api.aiStatus().then(setAi).catch(() => setAi(null))
  }, [])

  function addFiles(files: FileList | null) {
    if (!files) return
    const next: Pending[] = [...pending]
    for (const f of Array.from(files)) {
      if (!/\.(jpe?g|png|webp)$/i.test(f.name)) {
        setError(`Unsupported file: ${f.name} (allowed: JPG, PNG, WEBP)`)
        continue
      }
      // First image defaults to FRONT (back/side judgement is the operator's), the rest stay
      // unassigned evidence until the operator labels them.
      next.push({ file: f, role: next.length === 0 ? 'FRONT' : 'ADDITIONAL_EVIDENCE', preview: URL.createObjectURL(f) })
    }
    setPending(next)
    setError('')
  }

  function setRole(i: number, role: string) {
    const next = [...pending]
    next[i] = { ...next[i], role }
    setPending(next)
  }

  function removePending(i: number) {
    setPending(pending.filter((_, idx) => idx !== i))
  }

  async function uploadAll(id: number): Promise<ImageOut[]> {
    const out: ImageOut[] = []
    for (const p of pending) {
      const form = new FormData()
      form.append('file', p.file)
      form.append('role', p.role)
      out.push(await api.postForm<ImageOut>(`/inspections/${id}/images`, form))
    }
    return out
  }

  async function poll(id: number) {
    for (let i = 0; i < 60; i++) {
      const p = await api.get<{ id: number; status: string; stage_status: Record<string, string>; final_decision: string | null }>(
        `/inspections/${id}/progress`,
      )
      setStageStatus(p.stage_status || {})
      setStatus(p.status)
      if (p.final_decision) {
        setDecision(p.final_decision)
        const d = await api.get<InspectionDetail>(`/inspections/${id}`)
        setDetail(d)
        setSummary(d.summary || '')
        return
      }
      await new Promise((r) => setTimeout(r, 1000))
    }
    setError('Processing is taking longer than expected. Open the inspection to see its current state.')
  }

  async function start() {
    setBusy(true)
    setError('')
    try {
      const insp = await api.post<InspectionSummary>('/inspections')
      setInspection(insp)
      setStatus('UPLOADING')
      const uploaded = await uploadAll(insp.id)
      setImages(uploaded)
      setStatus('PROCESSING')
      await api.post(`/inspections/${insp.id}/process`)
      await poll(insp.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Inspection failed')
      setStatus('')
    } finally {
      setBusy(false)
    }
  }

  function stageState(key: string): string {
    return stageStatus[key] || 'pending'
  }

  /** Adding to Grocery is an explicit, recorded user action — never a side effect of scanning. */
  async function addToGrocery() {
    if (!detail || !inspection) return
    const val = (name: string) => detail.fields.find((f) => f.field_name === name)?.display_value || ''
    setGroceryBusy(true)
    try {
      const res = await api.post<{ message: string; expiry_note?: string }>('/grocery', {
        inspection_id: inspection.id,
        product_name: val('product_name') || `Inspection ${inspection.inspection_number}`,
        brand: val('brand'),
        quantity: val('net_quantity'),
        mrp: val('mrp'),
        batch_lot: val('batch_lot'),
        mfg_date: val('date_manufacturing'),
        best_before_text: val('date_best_before'),
        expiry_date: /^\d{4}-\d{2}-\d{2}$/.test(val('date_expiry')) ? val('date_expiry') : '',
      })
      setGroceryAdded(true)
      show(res.expiry_note ? `Added to Grocery — ${res.expiry_note}` : 'Added to your grocery tracker')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not add to grocery')
    } finally {
      setGroceryBusy(false)
    }
  }

  const fields = detail?.fields ?? []
  const critical = fields.filter((f) => ['CONFLICTING', 'UNCERTAIN'].includes(f.state) && (f.display_value || f.uncertainty_reason))
  const confirmed = fields.filter((f) => f.state === 'DETECTED')
  const notDetected = fields.filter((f) => f.state === 'MISSING')
  const flagged = detail ? detail.rules.filter((r) => r.status === 'FAIL' || r.status === 'UNCERTAIN') : []

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="page-title">
            New Inspection
            {inspection && <span className="ident">{inspection.inspection_number}</span>}
          </div>
          <div className="page-sub">
            Upload the package faces you could photograph. One inspection can carry many images —
            declarations are fused across all of them.
          </div>
        </div>
        <div className="actions">
          {ai && (
            <span className={`mode-pill ${ai.enabled ? 'vision' : 'offline'}`} title={ai.reason}>
              <Icon name={ai.enabled ? 'sparkles' : 'cpu'} size={13} />
              {ai.enabled ? `Vision ${ai.model} + OCR` : 'On-device OCR'}
            </span>
          )}
        </div>
      </div>

      <div className="card" style={{ padding: '14px 18px' }}>
        <div className="rail" aria-label="Inspection stages">
          {RAIL.map((label, i) => {
            const on = !inspection || (i === 0 && status !== '') || (i === 1 && !!status) || (i === 2 && !!decision) || (i === 3 && !!decision) || (i === 4 && !!detail?.reports?.length)
            return (
              <div key={label} style={{ display: 'contents' }}>
                <div className={`step ${on ? 'on' : ''}`}>
                  <span className="n">{String(i + 1).padStart(2, '0')}</span>
                  <span className="lbl">{label}</span>
                </div>
                {i < RAIL.length - 1 && <span className="sep" />}
              </div>
            )
          })}
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {toast}

      {!inspection && (
        <>
          <div className="guide-strip" aria-label="Capture guidance">
            <span className="tip"><b>Capture</b> the front, back and side panels when available</span>
            <span className="tip"><b>Ensure</b> the MRP, batch and date areas are readable</span>
            <span className="tip"><b>Keep</b> the package flat and avoid glare</span>
            <span className="tip"><b>Label</b> each image's role so evidence is traceable</span>
          </div>

          <Card title="01 — Capture product images">
            <div
              className={`dropzone ${dragOver ? 'over' : ''}`}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files) }}
              onClick={() => fileInput.current?.click()}
            >
              <Icon name="image-plus" size={22} />
              <div style={{ marginTop: 6 }}>
                <b>Drag &amp; drop package images here</b>
              </div>
              <div className="muted" style={{ marginTop: 4 }}>
                or click to browse — JPG / PNG / WEBP · front, back, side, top, close-up
              </div>
              <input
                ref={fileInput}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                hidden
                onChange={(e) => addFiles(e.target.files)}
              />
            </div>

            {pending.length > 0 && (
              <div className="tile-grid">
                {pending.map((p, i) => (
                  <div className="tile filled" key={i}>
                    <div className="thumb">
                      <img src={p.preview} alt={p.file.name} />
                      <span className="role-tag">{p.role.replace(/_/g, ' ')}</span>
                    </div>
                    <div className="chips">
                      <span className="chip">{Math.round(p.file.size / 1024)} KB</span>
                      <span className="chip">awaiting quality check</span>
                    </div>
                    <select value={p.role} onChange={(e) => setRole(i, e.target.value)} aria-label={`Role for ${p.file.name}`}>
                      {ROLES.map((r) => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
                    </select>
                    <div className="row">
                      <button className="btn sm secondary" style={{ flex: 1 }} onClick={() => removePending(i)}>
                        Remove
                      </button>
                    </div>
                  </div>
                ))}
                {['Top', 'Bottom', 'Close-up'].map((label) => (
                  <div className="tile empty" key={label}>
                    <div className="thumb" style={{ border: 'none', background: 'transparent' }}>
                      <div className="ph">
                        <div className="inner">
                          <span className="glyph"><Icon name="camera" /></span>
                          {label} (optional)
                        </div>
                      </div>
                    </div>
                    <div className="row">
                      <button className="btn sm" style={{ flex: 1 }} onClick={() => fileInput.current?.click()}>
                        <Icon name="upload" /> Add image
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="flex mt">
              <button className="btn" onClick={start} disabled={busy || pending.length === 0}>
                <Icon name="scan-line" />
                {busy ? 'Processing…' : `Start scan (${pending.length} image${pending.length === 1 ? '' : 's'})`}
              </button>
              {pending.length === 0 && <span className="muted">Upload at least one image to begin.</span>}
            </div>
          </Card>
        </>
      )}

      {inspection && (
        <>
          <Card title="02 — Pipeline progress">
            <div className="kv" style={{ marginBottom: 12 }}>
              <dt>Inspection</dt><dd><b>{inspection.inspection_number}</b></dd>
              <dt>Images</dt><dd>{images.length || 'uploading…'}</dd>
              <dt>Status</dt><dd>{status}</dd>
            </div>
            <div className="pipeline" aria-live="polite">
              {STAGES.map(([key, label]) => {
                const st = stageState(key)
                const dot = st === 'done' ? '✓' : st === 'running' ? '●' : st === 'failed' ? '✕' : st === 'skipped' ? '–' : '·'
                return (
                  <div className={`stage ${st}`} key={key}>
                    <span className="dot" aria-hidden="true">{dot}</span> {label}
                    {st === 'skipped' && <span className="muted"> (not required for this run)</span>}
                  </div>
                )
              })}
            </div>
          </Card>

          {decision && detail && (
            <>
              <DecisionBanner decision={decision} why={summary}>
                <button className="btn" onClick={() => navigate(`/inspections/${inspection.id}`)}>
                  <Icon name="search-check" /> Review evidence
                </button>
                <button className="btn secondary" onClick={addToGrocery} disabled={groceryBusy || groceryAdded}>
                  <Icon name="shopping-cart" /> {groceryAdded ? 'In Grocery' : 'Add to Grocery'}
                </button>
                <button className="btn secondary" onClick={() => navigate('/bills')}>
                  <Icon name="receipt" /> Scan a bill
                </button>
              </DecisionBanner>

              <div className="ws">
                <div>
                  <Card title="Critical items" right={undefined}>
                    {critical.length === 0 ? (
                      <EmptyState
                        glyph="✓"
                        headline="Nothing needs manual resolution"
                        how="No conflicting or uncertain declarations were recorded for this inspection."
                      />
                    ) : (
                      critical.map((f) => (
                        <div key={f.id} className="flex" style={{ alignItems: 'flex-start', marginBottom: 12 }}>
                          <Icon name="alert-triangle" />
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <b style={{ fontSize: 13 }}>{FIELD_LABELS[f.field_name] ?? f.field_name.replace(/_/g, ' ')}</b>
                            <div className="muted">
                              {f.display_value ? `offered value: ${f.display_value} — ` : ''}
                              {f.uncertainty_reason || 'evidence sources disagree'}
                            </div>
                          </div>
                          <StatusBadge value={f.state} />
                        </div>
                      ))
                    )}
                    {flagged.length > 0 && (
                      <p className="muted" style={{ marginTop: 8 }}>
                        {flagged.length} rule check{flagged.length === 1 ? '' : 's'} could not be decided
                        from the supplied images (UNCERTAIN). Uncertain is not a failure — it routes to
                        manual review.
                      </p>
                    )}
                  </Card>

                  <Card title="Rule evaluation">
                    <table className="tbl">
                      <thead><tr><th>Rule</th><th>Requirement</th><th>Observed</th><th>Result</th></tr></thead>
                      <tbody>
                        {detail.rules.map((r) => (
                          <tr key={r.id}>
                            <td>{r.rule_number}<div className="muted">{r.title}</div></td>
                            <td className="muted">{r.expected}</td>
                            <td>{r.observed || '—'}</td>
                            <td><StatusBadge value={r.status} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Card>
                </div>

                <div>
                  <div className="card pad0">
                    <div className="card-head">
                      <Icon name="sparkles" />
                      <div className="grow">
                        <span className="title">{confirmed.length} declarations detected</span>
                        <div className="hint">
                          Click any row on the inspection page to jump to its evidence region.
                        </div>
                      </div>
                    </div>
                    {confirmed.length === 0 ? (
                      <EmptyState
                        glyph="◌"
                        headline="No declarations detected"
                        how="The images were processed but no declaration could be read with confidence. Add clearer views (back, side, close-up) and reprocess."
                      />
                    ) : (
                      confirmed.map((f) => {
                        const pct = Math.round((f.confidence || 0) * 100)
                        return (
                          <div className="field-row" key={f.id}>
                            <span className="fname">{FIELD_LABELS[f.field_name] ?? f.field_name.replace(/_/g, ' ')}</span>
                            <span className="fval" title={f.display_value}>{f.display_value}</span>
                            <span className={`fconf ${pct < 70 ? 'low' : ''}`}>
                              <span className="bar"><i style={{ width: `${pct}%` }} /></span>
                              <span className="pct">{pct}%</span>
                            </span>
                            <span className="pin" title={`Source: ${f.source_engine || f.source} · ${f.preprocessing_variant || 'original'}`}>
                              <Icon name="map-pin" size={13} />
                            </span>
                          </div>
                        )
                      })
                    )}
                  </div>

                  <Card title="Not detected in supplied images">
                    {notDetected.length === 0 ? (
                      <p className="muted">Every tracked declaration was either detected or offered for review.</p>
                    ) : (
                      <>
                        <p className="muted" style={{ marginBottom: 8 }}>
                          These declarations were not found in the images you supplied. That is not
                          proof they are absent from the package — they may be printed on a face you
                          did not photograph.
                        </p>
                        <div className="flex">
                          {notDetected.map((f) => (
                            <span className="badge MISSING" key={f.id}>
                              <span className="bdot" aria-hidden="true">–</span>
                              {FIELD_LABELS[f.field_name] ?? f.field_name.replace(/_/g, ' ')}
                            </span>
                          ))}
                        </div>
                      </>
                    )}
                  </Card>

                  <Card title="Image quality">
                    <table className="tbl">
                      <thead><tr><th>Image</th><th>Role</th><th>Resolution</th><th>Quality</th><th>OCR lines</th></tr></thead>
                      <tbody>
                        {detail.images.map((im) => (
                          <tr key={im.id}>
                            <td>{im.original_filename}</td>
                            <td>{im.role.replace(/_/g, ' ')}</td>
                            <td className="mono">{im.width}×{im.height}</td>
                            <td><StatusBadge value={im.quality_status} /> <Confidence value={im.quality_score} showMark={false} /></td>
                            <td>{im.ocr_line_count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Card>

                  <div className="flex">
                    <button
                      className="btn secondary"
                      onClick={() => {
                        setInspection(null)
                        setDetail(null)
                        setImages([])
                        setStageStatus({})
                        setDecision(null)
                        setSummary('')
                        setStatus('')
                        setPending([])
                        setGroceryAdded(false)
                      }}
                    >
                      <Icon name="refresh-cw" /> New inspection
                    </button>
                  </div>
                </div>
              </div>
            </>
          )}

          {!decision && (
            <Card title="Reading the package">
              <p className="muted">
                The pipeline is running. Images are preserved byte-for-byte first, then assessed for
                quality, preprocessed, read by the on-device OCR engine{ai?.enabled ? ' and the vision provider' : ''},
                and finally turned into validated declarations with evidence regions.
              </p>
            </Card>
          )}
        </>
      )}
    </>
  )
}
