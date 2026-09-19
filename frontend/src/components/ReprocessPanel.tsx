// Reprocessing / migration UI.
//
// Two things live here, both grounded in real API data:
//  * ReprocessPanel — one inspection: which engine produced the ACTIVE result, a controlled way to
//    regenerate it, and the BEFORE / AFTER comparison of every regeneration it has been through.
//  * MigrationCard  — the administrative bulk run over records still on an older engine, with
//    counts before it starts, live progress, and per-record outcomes.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../services/api'
import type { InspectionRevision, MigrationJob, ReprocessDiff, ReprocessPreview, ReprocessResult } from '../types'
import { Card } from './Badges'

function pct(v: number | null | undefined): string {
  return v == null ? '—' : `${Math.round(v * 10) / 10}%`
}

export function DiffView({ diff }: { diff: ReprocessDiff }) {
  const fields = diff.fields || []
  const rules = diff.rules || []
  if (!diff.changed?.any) {
    return (
      <div className="muted" style={{ fontSize: 12.5 }}>
        No material change — every declaration, rule result, score and verdict is identical to the
        previous analysis.
      </div>
    )
  }
  return (
    <div className="diff">
      {diff.headline.length > 0 && (
        <ul className="diff-headline">
          {diff.headline.map((h, i) => (
            <li key={i}>{h}</li>
          ))}
        </ul>
      )}

      <div className="diff-chips">
        <span className={`badge ${diff.score.before === diff.score.after ? '' : 'LOW'}`}>
          compliance {pct(diff.score.before)} → {pct(diff.score.after)}
        </span>
        <span className={`badge ${diff.coverage.before === diff.coverage.after ? '' : 'LOW'}`}>
          coverage {pct(diff.coverage.before)} → {pct(diff.coverage.after)}
        </span>
        <span className="badge">
          AI verdict {diff.verdict.before || '—'} → {diff.verdict.after || '—'}
        </span>
        <span className="badge">
          review status {diff.review_status.before || '—'} → {diff.review_status.after || '—'}
        </span>
        <span className="badge">
          unresolved {diff.unresolved.before} → {diff.unresolved.after}
        </span>
        {diff.official_decision.before || diff.official_decision.after ? (
          <span className="badge HIGH">
            official decision {diff.official_decision.before || '—'} → {diff.official_decision.after || '—'}
          </span>
        ) : null}
      </div>

      {fields.length > 0 && (
        <table className="tbl" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>Declaration</th>
              <th>Before</th>
              <th>After</th>
              <th>Change</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => (
              <tr key={f.field_name}>
                <td>{f.label}</td>
                <td className="muted">
                  {f.before_value || f.before_state || '—'}
                </td>
                <td>
                  {f.after_value || f.after_state || '—'}
                  {f.manually_corrected && <span className="muted"> (human-verified)</span>}
                </td>
                <td>
                  <span
                    className={`badge ${
                      f.kind === 'newly_detected' ? 'PASS' : f.kind === 'no_longer_detected' ? 'FAIL' : ''
                    }`}
                  >
                    {f.kind.replace(/_/g, ' ')}
                  </span>
                  {f.after_uncertainty_reason && (
                    <div className="muted" style={{ fontSize: 11.5 }} title={f.after_uncertainty_reason}>
                      {f.after_uncertainty_reason.slice(0, 80)}
                      {f.after_uncertainty_reason.length > 80 ? '…' : ''}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {rules.length > 0 && (
        <table className="tbl" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>Rule</th>
              <th>Before</th>
              <th>After</th>
              <th>Observed after</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.rule_number}>
                <td>
                  {r.rule_number} {r.title ? `· ${r.title}` : ''}
                  {r.critical && <span className="muted"> · critical</span>}
                </td>
                <td>
                  <span className="badge">{r.before_status}</span>
                </td>
                <td>
                  <span className="badge">{r.after_status}</span>
                </td>
                <td className="muted">{r.after_observed || r.before_observed || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {(diff.violations.newly_flagged.length > 0 || diff.violations.no_longer_flagged.length > 0) && (
        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          {diff.violations.newly_flagged.length > 0 && (
            <div>Now flagged: {diff.violations.newly_flagged.map((r) => `Rule ${r}`).join(', ')}</div>
          )}
          {diff.violations.no_longer_flagged.length > 0 && (
            <div>No longer flagged: {diff.violations.no_longer_flagged.map((r) => `Rule ${r}`).join(', ')}</div>
          )}
        </div>
      )}
    </div>
  )
}

export function ReprocessPanel({
  inspectionId,
  canReprocess,
  provenance,
  onReprocessed,
}: {
  inspectionId: number
  canReprocess: boolean
  provenance: {
    engine_version: string
    current_engine: string
    up_to_date: boolean
    rule_fingerprint: string
    rule_count: number
    reprocess_count: number
    last_reprocessed_at: string
  }
  onReprocessed?: () => void
}) {
  const [revisions, setRevisions] = useState<InspectionRevision[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reason, setReason] = useState('')
  const [refreshOcr, setRefreshOcr] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [result, setResult] = useState<ReprocessResult | null>(null)
  const [selected, setSelected] = useState<number | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    api
      .inspectionRevisions(inspectionId)
      .then((res) => {
        setRevisions(res.revisions)
        setError('')
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load revision history'))
      .finally(() => setLoading(false))
  }, [inspectionId])

  useEffect(() => {
    load()
  }, [load])

  const shown: ReprocessDiff | null = useMemo(() => {
    if (result) return result.changes
    const rev = revisions.find((r) => r.revision_no === selected) || revisions[0]
    return rev ? rev.changes : null
  }, [result, revisions, selected])

  async function run() {
    setBusy(true)
    setError('')
    try {
      const res = await api.reprocessInspection(inspectionId, reason || 'manual reprocess', refreshOcr)
      setResult(res)
      setConfirming(false)
      setReason('')
      setRefreshOcr(false)
      load()
      onReprocessed?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reprocessing failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Analysis provenance & reprocessing">
      <dl className="kv">
        <dt>Active result engine</dt>
        <dd>
          {provenance.engine_version || '— (analysed before engine stamping)'}{' '}
          {provenance.up_to_date ? (
            <span className="badge PASS">current engine</span>
          ) : (
            <span className="badge LOW">older engine — reprocess available</span>
          )}
        </dd>
        <dt>Current engine</dt>
        <dd>
          {provenance.current_engine} · rule set {provenance.rule_fingerprint} ({provenance.rule_count} versions)
        </dd>
        <dt>Reprocessed</dt>
        <dd>
          {provenance.reprocess_count} time(s)
          {provenance.last_reprocessed_at
            ? ` · last ${provenance.last_reprocessed_at.slice(0, 19).replace('T', ' ')}`
            : ''}
        </dd>
      </dl>

      {canReprocess ? (
        <div className="mt">
          {!confirming ? (
            <div className="flex" style={{ alignItems: 'flex-end', gap: 8, flexWrap: 'wrap' }}>
              <div className="field" style={{ flex: 1, minWidth: 220, marginBottom: 0 }}>
                <label htmlFor="reprocess-reason">Reason (recorded in the audit trail)</label>
                <input
                  id="reprocess-reason"
                  value={reason}
                  maxLength={300}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="e.g. re-run after extraction and classification fixes"
                />
              </div>
              <button className="btn sm" disabled={busy} onClick={() => setConfirming(true)}>
                Reprocess inspection
              </button>
            </div>
          ) : (
            <div className="alert warn" role="alert">
              <b>Regenerate this inspection&apos;s analysis?</b>
              <div style={{ fontSize: 12.5, marginTop: 4 }}>
                The pipeline re-reads the stored images and evidence, re-evaluates every rule and
                recalculates the score. The inspection record is updated in place — no duplicate
                record is created — and the previous result is preserved on a revision row.
                Human corrections and any recorded official decision are kept.
              </div>
              <label className="flex" style={{ gap: 6, marginTop: 8, fontSize: 12.5 }}>
                <input type="checkbox" checked={refreshOcr} onChange={(e) => setRefreshOcr(e.target.checked)} />
                Also re-read the package images with OCR (slower; normally unnecessary)
              </label>
              <div className="flex" style={{ gap: 8, marginTop: 10 }}>
                <button className="btn sm" disabled={busy} onClick={run}>
                  {busy ? 'Reprocessing…' : 'Confirm reprocess'}
                </button>
                <button className="btn sm secondary" disabled={busy} onClick={() => setConfirming(false)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      ) : (
        <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
          Reprocessing requires the pipeline capability (inspector, enforcement officer or
          administrator). You can still review each regeneration and its before/after comparison below.
        </p>
      )}

      {error && <div className="alert error mt">{error}</div>}

      {result && (
        <div className="alert success mt" role="status">
          Reprocessed {result.inspection_number} under engine {result.engine_version}: {result.changes.changed.fields_changed}{' '}
          declaration(s) and {result.changes.changed.rules_changed} rule result(s) changed.
          {!result.ok && <div className="muted">Engine error recorded: {result.error}</div>}
        </div>
      )}

      <div className="mt">
        <b style={{ fontSize: 13 }}>Reprocessing history</b>
        {loading ? (
          <div className="muted" style={{ fontSize: 12.5 }}>Loading…</div>
        ) : revisions.length === 0 ? (
          <div className="muted" style={{ fontSize: 12.5 }}>
            This inspection has not been reprocessed. Its active result was produced by the original
            pipeline run.
          </div>
        ) : (
          <>
            <div className="flex" style={{ gap: 6, flexWrap: 'wrap', margin: '8px 0' }}>
              {revisions.map((r) => (
                <button
                  key={r.id}
                  className={`btn sm ${(!result && (selected ?? revisions[0].revision_no) === r.revision_no) ? '' : 'secondary'}`}
                  onClick={() => {
                    setResult(null)
                    setSelected(r.revision_no)
                  }}
                  title={r.reason || 'no reason recorded'}
                >
                  #{r.revision_no} · {r.created_at.slice(0, 16).replace('T', ' ')} · {r.actor || 'system'}
                </button>
              ))}
            </div>
            {(() => {
              const rev = revisions.find((r) => r.revision_no === (selected ?? revisions[0].revision_no)) || revisions[0]
              return (
                <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
                  {rev.kind} · engine {rev.engine_version} · rules {rev.rule_fingerprint} · actor{' '}
                  {rev.actor || 'system'} · reason: {rev.reason || 'not recorded'}
                </div>
              )
            })()}
            {shown && <DiffView diff={shown} />}
          </>
        )}
      </div>
    </Card>
  )
}

export function MigrationCard() {
  const [preview, setPreview] = useState<ReprocessPreview | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [job, setJob] = useState<MigrationJob | null>(null)
  const [reason, setReason] = useState('engine upgrade: evidence-precision pipeline')
  const [confirming, setConfirming] = useState(false)
  const [starting, setStarting] = useState(false)
  const timer = useRef<number | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    api
      .reprocessPreview()
      .then((p) => {
        setPreview(p)
        setError('')
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not read the migration preview'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // Reconnect to a run already in flight (e.g. after a page reload) and then poll it.
  useEffect(() => {
    api
      .reprocessJobs()
      .then((res) => {
        const active = res.jobs.find((j) => j.state === 'QUEUED' || j.state === 'RUNNING')
        if (active) setJob(active)
      })
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!job || job.state === 'DONE' || job.state === 'FAILED') {
      if (timer.current) window.clearInterval(timer.current)
      timer.current = null
      return
    }
    timer.current = window.setInterval(() => {
      api
        .reprocessJob(job.id)
        .then(setJob)
        .catch(() => undefined)
    }, 1500)
    return () => {
      if (timer.current) window.clearInterval(timer.current)
      timer.current = null
    }
  }, [job])

  async function start() {
    setStarting(true)
    setError('')
    try {
      const started = await api.startReprocessBatch({ reason, limit: preview?.max_batch ?? 200 })
      setJob(started)
      setConfirming(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start the migration')
    } finally {
      setStarting(false)
    }
  }

  if (loading) {
    return (
      <Card title="Reprocessing & migration">
        <div className="muted" style={{ fontSize: 12.5 }}>Reading the repository inventory…</div>
      </Card>
    )
  }

  const progress = job && job.total > 0 ? Math.round((job.done / job.total) * 100) : 0
  const running = job?.state === 'QUEUED' || job?.state === 'RUNNING'

  return (
    <Card title="Reprocessing & migration">
      {error && <div className="alert error">{error}</div>}
      {preview && (
        <>
          <dl className="kv">
            <dt>Engine</dt>
            <dd>
              {preview.engine_version} · rule set {preview.rule_fingerprint} ({preview.rule_count} versions)
            </dd>
            <dt>Inspections that can be reprocessed</dt>
            <dd>{preview.reprocessable_total}</dd>
            <dt>Still on an older engine</dt>
            <dd>
              <b>{preview.legacy_count}</b>
              {preview.legacy_count > 0 && <span className="muted"> — correct existing records are never churned</span>}
            </dd>
            <dt>Already current</dt>
            <dd>{preview.current_count}</dd>
          </dl>

          {preview.legacy_count > 0 && !running && (
            <>
              {!confirming ? (
                <div className="flex" style={{ alignItems: 'flex-end', gap: 8, flexWrap: 'wrap' }}>
                  <div className="field" style={{ flex: 1, minWidth: 220, marginBottom: 0 }}>
                    <label htmlFor="batch-reason">Migration reason (recorded per record)</label>
                    <input id="batch-reason" value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} />
                  </div>
                  <button className="btn sm" disabled={starting} onClick={() => setConfirming(true)}>
                    Reprocess {preview.legacy_count} legacy inspection(s)
                  </button>
                </div>
              ) : (
                <div className="alert warn">
                  <b>Recalculate {preview.legacy_count} existing inspection(s)?</b>
                  <div style={{ fontSize: 12.5, marginTop: 4 }}>
                    Each record is updated in place (never duplicated). Its previous result is kept on
                    a revision row, human corrections and recorded official decisions are preserved,
                    and every change is written to the audit trail. The run continues in the
                    background — this screen shows live progress.
                  </div>
                  <div className="flex" style={{ gap: 8, marginTop: 10 }}>
                    <button className="btn sm" disabled={starting} onClick={start}>
                      {starting ? 'Starting…' : 'Confirm migration'}
                    </button>
                    <button className="btn sm secondary" disabled={starting} onClick={() => setConfirming(false)}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </>
          )}

          {preview.legacy_count === 0 && !running && (
            <div className="alert success">Every reprocessable inspection is on the current engine.</div>
          )}

          {preview.legacy_count > 0 && !confirming && !running && (
            <details className="mt">
              <summary style={{ cursor: 'pointer', fontSize: 12.5 }}>
                Show the {preview.items.length} oldest record(s) that will be recalculated
              </summary>
              <table className="tbl" style={{ marginTop: 8 }}>
                <thead>
                  <tr>
                    <th>Inspection</th>
                    <th>Product</th>
                    <th>Engine</th>
                    <th>Score</th>
                    <th>Verdict</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.items.map((it) => (
                    <tr key={it.inspection_id}>
                      <td>
                        <a href={`/inspections/${it.inspection_id}`}>{it.inspection_number}</a>
                      </td>
                      <td className="muted">
                        {it.brand} {it.product}
                      </td>
                      <td className="muted">{it.pipeline_version || 'unstamped'}</td>
                      <td>{pct(it.score)}</td>
                      <td>
                        <span className="badge">{it.verdict || '—'}</span>
                      </td>
                      <td className="muted">
                        {it.review_status}
                        {it.official_decision ? ` · ${it.official_decision}` : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      )}

      {job && (
        <div className="mt">
          <div className="flex">
            <b style={{ fontSize: 13 }}>Migration {job.id}</b>
            <span className="right muted">
              {job.state} · {job.done}/{job.total || '—'} · {job.succeeded} succeeded · {job.failed} failed
            </span>
          </div>
          <div className="progress" aria-hidden="true">
            <div className="progress-fill" style={{ width: `${progress}%` }} />
          </div>
          {job.current && running && <div className="muted" style={{ fontSize: 12 }}>Processing {job.current}…</div>}
          {job.error && <div className="alert error">{job.error}</div>}
          {job.finished_at && (
            <div className="muted" style={{ fontSize: 12 }}>
              Finished {job.finished_at.slice(0, 19).replace('T', ' ')} · actor {job.actor} · reason: {job.reason}
            </div>
          )}
          {job.results.length > 0 && (
            <details className="mt" open={job.state === 'DONE'}>
              <summary style={{ cursor: 'pointer', fontSize: 12.5 }}>Per-record outcomes</summary>
              <table className="tbl" style={{ marginTop: 8 }}>
                <thead>
                  <tr>
                    <th>Inspection</th>
                    <th>Result</th>
                    <th>Score</th>
                    <th>Verdict</th>
                    <th>Change</th>
                  </tr>
                </thead>
                <tbody>
                  {job.results.map((r) => (
                    <tr key={`${r.inspection_id}-${r.inspection_number}`}>
                      <td>
                        <a href={`/inspections/${r.inspection_id}`}>{r.inspection_number || `#${r.inspection_id}`}</a>
                      </td>
                      <td>
                        <span className={`badge ${r.ok ? 'PASS' : 'FAIL'}`}>{r.ok ? 'reprocessed' : 'failed'}</span>
                        {r.error && <div className="muted" style={{ fontSize: 11 }}>{r.error}</div>}
                      </td>
                      <td>
                        {pct(r.score_before)} → {pct(r.score_after)}
                      </td>
                      <td className="muted">
                        {r.verdict_before || '—'} → {r.verdict_after || '—'}
                      </td>
                      <td className="muted" style={{ fontSize: 12 }}>
                        {r.headline.length > 0 ? r.headline.join('; ') : 'no material change'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
          <div className="mt">
            <button className="btn sm secondary" onClick={load}>Refresh inventory</button>
          </div>
        </div>
      )}
    </Card>
  )
}
