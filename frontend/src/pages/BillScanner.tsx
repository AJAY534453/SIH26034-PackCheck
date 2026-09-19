import { useEffect, useRef, useState } from 'react'
import { api } from '../services/api'
import { Icon } from '../components/Icon'
import { Card } from '../components/Badges'
import { EmptyState, Skeleton, useToast } from '../components/ui'
import type { AIStatus, BillOut, BillScanResponse } from '../types'

/** Human wording for the deterministic comparison result — never a legal verdict. */
const CMP_LABEL: Record<string, { text: string; cls: string; icon: string }> = {
  POTENTIAL_PRICE_DIFFERENCE: {
    text: 'Potential price difference — verify bill and product evidence',
    cls: 'alert warn',
    icon: 'alert-triangle',
  },
  PRICE_AT_OR_BELOW_MRP: { text: 'Billed at or below the printed MRP', cls: 'alert success', icon: 'check-circle' },
  INSUFFICIENT_DATA: {
    text: 'Insufficient data — a billed price and an MRP are both needed',
    cls: 'alert info',
    icon: 'info',
  },
}

const SOURCE_LABEL: Record<string, string> = {
  vision: 'read by the vision provider',
  manual: 'entered by the user',
  'vision+manual': 'read by the provider, then corrected by the user',
  demo: 'SAMPLE VALUES (demonstration only)',
}

export default function BillScanner() {
  const [ai, setAi] = useState<AIStatus | null>(null)
  const [bills, setBills] = useState<BillOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [scan, setScan] = useState<BillScanResponse | null>(null)
  // Manual entry mirrors the fields the scanner can read; kept in one object so a correction
  // updates the same bill row rather than creating a parallel one.
  const [form, setForm] = useState({ product_name: '', billed_price: '', mrp: '', quantity: '', store_name: '', bill_number: '' })
  const fileInput = useRef<HTMLInputElement>(null)

  function load() {
    api
      .get<{ items: BillOut[] }>('/bills')
      .then((d) => setBills(d.items))
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    api.aiStatus().then(setAi).catch(() => setAi(null))
    load()
  }, [])

  const { show, node: toast } = useToast()

  /** Populate the editable fields from a stored bill, so a correction starts from what is on record. */
  function fillForm(b: BillOut) {
    setForm({
      product_name: b.product_name || '',
      billed_price: b.billed_price != null ? String(b.billed_price) : '',
      mrp: b.mrp != null ? String(b.mrp) : '',
      quantity: b.quantity || '',
      store_name: b.store_name || '',
      bill_number: b.bill_number || '',
    })
  }

  function openBill(b: BillOut) {
    setScan({ bill: b, readings: {}, ai: ai!, message: '' })
    fillForm(b)
  }

  async function upload(file: File) {
    setBusy(true)
    setError('')
    try {
      const res = await api.scanBill(file)
      setScan(res)
      fillForm(res.bill)
      load()
      show(res.readings && Object.keys(res.readings).length ? 'Bill read — verify the values' : 'Bill stored')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed')
    } finally {
      setBusy(false)
    }
  }

  async function saveCorrection() {
    if (!scan) return
    setBusy(true)
    try {
      const res = await api.patch<{ bill: BillOut }>(`/bills/${scan.bill.id}`, {
        ...form,
        billed_price: form.billed_price === '' ? null : form.billed_price,
        mrp: form.mrp === '' ? null : form.mrp,
      })
      setScan({ ...scan, bill: res.bill })
      load()
      show('Bill updated and re-compared')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Update failed')
    } finally {
      setBusy(false)
    }
  }

  /** Enter a labelled SAMPLE bill so the demo flow can be shown without a real bill image. */
  async function loadSample() {
    setBusy(true)
    try {
      await api.post<{ bill: BillOut }>('/bills', {
        source: 'demo',
        store_name: 'SAMPLE STORE (demonstration)',
        bill_number: 'SAMPLE-0001',
        product_name: 'Sample Biscuits 200 g (demonstration data)',
        quantity: '1 x 200 g',
        billed_price: 45,
        mrp: 40,
      })
      load()
      show('Sample bill added and labelled as sample data')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not add the sample bill')
    } finally {
      setBusy(false)
    }
  }

  async function raiseComplaint(bill: BillOut) {
    try {
      await api.post('/complaints', {
        bill_id: bill.id,
        product_id: bill.product_id,
        product_name: bill.product_name,
        store_name: bill.store_name,
        category: 'PRICE',
        severity: 'MEDIUM',
        issue:
          `Price difference noted on bill ${bill.bill_number || `#${bill.id}`}: billed ₹${bill.billed_price} ` +
          `against a printed MRP of ₹${bill.mrp}. Please verify the bill and package evidence.`,
      })
      show('Complaint created from this bill')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not create the complaint')
    }
  }

  const cmp = scan ? CMP_LABEL[scan.bill.comparison_status] : null

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="page-title">Bill Scanner</div>
          <div className="page-sub">
            Read a shop bill, compare the charged price with the product's printed MRP, and raise a
            complaint if a difference needs verification.
          </div>
        </div>
        <div className="actions">
          {ai && (
            <span className={`mode-pill ${ai.enabled ? 'vision' : 'offline'}`} title={ai.reason}>
              <Icon name={ai.enabled ? 'sparkles' : 'cpu'} size={13} />
              {ai.enabled ? `Vision reading: ${ai.model}` : 'Reading unavailable — manual entry'}
            </span>
          )}
          <button className="btn secondary" onClick={loadSample} disabled={busy}>
            <Icon name="plus" /> Add labelled sample bill
          </button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {toast}

      <div className="ws">
        <div>
          <Card title="1. Upload the bill">
            <div
              className={`dropzone ${dragOver ? 'over' : ''}`}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragOver(false)
                const f = e.dataTransfer.files?.[0]
                if (f) upload(f)
              }}
              onClick={() => fileInput.current?.click()}
            >
              <Icon name="receipt" size={22} />
              <div style={{ marginTop: 6 }}>
                <b>{busy ? 'Reading…' : 'Drop a bill photo here'}</b>
              </div>
              <div className="muted" style={{ marginTop: 4 }}>
                or click to browse — JPG / PNG / WEBP. The image is stored as immutable evidence.
              </div>
              <input
                ref={fileInput}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) upload(f)
                }}
              />
            </div>
            {ai && !ai.enabled && (
              <div className="alert info" style={{ marginBottom: 0 }}>
                <b>Automated bill reading is not available.</b> {ai.reason} The uploaded image is
                still stored as evidence; enter the printed values below and the comparison runs on
                what you enter.
              </div>
            )}
          </Card>

          {scan && (
            <Card title="2. Verify the reading">
              <div className="card-head" style={{ padding: '0 0 12px' }}>
                <div className="grow">
                  <span className="prov">source: {SOURCE_LABEL[scan.bill.extraction_source] ?? scan.bill.extraction_source}</span>
                  {scan.bill.extraction_detail && (
                    <div className="muted" style={{ marginTop: 4 }}>{scan.bill.extraction_detail}</div>
                  )}
                </div>
                {scan.bill.extraction_confidence > 0 && (
                  <span className="prov">reading confidence {Math.round(scan.bill.extraction_confidence * 100)}%</span>
                )}
              </div>

              {Object.keys(scan.readings).length > 0 && (
                <table className="tbl" style={{ marginBottom: 14 }}>
                  <thead>
                    <tr><th>Field</th><th>Read value</th><th>Confidence</th><th>Printed line</th></tr>
                  </thead>
                  <tbody>
                    {Object.entries(scan.readings).map(([field, r]) => (
                      <tr key={field}>
                        <td>{field.replace(/_/g, ' ')}</td>
                        <td><b>{r.value}</b></td>
                        <td>{Math.round(r.confidence * 100)}%</td>
                        <td className="muted">{r.evidence_text || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <div className="grid cols-3">
                <div className="field">
                  <label htmlFor="b-product">Product on bill</label>
                  <input id="b-product" value={form.product_name} onChange={(e) => setForm({ ...form, product_name: e.target.value })} />
                </div>
                <div className="field">
                  <label htmlFor="b-billed">Billed price (₹)</label>
                  <input id="b-billed" value={form.billed_price} onChange={(e) => setForm({ ...form, billed_price: e.target.value })} />
                </div>
                <div className="field">
                  <label htmlFor="b-mrp">MRP read from the package (₹)</label>
                  <input id="b-mrp" value={form.mrp} onChange={(e) => setForm({ ...form, mrp: e.target.value })} />
                </div>
                <div className="field">
                  <label htmlFor="b-store">Store</label>
                  <input id="b-store" value={form.store_name} onChange={(e) => setForm({ ...form, store_name: e.target.value })} />
                </div>
                <div className="field">
                  <label htmlFor="b-num">Bill number</label>
                  <input id="b-num" value={form.bill_number} onChange={(e) => setForm({ ...form, bill_number: e.target.value })} />
                </div>
                <div className="field">
                  <label htmlFor="b-qty">Quantity</label>
                  <input id="b-qty" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} />
                </div>
              </div>
              <div className="flex">
                <button className="btn" onClick={saveCorrection} disabled={busy}>
                  <Icon name="check" /> Save correction and re-compare
                </button>
                {scan.bill.stored_filename && (
                  <a className="btn secondary" href={api.fileUrl('bills', scan.bill.stored_filename)} target="_blank" rel="noreferrer">
                    <Icon name="eye" /> View bill evidence
                  </a>
                )}
                <button className="btn secondary" onClick={() => raiseComplaint(scan.bill)}>
                  <Icon name="message-square-warning" /> Raise complaint
                </button>
              </div>
            </Card>
          )}
        </div>

        <div>
          <Card title="Price comparison">
            {!scan ? (
              <EmptyState
                glyph="◌"
                headline="No bill selected"
                how="Upload a bill, or open one from the list below to see its comparison."
              />
            ) : (
              <>
                <div className="price-cmp">
                  <div className="cell">
                    <div className="k">Billed price</div>
                    <div className="v">₹{scan.bill.billed_price ?? '—'}</div>
                  </div>
                  <div className="cell">
                    <div className="k">Printed MRP</div>
                    <div className="v">₹{scan.bill.mrp ?? '—'}</div>
                  </div>
                  <div className={`cell ${scan.bill.comparison_status === 'POTENTIAL_PRICE_DIFFERENCE' ? 'hot' : ''}`}>
                    <div className="k">Difference</div>
                    <div className="v">
                      {scan.bill.price_difference != null ? `₹${scan.bill.price_difference}` : '—'}
                    </div>
                    {scan.bill.price_difference_pct != null && (
                      <div className="muted">{scan.bill.price_difference_pct}% of MRP</div>
                    )}
                  </div>
                </div>
                {cmp && (
                  <div className={cmp.cls} style={{ marginTop: 14, marginBottom: 0 }}>
                    <Icon name={cmp.icon} /> <b>{cmp.text}</b>
                  </div>
                )}
                <p className="muted" style={{ marginTop: 10, lineHeight: 1.5 }}>
                  The comparison is arithmetic performed by the backend on the stored values — the
                  vision provider only reads the printed text. A price difference is a prompt to
                  check evidence, not a finding of law.
                </p>
              </>
            )}
          </Card>

          <Card title="Scanned bills">
            {loading ? (
              <Skeleton lines={4} />
            ) : bills.length === 0 ? (
              <EmptyState
                glyph="◌"
                headline="No bills yet"
                how="Upload a bill photo to store it as evidence and compare the charged price with the MRP."
              />
            ) : (
              <table className="tbl">
                <thead>
                  <tr><th>Bill</th><th>Product</th><th>Billed</th><th>MRP</th><th>Status</th><th>Source</th><th></th></tr>
                </thead>
                <tbody>
                  {bills.map((b) => (
                    <tr key={b.id}>
                      <td className="mono">#{b.id}</td>
                      <td>
                        {b.product_name || '—'}
                        {b.extraction_source === 'demo' && <span className="sample-flag" style={{ marginLeft: 6 }}>SAMPLE</span>}
                      </td>
                      <td>{b.billed_price != null ? `₹${b.billed_price}` : '—'}</td>
                      <td>{b.mrp != null ? `₹${b.mrp}` : '—'}</td>
                      <td>
                        {b.comparison_status === 'POTENTIAL_PRICE_DIFFERENCE' ? (
                          <span className="badge CONFLICTING">⚠ Potential difference</span>
                        ) : b.comparison_status === 'PRICE_AT_OR_BELOW_MRP' ? (
                          <span className="badge PASS">✓ At or below MRP</span>
                        ) : (
                          <span className="badge NOT_APPLICABLE">– Insufficient data</span>
                        )}
                      </td>
                      <td className="muted">{b.extraction_source}</td>
                      <td className="flex">
                        <button className="btn sm secondary" onClick={() => openBill(b)}>
                          Open
                        </button>
                        {b.stored_filename && (
                          <a className="btn sm ghost" href={api.fileUrl('bills', b.stored_filename)} target="_blank" rel="noreferrer">
                            Evidence
                          </a>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>
      </div>
    </>
  )
}
