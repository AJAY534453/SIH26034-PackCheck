import { useEffect, useRef, useState } from 'react'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { Confidence, EmptyState, Skeleton, StatusBadge, useToast } from '../components/ui'
import { Icon } from '../components/Icon'
import type { ImageAnalysis } from '../types'

/** The file route is served by the API, which the dev server exposes under /api. */
const asset = (url: string) => `/api${url}`

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

const STATE_TEXT: Record<string, string> = {
  NO_TEXT: 'No readable text detected',
  LOW_CONFIDENCE: 'Low-confidence reading',
  TEXT_EXTRACTED: 'Text extracted',
}

type Mode = 'auto' | 'full' | 'text'

export default function Analysis() {
  const [result, setResult] = useState<ImageAnalysis | null>(null)
  const [history, setHistory] = useState<ImageAnalysis[]>([])
  const [mode, setMode] = useState<Mode>('auto')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [cameraOn, setCameraOn] = useState(false)
  const [cameraNote, setCameraNote] = useState('')
  const [preview, setPreview] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const { show, node: toast } = useToast()

  function loadHistory() {
    api.analyses().then((d) => setHistory(d.items)).catch(() => setHistory([]))
  }
  useEffect(loadHistory, [])

  function stopCamera() {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    setCameraOn(false)
  }
  useEffect(() => stopCamera, [])

  async function startCamera() {
    setCameraNote('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false })
      streamRef.current = stream
      setCameraOn(true)
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play().catch(() => undefined)
      }
    } catch {
      setCameraNote('Camera access is unavailable in this browser. Use “Upload image” instead.')
    }
  }

  function capture() {
    const video = videoRef.current
    if (!video) return
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth || 1280
    canvas.height = video.videoHeight || 720
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
    canvas.toBlob((blob) => {
      if (blob) void run(new File([blob], 'camera-capture.png', { type: 'image/png' }))
    }, 'image/png')
    stopCamera()
  }

  async function run(file: File) {
    setBusy(true)
    setError('')
    setResult(null)
    if (preview) URL.revokeObjectURL(preview)
    setPreview(URL.createObjectURL(file))
    try {
      const res = await api.analyze(file, mode)
      setResult(res.analysis)
      loadHistory()
      show(STATE_TEXT[res.analysis.state] ?? 'Analysis complete')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Analysis failed')
    } finally {
      setBusy(false)
    }
  }

  function onPick(files: FileList | null) {
    const file = files?.[0]
    if (file) void run(file)
  }

  const structured = result ? Object.entries(result.structured) : []

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <div className="page-title">Image Analysis</div>
          <div className="page-sub">
            Capture or upload an image. The image is enhanced two ways — for the whole picture and
            for text — then read by the on-device OCR engine and structured into product fields.
            The original image is preserved and stays attached to every result.
          </div>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {toast}

      <Card title="1 — Capture or upload">
        <div className="flex" style={{ gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
          <label htmlFor="analysis-mode" className="muted">Enhancement mode</label>
          <select id="analysis-mode" value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
            <option value="auto">Automatic (detect text)</option>
            <option value="full">Full-image enhancement</option>
            <option value="text">Text-focused enhancement</option>
          </select>
          <span className="muted" style={{ fontSize: 11.5 }}>
            Both representations are always produced so you can compare them.
          </span>
        </div>

        <div className="flex" style={{ gap: 10, flexWrap: 'wrap' }}>
          <button className="btn" onClick={() => fileInput.current?.click()} disabled={busy}>
            <Icon name="upload" /> Upload image
          </button>
          <button className="btn secondary" onClick={startCamera} disabled={busy || cameraOn}>
            <Icon name="camera" /> Use camera
          </button>
          {/* The capture attribute opens the device camera directly on mobile. */}
          <label className="btn secondary" style={{ display: 'inline-flex', gap: 6, cursor: 'pointer' }}>
            <Icon name="camera" /> Take photo
            <input
              type="file"
              accept="image/*"
              capture="environment"
              hidden
              onChange={(e) => onPick(e.target.files)}
            />
          </label>
          <input
            ref={fileInput}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            hidden
            onChange={(e) => onPick(e.target.files)}
          />
          {busy && <span className="mode-pill"><Icon name="cpu" size={13} /> Enhancing and reading…</span>}
        </div>
        {cameraNote && <div className="alert warn" style={{ marginTop: 12 }}>{cameraNote}</div>}

        {cameraOn && (
          <div style={{ marginTop: 12 }}>
            <video
              ref={videoRef}
              playsInline
              muted
              style={{ width: '100%', maxWidth: 520, borderRadius: 10, border: '1px solid var(--border)' }}
            />
            <div className="flex" style={{ marginTop: 8 }}>
              <button className="btn" onClick={capture}><Icon name="scan-line" /> Capture frame</button>
              <button className="btn secondary" onClick={stopCamera}>Cancel</button>
            </div>
          </div>
        )}

        {preview && !busy && (
          <div style={{ marginTop: 12 }}>
            <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>Just analysed</div>
            <img src={preview} alt="captured" style={{ maxWidth: 220, borderRadius: 8, border: '1px solid var(--border)' }} />
          </div>
        )}
      </Card>

      {busy && <Card><Skeleton lines={5} /></Card>}

      {result && (
        <>
          <Card
            title="2 — Result"
            right={
              <span className={`badge ${result.state === 'TEXT_EXTRACTED' ? 'PASS' : result.state === 'NO_TEXT' ? 'NOT_APPLICABLE' : 'UNCERTAIN'}`}>
                <span className="bdot" aria-hidden="true">{result.state === 'TEXT_EXTRACTED' ? '✓' : result.state === 'NO_TEXT' ? '–' : '!'}</span>
                {STATE_TEXT[result.state] ?? result.state}
              </span>
            }
          >
            <p className="muted" style={{ marginBottom: 12 }}>{result.message}</p>
            <dl className="kv">
              <dt>OCR engine</dt><dd>{result.engine || '—'}</dd>
              <dt>Text lines</dt><dd>{result.line_count}</dd>
              <dt>Mean confidence</dt><dd>{result.mean_confidence ? <Confidence value={result.mean_confidence} /> : '—'}</dd>
              <dt>Image quality</dt><dd><StatusBadge value={result.quality_status} /> <Confidence value={result.quality_score} showMark={false} /></dd>
              <dt>Source file</dt><dd className="mono">{result.original_filename}</dd>
              <dt>Analysed</dt><dd>{result.created_at.slice(0, 19).replace('T', ' ')}</dd>
            </dl>
          </Card>

          <div className="grid cols-3">
            <Card title="Original image">
              <img className="evidence-img" src={asset(result.original_url)} alt="original upload" />
              <p className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>Stored byte-for-byte as the evidence.</p>
            </Card>
            <Card title="Full-image enhancement">
              <img className="evidence-img" src={asset(result.enhanced_full_url)} alt="full enhancement" />
              <Ops ops={result.full_ops} />
            </Card>
            <Card title="Text-focused enhancement">
              <img className="evidence-img" src={asset(result.enhanced_text_url)} alt="text enhancement" />
              <Ops ops={result.text_ops} />
            </Card>
          </div>

          <Card title="3 — Extracted text">
            {result.ocr_lines.length === 0 ? (
              <EmptyState glyph="◌" headline="No readable text detected in this image." how="Add a clearer photo, fill more of the frame, and reduce glare or blur." />
            ) : (
              <>
                <pre className="ocr-text">{result.raw_text}</pre>
                <table className="tbl" style={{ marginTop: 12 }}>
                  <thead><tr><th>Line</th><th>Confidence</th></tr></thead>
                  <tbody>
                    {result.ocr_lines.map((l, i) => (
                      <tr key={i}>
                        <td>{l.text}</td>
                        <td style={{ width: 140 }}><Confidence value={l.confidence} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </Card>

          <Card title="4 — Structured product information">
            {structured.length === 0 ? (
              <EmptyState
                glyph="◌"
                headline="No structured declarations"
                how={result.state === 'NO_TEXT'
                  ? 'No text was read, so no field could be extracted.'
                  : 'Text was read but no declaration matched a known field (a document that is not a grocery/product label produces no structured fields — its raw text is above).'}
              />
            ) : (
              <table className="tbl">
                <thead><tr><th>Field</th><th>Value</th><th>State</th><th>Confidence</th></tr></thead>
                <tbody>
                  {structured.map(([name, f]) => (
                    <tr key={name}>
                      <td><b>{FIELD_LABELS[name] ?? name.replace(/_/g, ' ')}</b></td>
                      <td>{f.value || '—'}{f.state === 'CONFLICTING' && <div className="muted">multiple values detected</div>}</td>
                      <td><StatusBadge value={f.state} /></td>
                      <td><Confidence value={f.confidence} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="muted" style={{ marginTop: 10, fontSize: 12, lineHeight: 1.5 }}>
              Extraction is not validation: these are candidate readings from the image, offered for
              human confirmation. An image cannot prove a declaration is absent.
            </p>
          </Card>
        </>
      )}

      <Card title="Recent analyses">
        {history.length === 0 ? (
          <EmptyState glyph="◌" headline="Nothing analysed yet" how="Capture or upload an image above to create the first analysis." />
        ) : (
          <table className="tbl">
            <thead><tr><th>Preview</th><th>File</th><th>State</th><th>Lines</th><th>When</th><th></th></tr></thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.id}>
                  <td><img src={asset(h.original_url)} alt="" style={{ width: 54, height: 40, objectFit: 'cover', borderRadius: 6, border: '1px solid var(--border)' }} /></td>
                  <td className="mono">{h.original_filename}</td>
                  <td><StatusBadge value={h.state} /></td>
                  <td>{h.line_count}</td>
                  <td>{h.created_at.slice(0, 19).replace('T', ' ')}</td>
                  <td><button className="btn sm secondary" onClick={() => setResult(h)}>Open</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  )
}

function Ops({ ops }: { ops: string[] }) {
  if (!ops.length) return null
  return (
    <ul className="ops-list">
      {ops.map((o) => <li key={o}>{o}</li>)}
    </ul>
  )
}
