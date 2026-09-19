// Compliance presentation primitives: the AI preliminary verdict is ALWAYS visually separated from
// the official final decision, and a measured requirement always shows required vs detected plus the
// legal reference it was compared against. No page may re-implement these.
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../services/api'
import type { ComplianceCheck, FontSizeCompliance, ReviewStatus, ScanScoring } from '../types'
import { Icon } from './Icon'
import { StatusBadge, StatusChip } from './ui'

export function scoreTone(score: number | null | undefined, threshold: number): 'pass' | 'warn' | 'fail' {
  if (score === null || score === undefined) return 'warn'
  if (score >= threshold) return 'pass'
  if (score >= Math.max(0, threshold - 15)) return 'warn'
  return 'fail'
}

/**
 * Compliance percentage AND evidence coverage, both explicit. They measure different things:
 * compliance is how well the requirements that could be decided are met; coverage is how much of
 * the applicable rule set the evidence actually allowed us to decide. One number could not say both.
 */
export function ScoreMeter({
  score,
  threshold,
  label = 'Compliance score',
  verdict,
  coverage,
  coverageFloor,
}: {
  score: number | null | undefined
  threshold: number
  label?: string
  verdict?: string
  coverage?: number | null
  coverageFloor?: number
}) {
  const tone = verdict === 'NON_COMPLIANT' ? 'fail' : scoreTone(score, threshold)
  const value = score === null || score === undefined ? null : score
  const floor = coverageFloor ?? 70
  const coverageLow = coverage !== null && coverage !== undefined && coverage < floor
  return (
    <div>
      <div className="muted" style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {label}
      </div>
      <div className={`score ${tone}`} style={{ marginTop: 4 }}>
        <span className="num" style={{ fontSize: 20 }}>{value === null ? '—' : `${value}%`}</span>
        <span className="track" aria-hidden="true">
          <i style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
        </span>
      </div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 4 }}>
        Threshold {threshold}% — at or above with no failed requirement, the AI may issue a
        pass-oriented verdict; below it the result is flagged for official finalization.
      </div>
      {coverage !== null && coverage !== undefined && (
        <div style={{ fontSize: 11.5, marginTop: 6 }}>
          <span className={coverageLow ? 'badge HIGH' : 'badge PASS'}>COVERAGE {coverage}%</span>{' '}
          <span className="muted">
            of the applicable requirements could be verified from the evidence (floor {floor}%).
            {coverageLow ? ' Below the floor, so the result needs official finalization.' : ''}
          </span>
        </div>
      )}
    </div>
  )
}

/**
 * The full scoring basis: the formula, what was counted with its weight, what could not be
 * verified, and what was legitimately excluded — with the reason for each. Read straight from the
 * backend's own scoring trace so the explanation cannot drift from the arithmetic.
 */
export function ScoreBasis({ scoring }: { scoring?: ScanScoring }) {
  if (!scoring || (!scoring.counted && !scoring.unverified && !scoring.excluded)) {
    return (
      <div className="muted">
        The scoring breakdown is available only to roles that can review compliance results.
      </div>
    )
  }
  const counted = scoring.counted ?? []
  const unverified = scoring.unverified ?? []
  const excluded = scoring.excluded ?? []
  return (
    <div className="basis">
      <div className="basis-math">
        <span>
          Compliance <b>{scoring.score ?? 0}%</b>
        </span>
        <span>
          Evidence coverage <b>{scoring.coverage ?? 0}%</b>
        </span>
        <span>
          Decided weight <b>{scoring.decided_weight ?? 0}</b> of <b>{scoring.applicable_weight ?? 0}</b>
        </span>
        <span>
          Thresholds <b>{scoring.threshold ?? 85}%</b> compliance · <b>{scoring.coverage_floor ?? 70}%</b> coverage
        </span>
      </div>
      {scoring.method && <div className="muted basis-method">{scoring.method}</div>}

      <div className="basis-group">
        <div className="basis-title">Counted in the score ({counted.length})</div>
        {counted.length === 0 ? (
          <div className="muted">
            No requirement could be decided from the evidence — nothing was counted, so the
            compliance percentage is 0 and the result cannot pass automatically.
          </div>
        ) : (
          counted.map((c) => (
            <div className="basis-row" key={`counted-${c.rule_number}-${c.status}`}>
              <span className="rule">Rule {c.rule_number}</span>
              <StatusChip value={c.status} />
              <span className="muted">
                weight {c.weight} × credit {c.credit} = <b>{c.contribution}</b>
              </span>
            </div>
          ))
        )}
      </div>

      {unverified.length > 0 && (
        <div className="basis-group">
          <div className="basis-title">Not verified — lowers coverage, not treated as a violation ({unverified.length})</div>
          {unverified.map((u) => (
            <div className="basis-row" key={`unverified-${u.rule_number}-${u.kind}`}>
              <span className="rule">Rule {u.rule_number}</span>
              <StatusChip value={u.kind === 'EVIDENCE_MISSING' ? 'NOT_APPLICABLE' : u.kind} />
              <span className="muted">
                {u.kind === 'EVIDENCE_MISSING'
                  ? 'no evidence established that this requirement applies — counted as unverified, never as an exemption'
                  : u.reason}
              </span>
            </div>
          ))}
        </div>
      )}

      {excluded.length > 0 && (
        <div className="basis-group">
          <div className="basis-title">Not counted ({excluded.length})</div>
          {excluded.map((e) => (
            <div className="basis-row" key={`excluded-${e.rule_number}-${e.kind}`}>
              <span className="rule">Rule {e.rule_number}</span>
              <span className="badge NOT_APPLICABLE">{e.kind}</span>
              <span className="muted">{e.reason}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * The two verdicts side by side. This is the component that guarantees a preliminary AI result can
 * never be read as a final official decision.
 */
export function VerdictSplit({
  aiVerdict,
  score,
  threshold,
  reviewStatus,
  officialDecision,
  finalizedBy,
  finalizedAt,
  remarks,
  summary,
  recommendedAction,
  statusMessage,
  coverage,
  coverageFloor,
  children,
}: {
  aiVerdict: string
  score: number | null
  threshold: number
  coverage?: number | null
  coverageFloor?: number
  reviewStatus: ReviewStatus
  officialDecision: string | null
  finalizedBy?: string
  finalizedAt?: string | null
  remarks?: string
  summary?: string
  recommendedAction?: string
  statusMessage?: string
  children?: React.ReactNode
}) {
  const finalized = reviewStatus === 'FINALIZED' && !!officialDecision
  return (
    <>
      <div className="verdict-split">
        <div className="verdict-box ai">
          <div className="k">AI preliminary verdict</div>
          <div className="v">
            <StatusBadge value={aiVerdict || 'NOT_EVALUATED'} />
          </div>
          <ScoreMeter
            score={score}
            threshold={threshold}
            verdict={aiVerdict}
            coverage={coverage}
            coverageFloor={coverageFloor}
          />
          {summary && <div className="n" style={{ marginTop: 8 }}>{summary}</div>}
        </div>
        <div className="verdict-box official">
          <div className="k">Official final decision</div>
          <div className="v">
            {finalized ? <StatusBadge value={officialDecision} /> : <span className="badge NOT_APPLICABLE">Not finalized</span>}
          </div>
          <div className="n">
            {finalized
              ? `Recorded by ${finalizedBy || 'an official'}${finalizedAt ? ` on ${finalizedAt.slice(0, 19).replace('T', ' ')}` : ''}.`
              : 'No authorized official has recorded a final decision for this result yet.'}
          </div>
          {finalized && remarks && (
            <div className="n" style={{ marginTop: 6 }}>
              <b>Remarks:</b> {remarks}
            </div>
          )}
          {!finalized && recommendedAction && (
            <div className="n" style={{ marginTop: 6 }}>
              <b>Next step:</b> {recommendedAction}
            </div>
          )}
        </div>
      </div>
      {finalized ? (
        <div className="final-note" role="status">
          <Icon name="badge-check" size={16} />
          <span>An authorized official has recorded the final decision. The AI verdict above remains the preliminary one that was reviewed.</span>
        </div>
      ) : (
        <div className="pending-note" role="status">
          <Icon name="clock" size={16} />
          <span>{statusMessage || 'This compliance result is pending finalization by the higher officials.'}</span>
        </div>
      )}
      {children}
    </>
  )
}

/** Every requirement check, with the measured values and legal reference behind the status. */
export function CheckList({ checks }: { checks: ComplianceCheck[] }) {
  const [showNotApplicable, setShowNotApplicable] = useState(false)
  const visible = useMemo(
    () => checks.filter((c) => showNotApplicable || c.status !== 'NOT_APPLICABLE' || c.check_type === 'manual_only'),
    [checks, showNotApplicable],
  )
  const hidden = checks.length - visible.length
  if (checks.length === 0) {
    return <div className="muted">No requirement checks have been recorded for this scan yet.</div>
  }
  return (
    <>
      {visible.map((c) => (
        <div className={`check-row ${c.status}`} key={`${c.rule_number}-${c.check_type}`}>
          <div className="head">
            <span className="rule">Rule {c.rule_number}</span>
            <StatusChip value={c.status} />
            {c.critical && <span className="badge HIGH" title="Critical requirement — carries double weight in the score">CRITICAL</span>}
            <span className="t">{c.title}</span>
          </div>
          <div className="why">{c.explanation}</div>
          {(c.required_value !== null && c.required_value !== undefined) ||
          (c.detected_value !== null && c.detected_value !== undefined) ? (
            <div className="measure">
              <span>
                Required: <b>{c.required_value ?? 'unavailable'}{c.required_value !== null && c.required_value !== undefined ? ' mm' : ''}</b>
              </span>
              <span>
                Detected: <b>{c.detected_value ?? 'unavailable'}{c.detected_value !== null && c.detected_value !== undefined ? ' mm' : ''}</b>
                {c.detected_field ? ` (${c.detected_field})` : ''}
              </span>
              <span>
                Satisfied: <b>{c.satisfied === true ? 'yes' : c.satisfied === false ? 'no' : 'not established'}</b>
              </span>
            </div>
          ) : null}
          {c.legal_reference && <div className="ref">Legal reference: {c.legal_reference}</div>}
        </div>
      ))}
      {hidden > 0 && (
        <button className="btn sm secondary" onClick={() => setShowNotApplicable(true)}>
          Show {hidden} not-applicable check(s)
        </button>
      )}
    </>
  )
}

type CalibrationResponse = { message: string; scan_id: number | null }

/**
 * Calibration control. Millimetres cannot be measured from pixels, so the app asks for a physical
 * scale and shows the honest state until one exists. The click-to-measure helper derives the pixel
 * span of a known printed length from two clicks on the package image.
 */
export function Calibrator({
  inspectionId,
  imageUrl,
  initial,
  onCalibrated,
}: {
  inspectionId: number
  imageUrl?: string
  initial?: FontSizeCompliance['calibration']
  onCalibrated?: (result: CalibrationResponse) => void
}) {
  const [mode, setMode] = useState<'reference' | 'px_per_mm' | 'panel'>('reference')
  const [referenceMm, setReferenceMm] = useState('')
  const [referencePx, setReferencePx] = useState('')
  const [pxPerMm, setPxPerMm] = useState('')
  const [panelW, setPanelW] = useState(initial?.panel_width_mm ? String(initial.panel_width_mm) : '')
  const [panelH, setPanelH] = useState(initial?.panel_height_mm ? String(initial.panel_height_mm) : '')
  const [form, setForm] = useState(initial?.packaging_form || 'normal')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')
  const [points, setPoints] = useState<{ x: number; y: number }[]>([])
  const imgRef = useRef<HTMLImageElement | null>(null)

  // Click-to-measure: two clicks give a pixel distance in the image's own resolution.
  const measuredPx = useMemo(() => {
    if (points.length < 2) return null
    const [a, b] = points
    return Math.round(Math.hypot(b.x - a.x, b.y - a.y))
  }, [points])

  function onImageClick(e: React.MouseEvent<HTMLImageElement>) {
    const img = imgRef.current
    if (!img) return
    const rect = img.getBoundingClientRect()
    const scaleX = img.naturalWidth / rect.width
    const scaleY = img.naturalHeight / rect.height
    const point = { x: Math.round((e.clientX - rect.left) * scaleX), y: Math.round((e.clientY - rect.top) * scaleY) }
    setPoints((prev) => (prev.length >= 2 ? [point] : [...prev, point]))
  }

  useEffect(() => {
    if (measuredPx && !referencePx) setReferencePx(String(measuredPx))
  }, [measuredPx, referencePx])

  async function submit() {
    setError('')
    setOk('')
    const body: Parameters<typeof api.calibrate>[1] = { packaging_form: form, note }
    if (mode === 'reference') {
      if (!referenceMm || !referencePx) {
        setError('Enter the known length in millimetres and its pixel span.')
        return
      }
      body.reference_mm = Number(referenceMm)
      body.reference_px = Number(referencePx)
    } else if (mode === 'px_per_mm') {
      if (!pxPerMm) {
        setError('Enter pixels per millimetre.')
        return
      }
      body.px_per_mm = Number(pxPerMm)
    } else {
      if (!panelW || !panelH) {
        setError('Enter the measured panel width and height in millimetres.')
        return
      }
      body.panel_width_mm = Number(panelW)
      body.panel_height_mm = Number(panelH)
    }
    setBusy(true)
    try {
      const res = await api.calibrate(inspectionId, body)
      setOk('Calibration saved — the rules were re-evaluated and the compliance score updated.')
      onCalibrated?.(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Calibration failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="tabs" role="tablist" aria-label="Calibration method">
        <button role="tab" aria-selected={mode === 'reference'} className={`tab ${mode === 'reference' ? 'on' : ''}`} onClick={() => setMode('reference')}>
          Known length → px/mm
        </button>
        <button role="tab" aria-selected={mode === 'px_per_mm'} className={`tab ${mode === 'px_per_mm' ? 'on' : ''}`} onClick={() => setMode('px_per_mm')}>
          Direct px/mm
        </button>
        <button role="tab" aria-selected={mode === 'panel'} className={`tab ${mode === 'panel' ? 'on' : ''}`} onClick={() => setMode('panel')}>
          Measured panel size
        </button>
      </div>

      {mode === 'reference' && (
        <>
          <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
            Measure any printed length you know exactly (a ruler marking, a currency note edge, a
            package dimension) and give its real length in millimetres.
            {imageUrl ? ' Click two points on the image to read the pixel span.' : ''}
          </p>
          {imageUrl && (
            <div className="measure-stage" style={{ marginBottom: 10 }}>
              <img
                ref={imgRef}
                src={imageUrl}
                alt="Package image used for scale measurement"
                onClick={onImageClick}
                style={{ cursor: 'crosshair', maxHeight: 260 }}
              />
              {points.length > 0 && (
                <svg aria-hidden="true">
                  {points.length === 2 && (
                    <line
                      x1={`${(points[0].x / (imgRef.current?.naturalWidth || 1)) * 100}%`}
                      y1={`${(points[0].y / (imgRef.current?.naturalHeight || 1)) * 100}%`}
                      x2={`${(points[1].x / (imgRef.current?.naturalWidth || 1)) * 100}%`}
                      y2={`${(points[1].y / (imgRef.current?.naturalHeight || 1)) * 100}%`}
                      stroke="#2563EB"
                      strokeWidth="2"
                    />
                  )}
                </svg>
              )}
            </div>
          )}
          <div className="split">
            <div className="field">
              <label htmlFor="cal-mm">Known length (mm)</label>
              <input id="cal-mm" inputMode="decimal" value={referenceMm} onChange={(e) => setReferenceMm(e.target.value)} placeholder="e.g. 50" />
            </div>
            <div className="field">
              <label htmlFor="cal-px">Pixel span {measuredPx ? `(measured ${measuredPx} px)` : ''}</label>
              <input id="cal-px" inputMode="numeric" value={referencePx} onChange={(e) => setReferencePx(e.target.value)} placeholder="e.g. 402" />
            </div>
          </div>
        </>
      )}

      {mode === 'px_per_mm' && (
        <div className="field">
          <label htmlFor="cal-ppm">Pixels per millimetre</label>
          <input id="cal-ppm" inputMode="decimal" value={pxPerMm} onChange={(e) => setPxPerMm(e.target.value)} placeholder="e.g. 8.4" />
        </div>
      )}

      {mode === 'panel' && (
        <>
          <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
            Rule 7(4) defines the principal display panel area — for a rectangular package it is the
            printed side's height × width. Enter what you measure with a rule.
          </p>
          <div className="split">
            <div className="field">
              <label htmlFor="cal-pw">Panel width (mm)</label>
              <input id="cal-pw" inputMode="decimal" value={panelW} onChange={(e) => setPanelW(e.target.value)} placeholder="e.g. 150" />
            </div>
            <div className="field">
              <label htmlFor="cal-ph">Panel height (mm)</label>
              <input id="cal-ph" inputMode="decimal" value={panelH} onChange={(e) => setPanelH(e.target.value)} placeholder="e.g. 100" />
            </div>
          </div>
        </>
      )}

      <div className="split">
        <div className="field">
          <label htmlFor="cal-form">Declaration surface</label>
          <select id="cal-form" value={form} onChange={(e) => setForm(e.target.value)}>
            <option value="normal">Printed on the label / surface (normal)</option>
            <option value="moulded">Blown, formed of molded into the container</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="cal-note">Note (optional)</label>
          <input id="cal-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. measured with steel rule, 12 Sep" />
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {ok && <div className="alert success">{ok}</div>}
      <button className="btn" onClick={submit} disabled={busy}>
        {busy ? 'Saving…' : 'Save calibration and re-evaluate'}
      </button>
      {points.length > 0 && (
        <button className="btn sm secondary" style={{ marginLeft: 8 }} onClick={() => { setPoints([]); setReferencePx('') }}>
          Clear measurement
        </button>
      )}
    </div>
  )
}

/** Rule 7 panel: status, required vs detected, the legal reference and the measurements used. */
export function FontSizePanel({
  data,
  imageUrl,
  onCalibrated,
}: {
  data: FontSizeCompliance
  imageUrl?: string
  onCalibrated?: (result: CalibrationResponse) => void
}) {
  const [showCalibrator, setShowCalibrator] = useState(!data.px_per_mm)
  const measurements = data.measurements || []
  return (
    <div className="card">
      <div className="flex" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <h3>Rule 7 — letter height (font size) compliance</h3>
        <StatusBadge value={data.status} />
      </div>
      <div className="kv" style={{ marginTop: 4 }}>
        <dt>Legal source</dt>
        <dd className="muted">{data.source_reference}</dd>
        <dt>Applied requirement</dt>
        <dd>
          {data.required_mm === null || data.required_mm === undefined
            ? 'Not established — see the reason below'
            : <>
                <b>{data.required_mm} mm</b> minimum
                {data.required_reference ? <> · {data.required_reference}</> : null}
              </>}
        </dd>
        <dt>Detected</dt>
        <dd>
          {data.detected_mm === null || data.detected_mm === undefined
            ? 'Not measured'
            : <>
                <b>{data.detected_mm} mm</b> (smallest measured declaration
                {data.detected_field ? `: ${data.detected_field}` : ''}
                {data.detected_text ? ` — “${data.detected_text}”` : ''})
              </>}
        </dd>
        <dt>Requirement satisfied</dt>
        <dd>
          {data.satisfied === true ? <span className="badge COMPLIANT">Satisfied on the measured evidence</span>
            : data.satisfied === false ? <span className="badge NON_COMPLIANT">Not satisfied — evidence-backed shortfall</span>
            : <span className="badge UNCERTAIN">Not established</span>}
        </dd>
        <dt>Scale in use</dt>
        <dd>
          {data.px_per_mm
            ? `${data.px_per_mm.toFixed(2)} px/mm · ${data.px_per_mm_source}${data.px_per_mm_note ? ` (${data.px_per_mm_note})` : ''}`
            : <span className="muted">No calibration recorded — millimetres cannot be measured from pixels alone.</span>}
        </dd>
        <dt>Panel area</dt>
        <dd>
          {data.panel_area_cm2
            ? `${data.panel_area_cm2} cm² · ${data.panel_area_source}${data.panel_area_note ? ` (${data.panel_area_note})` : ''}`
            : <span className="muted">Not available — enter the measured panel size below.</span>}
        </dd>
        <dt>Method</dt>
        <dd className="muted">{data.method}</dd>
        {data.min_width_ratio !== null && data.min_width_ratio !== undefined && (
          <>
            <dt>Width ratio (Rule 7(3))</dt>
            <dd>
              narrowest {data.min_width_ratio} — {data.width_ratio_ok ? 'meets' : 'below'} the one-third minimum (estimated from the box width)
            </dd>
          </>
        )}
      </div>

      <div className="alert info" style={{ marginTop: 12 }}>{data.reason}</div>
      {data.exempt_note && <p className="muted" style={{ fontSize: 12, lineHeight: 1.6 }}>{data.exempt_note}</p>}

      {measurements.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: 10 }}>
          <table className="measure-table">
            <thead>
              <tr>
                <th>Declaration</th><th>Text read</th><th>Letter height</th><th>Line box height</th><th>Width ratio</th>
              </tr>
            </thead>
            <tbody>
              {measurements.map((m) => (
                <tr key={`${m.field_name}-${m.bbox_px?.join('-')}`}>
                  <td>{m.field_name}{m.state !== 'DETECTED' ? ` · ${m.state}` : ''}</td>
                  <td>{m.text || '—'}</td>
                  <td className="num">{m.letter_height_mm} mm</td>
                  <td className="num">{m.line_height_mm} mm</td>
                  <td className="num">{m.width_ratio ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.smallest_line && (
        <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          Smallest text read anywhere on the package: {data.smallest_line.letter_height_mm} mm ——
          informational only; a comparison is only made against regulated declarations.
        </p>
      )}

      <div className="flex" style={{ marginTop: 12 }}>
        <button className="btn sm secondary" onClick={() => setShowCalibrator((v) => !v)}>
          {showCalibrator ? 'Hide calibration' : data.px_per_mm ? 'Update calibration' : 'Record a calibration'}
        </button>
      </div>
      {showCalibrator && (
        <div style={{ marginTop: 14 }}>
          <Calibrator inspectionId={data.inspection_id} imageUrl={imageUrl} initial={data.calibration} onCalibrated={onCalibrated} />
        </div>
      )}

      <details className="legal-table" style={{ marginTop: 14 }}>
        <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
          Rule 7 minimum-height tables in force
        </summary>
        {data.tables.map((t) => (
          <div key={t.id} style={{ marginTop: 10 }}>
            <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>{t.id} — {t.title}</div>
            <div style={{ overflowX: 'auto' }}>
              <table className="measure-table">
                <thead>
                  <tr><th>#</th><th>Area of principal display panel</th><th>Normal (mm)</th><th>Blown/formed/molded (mm)</th></tr>
                </thead>
                <tbody>
                  {t.rows.map((row) => (
                    <tr key={row.serial}>
                      <td>{row.serial}</td>
                      <td>{row.label}</td>
                      <td className="num">{row.normal_mm}</td>
                      <td className="num">{row.moulded_mm}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </details>
    </div>
  )
}
