import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, hasPermission } from '../services/api'
import { Card } from '../components/Badges'
import {
  Confidence,
  EmptyState,
  SectionHead,
  Skeleton,
  StatusBadge,
  useToast,
} from '../components/ui'
import EvidenceViewer from '../components/EvidenceViewer'
import Modal from '../components/Modal'
import { FontSizePanel, VerdictSplit } from '../components/CompliancePanels'
import VisionPanel from '../components/VisionPanel'
import { ReprocessPanel } from '../components/ReprocessPanel'
import type { EvidenceOut, FontSizeCompliance, InspectionDetail as Detail, ScanDetailPayload } from '../types'

type ViolationAction = 'CONFIRM' | 'DISMISS' | 'RESOLVE'

/** The retained crops that support one rule conclusion (empty when none were anchored). */
function ruleEvidenceFor(detail: Detail, ruleId: number) {
  return detail.rule_evidence?.[String(ruleId)] || []
}

type DialogSpec =
  | { kind: 'confirm_absent'; fieldName: string }
  | { kind: 'edit'; fieldName: string; currentValue: string }
  | { kind: 'add' }
  | { kind: 'finalize'; decision: 'COMPLIANT' | 'NON_COMPLIANT' }
  | { kind: 'violation'; violationId: number; action: ViolationAction; ruleNumber: string; title: string }

export default function InspectionDetail() {
  const { id } = useParams()
  const [d, setD] = useState<Detail | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [dialog, setDialog] = useState<DialogSpec | null>(null)
  const [dialogValue, setDialogValue] = useState('')
  const [dialogReason, setDialogReason] = useState('')
  // Rule 7 measurement detail is fetched separately because it also carries the legal tables and
  // the calibration state used to produce it.
  const [fontSize, setFontSize] = useState<FontSizeCompliance | null>(null)
  const [finDecision, setFinDecision] = useState('COMPLIANT')
  const [finRemarks, setFinRemarks] = useState('')
  // "View evidence": the exact declaration region an assertion came from, opened from a field row
  // or from a rule conclusion.
  const [evidenceFocus, setEvidenceFocus] = useState<{ field: string; evidenceId?: number } | null>(null)
  const { show, node: toastNode } = useToast()

  const load = useCallback(() => {
    api
      .get<Detail>(`/inspections/${id}`)
      .then((detail) => {
        setD(detail)
        setFinRemarks(detail.compliance_review?.remarks || '')
        if (detail.official_decision) setFinDecision(detail.official_decision)
        return api
          .fontSizeCompliance(detail.id)
          .then(setFontSize)
          .catch(() => setFontSize(null))
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
  }, [id])

  useEffect(() => { load() }, [load])

  async function act(fn: () => Promise<unknown>, successMsg?: string) {
    setBusy(true)
    setError('')
    try {
      await fn()
      if (successMsg) show(successMsg)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Action failed')
    } finally {
      setBusy(false)
    }
  }

  function openConfirmAbsent(fieldName: string) {
    setDialogValue('')
    setDialogReason('')
    setDialog({ kind: 'confirm_absent', fieldName })
  }

  function openEdit(fieldName: string, currentValue: string) {
    setDialogValue(currentValue)
    setDialogReason('')
    setDialog({ kind: 'edit', fieldName, currentValue })
  }

  function openAdd() {
    setDialogValue('')
    setDialogReason('')
    setDialog({ kind: 'add' })
  }

  function openViolation(violationId: number, action: ViolationAction, ruleNumber: string, title: string) {
    setDialogValue('')
    setDialogReason('')
    setDialog({ kind: 'violation', violationId, action, ruleNumber, title })
  }

  async function submitDialog() {
    if (!dialog) return
    if (dialog.kind === 'confirm_absent') {
      const fieldName = dialog.fieldName
      const reason = dialogReason.trim()
      setDialog(null)
      if (!reason) return
      await act(
        () =>
          api.post(`/inspections/${id}/review/field`, {
            action: 'CONFIRM_ABSENT',
            field_name: fieldName,
            reason,
          }),
        'Absence recorded — rules re-evaluated',
      )
    } else if (dialog.kind === 'edit') {
      const fieldName = dialog.fieldName
      const corrected = dialogValue.trim()
      setDialog(null)
      if (!corrected || corrected === dialog.currentValue) return
      await act(
        () =>
          api.post(`/inspections/${id}/review/field`, {
            action: 'EDIT_FIELD',
            field_name: fieldName,
            corrected_value: corrected,
            reason: dialogReason.trim() || 'manual correction during review',
          }),
        'Field corrected — audit trail updated',
      )
    } else if (dialog.kind === 'add') {
      const name = dialogValue.trim().split('\n')[0] || ''
      setDialog(null)
      if (!name || !dialogValue.trim()) return
      await act(
        () =>
          api.post(`/inspections/${id}/review/field`, {
            action: 'ADD_FIELD',
            field_name: name.toLowerCase(),
            corrected_value: dialogValue.trim().split('\n').slice(1).join(' ').trim(),
            reason: dialogReason.trim() || 'field added by inspector',
          }),
        'Field added — audit trail updated',
      )
    } else if (dialog.kind === 'violation') {
      const { violationId, action } = dialog
      const reason = dialogReason.trim()
      setDialog(null)
      if (action !== 'CONFIRM' && !reason) return
      await act(
        () =>
          api.post(`/inspections/${id}/review/violation/${violationId}`, {
            action,
            reason: reason || 'reviewer confirmed the finding',
          }),
        `Violation ${action.toLowerCase()}ed — decision re-evaluated`,
      )
    } else {
      const decision = dialog.decision
      const reason = dialogReason.trim()
      setDialog(null)
      if (!reason) return
      await act(
        () => api.post(`/inspections/${id}/review/final`, { decision, reason }),
        `Decision finalized: ${decision.replace(/_/g, ' ')}`,
      )
    }
  }

  function generateReport() {
    void act(async () => {
      await api.post(`/reports/generate/${id}`)
    }, 'PDF report generated')
  }

  /** Official finalization from the inspection page (the same record the repository shows). */
  async function finalizeReview(scanId: number) {
    if (!finRemarks.trim()) {
      setError('A remark is required: it is recorded as the reason for the official decision.')
      return
    }
    await act(
      () => api.repositoryFinalize(scanId, finDecision, finRemarks),
      `Official decision recorded: ${finDecision.replace(/_/g, ' ')}`,
    )
  }

  async function saveRemarks(scanId: number) {
    await act(() => api.repositoryRemarks(scanId, finRemarks), 'Remarks saved')
  }

  if (error && !d) return <div className="alert error">{error}</div>
  if (!d)
    return (
      <>
        <div className="page-title">Inspection {id}</div>
        <Card><Skeleton lines={6} /></Card>
      </>
    )

  // Capability comes from the centrally-defined permissions the server returned for this account.
  const canEdit = hasPermission('inspections.review')
  // The repository's view of this inspection (score, checks, review status, official decision).
  const review = (d.compliance_review && 'id' in d.compliance_review
    ? d.compliance_review
    : null) as ScanDetailPayload | null
  const evidenceByField = new Map<string, EvidenceOut[]>()
  for (const ev of d.evidence) {
    const list = evidenceByField.get(ev.field_name) || []
    list.push(ev)
    evidenceByField.set(ev.field_name, list)
  }
  const conflicting = d.fields.filter((f) => f.conflict_status === 'CONFLICTING')
  const uncertain = d.fields.filter((f) => f.state === 'UNCERTAIN' || f.state === 'MISSING')
  const decisionClass = (d.final_decision || 'pending').toLowerCase()
  const decisionWhy =
    d.final_decision === 'NEEDS_MANUAL_REVIEW'
      ? `${uncertain.length} declaration(s) could not be verified from the supplied images and require human confirmation.`
      : d.final_decision === 'NON_COMPLIANT'
        ? d.summary
        : d.final_decision === 'COMPLIANT'
          ? 'All applicable requirements passed with sufficient evidence.'
          : d.summary

  return (
    <>
      <div className="page-title">Inspection {d.inspection_number}</div>
      <div className="page-sub">
        Created {d.created_at.slice(0, 19).replace('T', ' ')} by {d.inspector || '—'} · {d.images.length} image(s)
      </div>

      {error && <div className="alert error">{error}</div>}

      {/* The automated verdict and the official final decision are presented as two distinct
          things — a pending result is never shown as approved or rejected. */}
      {review ? (
        <VerdictSplit
          aiVerdict={review.ai_verdict}
          score={review.compliance_score}
          threshold={review.threshold}
          reviewStatus={review.review_status}
          officialDecision={review.official_decision}
          finalizedBy={review.finalized_by}
          finalizedAt={review.finalized_at}
          remarks={review.remarks}
          summary={review.ai_summary}
          recommendedAction={review.recommended_action}
          statusMessage={review.status_message}
        />
      ) : (
        <div className={`decision-banner ${decisionClass}`}>
          <div>
            <div className="dlabel">Automated verdict (preliminary)</div>
            <div className="dvalue">
              <StatusBadge value={d.final_decision ?? 'NOT_APPLICABLE'} />
            </div>
            <div className="dwhy">{decisionWhy}</div>
          </div>
        </div>
      )}

      {review && review.can_finalize && (
        <Card title="Official finalization">
          <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
            The AI verdict above stays preliminary. Recording the official decision here also updates the
            product's repository entry — one record, one final decision.
          </p>
          {review.official_decision && (
            <div className="alert info">
              Current official decision: <b>{review.official_decision}</b>
              {review.finalized_by ? ` — recorded by ${review.finalized_by}` : ''}
              {review.finalized_at ? ` on ${review.finalized_at.slice(0, 19).replace('T', ' ')}` : ''}
            </div>
          )}
          <div className="split">
            <div className="field">
              <label htmlFor="insp-fin-decision">Official decision</label>
              <select id="insp-fin-decision" value={finDecision} onChange={(e) => setFinDecision(e.target.value)}>
                <option value="COMPLIANT">Compliant</option>
                <option value="NON_COMPLIANT">Non-compliant</option>
                <option value="NEEDS_MANUAL_REVIEW">Needs further review</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="insp-fin-remarks">Remarks (required to finalize)</label>
              <textarea
                id="insp-fin-remarks"
                rows={2}
                value={finRemarks}
                onChange={(e) => setFinRemarks(e.target.value)}
                placeholder="e.g. Verified against the physical package; batch and MRP match the label."
              />
            </div>
          </div>
          <div className="flex">
            <button className="btn" onClick={() => void finalizeReview(review.id)} disabled={busy || !finRemarks.trim()}>
              Record official decision
            </button>
            <button className="btn sm secondary" onClick={() => void saveRemarks(review.id)} disabled={busy || !finRemarks.trim()}>
              Save remarks only
            </button>
          </div>
        </Card>
      )}

      {canEdit && (
        <Card title="Reporting and export">
          <div className="flex">
            <button className="btn sm" onClick={generateReport} disabled={busy}>Generate PDF report</button>
            {/* Print-ready HTML report: the same stored rows in a wrapping table layout. */}
            <a className="btn sm secondary" href={`/api/reports/html/${d.id}`} target="_blank" rel="noreferrer">Print report</a>
            <a className="btn sm secondary" href={`/api/inspections/${d.id}/export?format=csv`}>Export CSV</a>
            <a className="btn sm secondary" href={`/api/inspections/${d.id}/export`}>Export JSON</a>
          </div>
        </Card>
      )}

      {/* 1. package evidence */}
      <SectionHead
        no="1"
        title="Package evidence"
        hint="originals preserved — select a declaration to highlight its source region"
      />
      <Card>
        <EvidenceViewer images={d.images} fields={d.fields} evidence={d.evidence} />
      </Card>

      {/* 2. extracted declarations */}
      <SectionHead
        no="2"
        title="Extracted declarations"
        hint="extraction ≠ validation ≠ legal decision"
      />
      <Card>
        <div className="alert warn" style={{ marginBottom: 12 }}>
          MISSING means “not found in the supplied images” — this does <b>not</b> imply the declaration is
          absent from the package. Only a human-confirmed absence (audited) can establish non-compliance.
        </div>
        {d.fields.length === 0 ? (
          <EmptyState
            headline="No declarations extracted"
            how="The supplied images produced no reliable candidates. Upload clearer or additional sides."
            action={canEdit ? <button className="btn sm secondary" onClick={openAdd}>Add field manually</button> : undefined}
          />
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Field</th><th>Value</th><th>Status</th><th>Confidence</th><th>Source</th><th>Evidence</th>
                {canEdit && <th></th>}
              </tr>
            </thead>
            <tbody>
              {d.fields.map((f) => {
                const evs = evidenceByField.get(f.field_name) || []
                const hasValue = !!f.display_value?.trim()
                return (
                  <tr key={f.id}>
                    <td><b>{f.field_name.replace(/_/g, ' ')}</b></td>
                    <td>
                      {hasValue ? f.display_value : '—'}
                      {f.conflict_status === 'CONFLICTING' && (
                        <div style={{ color: 'var(--amber)', fontSize: 12 }}>
                          ⚠ conflicting candidates retained — both shown in section 2a
                        </div>
                      )}
                      {f.manually_corrected && (
                        <div className="muted">human-verified (original AI value preserved in audit)</div>
                      )}
                      {f.uncertainty_reason && !hasValue && (
                        <div className="muted" title={f.uncertainty_reason}>
                          {f.uncertainty_reason.length > 90
                            ? f.uncertainty_reason.slice(0, 90).trimEnd() + '…'
                            : f.uncertainty_reason}
                        </div>
                      )}
                    </td>
                    <td><StatusBadge value={f.state} /></td>
                    <td>
                      {hasValue && f.state !== 'HUMAN_CONFIRMED_ABSENT' ? (
                        <Confidence value={f.confidence} />
                      ) : (
                        <span className="muted" title="No extracted value — confidence not applicable">—</span>
                      )}
                    </td>
                    <td className="muted">
                      {f.source_engine}
                      {f.preprocessing_variant !== 'original' ? ` + ${f.preprocessing_variant}` : ''}
                    </td>
                    <td>
                      {evs.length > 0 ? (
                        <span className="flex" style={{ gap: 6, alignItems: 'center' }}>
                          <button
                            type="button"
                            className="btn sm secondary"
                            title="Highlight the exact region this value was read from"
                            onClick={() => setEvidenceFocus({ field: f.field_name, evidenceId: evs[0].id })}
                          >
                            View evidence
                          </button>
                          <a
                            href={api.fileUrl('crops', evs[0].stored_filename)}
                            target="_blank"
                            rel="noreferrer"
                            title="Open the retained crop in a new tab"
                          >
                            crop
                          </a>
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                      {evs[0]?.note?.toLowerCase().includes('candidate') && (
                        <span
                          className="muted"
                          style={{ display: 'block', fontSize: 11 }}
                          title={evs[0].note}
                        >
                          review candidate
                        </span>
                      )}
                    </td>
                    {canEdit && (
                      <td>
                        <span className="flex" style={{ gap: 4 }}>
                          {hasValue && (
                            <button className="btn sm secondary" onClick={() => openEdit(f.field_name, f.display_value)}>
                              Edit
                            </button>
                          )}
                          {!hasValue && f.state !== 'HUMAN_CONFIRMED_ABSENT' && (
                            <button
                              className="btn sm secondary"
                              title="Confirm this declaration is genuinely absent from the package after manual examination — audited"
                              onClick={() => openConfirmAbsent(f.field_name)}
                            >
                              Confirm absent
                            </button>
                          )}
                        </span>
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}

        {/* 2a. conflicting evidence — both sides, never silently resolved */}
        {conflicting.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <b style={{ fontSize: 13 }}>⚠ Conflicting evidence</b>
            <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
              The supplied images disagree on these declarations. Pocket does not choose between them.
            </div>
            {conflicting.map((f) => {
              const evs = evidenceByField.get(f.field_name) || []
              return (
                <div key={f.id} style={{ marginBottom: 12 }}>
                  <b style={{ fontSize: 12.5 }}>{f.field_name.replace(/_/g, ' ')}</b>
                  <div className="conflict-pair">
                    {evs.map((ev) => (
                      <div className="side" key={ev.id}>
                        <img className="img" src={api.fileUrl('crops', ev.stored_filename)} alt={`evidence for ${f.field_name}`} />
                        <code style={{ fontSize: 12 }}>{ev.raw_text || '—'}</code>
                        <div className="muted" style={{ fontSize: 11.5, marginTop: 4 }}>
                          {ev.extraction_method || 'OCR'} · conf {Math.round((ev.confidence || 0) * 100)}%
                        </div>
                      </div>
                    ))}
                    {evs.length === 0 && <div className="muted">No anchored evidence retained.</div>}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Card>

      {/* 3. classification */}
      <SectionHead no="3" title="Product classification" />
      <div className="row2">
        <Card title="Result">
          <div className="kv">
            <dt>Category</dt><dd>{d.category || '—'}</dd>
            <dt>State</dt><dd><StatusBadge value={d.category_state} /></dd>
            <dt>Confidence</dt><dd><Confidence value={d.category_confidence} /></dd>
          </div>
        </Card>
        <Card title="Signals">
          <p className="muted" style={{ fontSize: 12.5 }}>{d.classification_signals || 'No signals recorded.'}</p>
        </Card>
      </div>

      {/* 4. rule evaluation */}
      <SectionHead
        no="4"
        title="Rule evaluations"
        hint="deterministic engine — AI never decides a legal outcome"
      />
      <Card>
        {d.rules.length === 0 ? (
          <EmptyState headline="No rule evaluations" how="Rules are evaluated after the scan pipeline completes." />
        ) : (
          d.rules.map((r) => (
            <div key={r.id} style={{ borderBottom: '1px solid var(--border)', padding: '10px 0' }}>
                <div className="flex">
                  <b style={{ fontSize: 13 }}>{r.rule_number || '—'} · {r.title}</b>
                  <span className="right flex">
                    {r.critical && <span className="muted">critical</span>}
                    <StatusBadge value={r.status} />
                  </span>
                </div>
                <div className="muted" style={{ margin: '4px 0' }}>{r.reason}</div>
                {r.observed && <div style={{ fontSize: 12.5 }}><b>Observed:</b> {r.observed}</div>}
                <div className="muted" style={{ fontSize: 12 }}>Expected: {r.expected}</div>
                {ruleEvidenceFor(d, r.id).length > 0 ? (
                  <div className="rule-evidence">
                    <span className="muted" style={{ fontSize: 11.5 }}>Evidence:</span>
                    {ruleEvidenceFor(d, r.id).map((e) => (
                      <button
                        key={e.id}
                        type="button"
                        className="ev-chip"
                        title={`${e.raw_text || e.field_name} · conf ${Math.round((e.confidence || 0) * 100)}% · ${e.extraction_method}`}
                        onClick={() => setEvidenceFocus({ field: e.field_name, evidenceId: e.id })}
                      >
                        <img src={api.fileUrl('crops', e.stored_filename)} alt="" />
                        <span>{e.field_name.replace(/_/g, ' ')}</span>
                      </button>
                    ))}
                    <button
                      type="button"
                      className="btn sm secondary"
                      onClick={() => {
                        const first = ruleEvidenceFor(d, r.id)[0]
                        if (first) setEvidenceFocus({ field: first.field_name, evidenceId: first.id })
                      }}
                    >
                      View evidence
                    </button>
                  </div>
                ) : (
                  <div className="muted" style={{ fontSize: 11.5, marginTop: 4 }}>
                    No retained source region for this check
                    {r.status === 'NOT_APPLICABLE'
                      ? ' — the requirement is out of scope for this package.'
                      : r.status === 'UNCERTAIN'
                        ? ' — the requirement could not be decided from the supplied evidence.'
                        : '.'}
                  </div>
                )}
            </div>
          ))
        )}
      </Card>

      {/* 5. violations */}
      {d.violations.length > 0 && (
        <>
          <SectionHead no="5" title="Potential violations" hint="human confirmation converts these into confirmed violations" />
          <Card>
            <table className="tbl">
              <thead><tr><th>Rule</th><th>Title</th><th>Severity</th><th>Status</th><th>Description</th>{canEdit && <th>Actions</th>}</tr></thead>
              <tbody>
                {d.violations.map((v) => (
                  <tr key={v.id}>
                    <td>{v.rule_number}</td>
                    <td>{v.title}</td>
                    <td><StatusBadge value={v.severity} /></td>
                    <td><StatusBadge value={v.status} /></td>
                    <td className="muted">{v.description}</td>
                    {canEdit && (
                      <td>
                        <div className="flex">
                          <button className="btn sm danger" disabled={busy} onClick={() => openViolation(v.id, 'CONFIRM', v.rule_number, v.title)}>
                            Confirm
                          </button>
                          <button className="btn sm secondary" disabled={busy} onClick={() => openViolation(v.id, 'DISMISS', v.rule_number, v.title)}>
                            Dismiss
                          </button>
                          <button className="btn sm secondary" disabled={busy} onClick={() => openViolation(v.id, 'RESOLVE', v.rule_number, v.title)}>
                            Resolve
                          </button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}

      {/* 6. manual review */}
      {canEdit && (
        <>
          <SectionHead no="6" title="Manual review" hint="every action is audited with actor, time and reason" />
          <Card>
            <div className="flex" style={{ alignItems: 'flex-start' }}>
              <div className="field" style={{ flex: 1, marginBottom: 0 }}>
                <label htmlFor="review-note">Review note</label>
                <textarea
                  id="review-note"
                  rows={2}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Record observations, decisions or follow-ups…"
                />
              </div>
              <button
                className="btn sm"
                style={{ alignSelf: 'flex-end' }}
                disabled={busy || !note.trim()}
                onClick={() => act(async () => {
                  await api.post(`/inspections/${id}/review/note`, { note })
                  setNote('')
                }, 'Review note added')}
              >
                Add note
              </button>
            </div>
            <div className="flex mt">
              <button className="btn sm success" disabled={busy} onClick={() => { setDialogReason(''); setDialog({ kind: 'finalize', decision: 'COMPLIANT' }) }}>
                Finalize: COMPLIANT
              </button>
              <button className="btn sm danger" disabled={busy} onClick={() => { setDialogReason(''); setDialog({ kind: 'finalize', decision: 'NON_COMPLIANT' }) }}>
                Finalize: NON-COMPLIANT
              </button>
              <button className="btn sm secondary" onClick={openAdd} disabled={busy}>Add field</button>
            </div>
            <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
              Finalizing requires a written reason and is recorded in the audit history with your identity.
            </p>
          </Card>
        </>
      )}

      {/* 7. history */}
      {/* 7. Rule 7 letter-height measurement (needs a physical scale to be meaningful) */}
      <SectionHead
        no="7"
        title="Rule 7 — letter height (font size)"
        hint="millimetres need a recorded scale; the measurement never guesses one"
      />
      {fontSize ? (
        <FontSizePanel
          data={fontSize}
          imageUrl={d.images[0] ? api.fileUrl('originals', d.images[0].stored_filename) : undefined}
          onCalibrated={() => load()}
        />
      ) : (
        <Card>
          <p className="muted">
            The Rule 7 measurement is unavailable for this inspection (no images or no processed rule
            evaluation yet).
          </p>
        </Card>
      )}

      {/* 8. what the vision engine measured on each face, with the stage timings of this run */}
      <SectionHead
        no="8"
        title="Vision analysis (on-device)"
        hint="regions, prominence and readability measured from the images — never a declaration value"
      />
      <div className="row2">
        <Card title="Observations">
          <VisionPanel vision={d.vision} images={d.images} evidence={d.evidence} />
        </Card>
        <Card title="Pipeline timings">
          {Object.keys(d.stage_timings || {}).length === 0 ? (
            <div className="muted">
              No timings recorded for this run{d.provenance && !d.provenance.up_to_date ? ' — reprocess it to measure it again.' : '.'}
            </div>
          ) : (
            <table className="tbl">
              <thead><tr><th>Stage</th><th>Elapsed</th></tr></thead>
              <tbody>
                {Object.entries(d.stage_timings)
                  .sort((a, b) => b[1] - a[1])
                  .map(([stage, ms]) => (
                    <tr key={stage}>
                      <td>{stage.replace(/_/g, ' ')}</td>
                      <td className="mono">{(ms / 1000).toFixed(1)} s</td>
                    </tr>
                  ))}
                <tr>
                  <td><b>Total pipeline</b></td>
                  <td className="mono"><b>{(d.duration_ms / 1000).toFixed(1)} s</b></td>
                </tr>
              </tbody>
            </table>
          )}
          <div className="mt">
            <div className="muted">Perception sources for this result</div>
            <div className="flex" style={{ marginTop: 6, flexWrap: 'wrap', gap: 6 }}>
              <span className="chip mono">{d.vision_engine || 'vision engine not recorded'}</span>
              <span className="chip">provider: {(d.provider_status || 'not recorded').replace(/_/g, ' ').toLowerCase()}</span>
            </div>
          </div>
        </Card>
      </div>

      {/* 9. analysis provenance, reprocessing and the before/after audit of every regeneration */}
      {d.provenance && (
        <>
          <SectionHead
            no="9"
            title="Analysis provenance & reprocessing"
            hint="what changed, when, by whom, and under which engine and rule set"
          />
          <ReprocessPanel
            inspectionId={d.id}
            canReprocess={hasPermission('inspections.manage')}
            provenance={d.provenance}
            onReprocessed={() => load()}
          />
        </>
      )}

      <SectionHead no="10" title="Review history & reports" />
      <div className="row2">
        <Card title="Review history (audit)">
          {d.review_actions.length === 0 ? (
            <div className="muted">No manual review actions yet.</div>
          ) : (
            <table className="tbl">
              <thead><tr><th>Time</th><th>Reviewer</th><th>Action</th><th>Original</th><th>Corrected</th><th>Reason</th></tr></thead>
              <tbody>
                {d.review_actions.map((a) => (
                  <tr key={a.id}>
                    <td>{a.created_at.slice(0, 19).replace('T', ' ')}</td>
                    <td>{a.reviewer}</td>
                    <td>{a.action}</td>
                    <td>{a.original_value || '—'}</td>
                    <td>{a.corrected_value || '—'}</td>
                    <td className="muted">{a.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Reports">
          {d.reports.length === 0 ? (
            <div className="muted">No reports generated yet.</div>
          ) : (
            d.reports.map((r) => (
              <div key={r.id} className="flex" style={{ marginBottom: 8 }}>
                <a href={api.fileUrl('reports', r.stored_filename)} target="_blank" rel="noreferrer">
                  Report #{r.id} (PDF)
                </a>
                <span className="muted right">{r.created_at.slice(0, 19).replace('T', ' ')} · {r.generated_by}</span>
              </div>
            ))
          )}
        </Card>
      </div>

      {/* evidence locator — the exact region a declaration or rule conclusion came from */}
      {evidenceFocus && d && (
        <Modal title={`Evidence — ${evidenceFocus.field.replace(/_/g, ' ')}`} onClose={() => setEvidenceFocus(null)}>
          <p className="muted" style={{ fontSize: 12.5 }}>
            The highlighted region is the exact area the value was read from on the package image;
            the retained crop and its OCR text are shown alongside.
          </p>
          <EvidenceViewer
            images={d.images}
            fields={d.fields}
            evidence={d.evidence}
            initialField={evidenceFocus.field}
            focusEvidenceId={evidenceFocus.evidenceId}
            compact={false}
          />
        </Modal>
      )}

      {/* dialogs */}
      {dialog && (
        <Modal title="Review action" onClose={() => setDialog(null)}>
          {dialog.kind === 'confirm_absent' && (
            <>
              <h3>Confirm absence — {dialog.fieldName.replace(/_/g, ' ')}</h3>
              <p className="muted">
                This records that you have <b>physically examined the package</b> and confirmed the
                declaration is genuinely absent. Applicable rules will be re-evaluated and the
                inspection may become <b>NON-COMPLIANT</b>. This action is audited with your name.
              </p>
              <div className="field">
                <label htmlFor="dlg-reason">Reason for confirming absence (required)</label>
                <textarea
                  id="dlg-reason"
                  rows={3}
                  value={dialogReason}
                  onChange={(e) => setDialogReason(e.target.value)}
                  placeholder="e.g. Physical examination of all sides: no MRP is printed anywhere on the package"
                />
              </div>
              <div className="flex" style={{ justifyContent: 'flex-end' }}>
                <button className="btn sm secondary" onClick={() => setDialog(null)}>Cancel</button>
                <button className="btn sm danger" disabled={busy || !dialogReason.trim()} onClick={submitDialog}>
                  Confirm absent
                </button>
              </div>
            </>
          )}
          {dialog.kind === 'edit' && (
            <>
              <h3>Edit field — {dialog.fieldName.replace(/_/g, ' ')}</h3>
              <p className="muted">The original AI output is preserved in the audit history.</p>
              <div className="field">
                <label htmlFor="dlg-value">Corrected value</label>
                <input id="dlg-value" value={dialogValue} onChange={(e) => setDialogValue(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="dlg-edit-reason">Reason (optional)</label>
                <input id="dlg-edit-reason" value={dialogReason} onChange={(e) => setDialogReason(e.target.value)} placeholder="e.g. verified from original package" />
              </div>
              <div className="flex" style={{ justifyContent: 'flex-end' }}>
                <button className="btn sm secondary" onClick={() => setDialog(null)}>Cancel</button>
                <button className="btn sm" disabled={busy || !dialogValue.trim()} onClick={submitDialog}>Save correction</button>
              </div>
            </>
          )}
          {dialog.kind === 'add' && (
            <>
              <h3>Add missing field</h3>
              <p className="muted">Add a declaration you can read on the physical package.</p>
              <div className="field">
                <label htmlFor="dlg-name">Field name (e.g. mrp, batch_lot, date_manufacturing)</label>
                <input id="dlg-name" value={dialogValue.split('\n')[0] || ''} onChange={(e) => setDialogValue(e.target.value + '\n' + (dialogValue.split('\n')[1] || ''))} />
              </div>
              <div className="field">
                <label htmlFor="dlg-addval">Value</label>
                <input id="dlg-addval" value={dialogValue.split('\n')[1] || ''} onChange={(e) => setDialogValue((dialogValue.split('\n')[0] || '') + '\n' + e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="dlg-add-reason">Reason (optional)</label>
                <input id="dlg-add-reason" value={dialogReason} onChange={(e) => setDialogReason(e.target.value)} placeholder="e.g. printed on bottom panel, not captured in photos" />
              </div>
              <div className="flex" style={{ justifyContent: 'flex-end' }}>
                <button className="btn sm secondary" onClick={() => setDialog(null)}>Cancel</button>
                <button
                  className="btn sm"
                  disabled={busy || !(dialogValue.split('\n')[0] || '').trim() || !(dialogValue.split('\n')[1] || '').trim()}
                  onClick={submitDialog}
                >
                  Add field
                </button>
              </div>
            </>
          )}
          {dialog.kind === 'violation' && (
            <>
              <h3>
                {dialog.action === 'CONFIRM' ? 'Confirm' : dialog.action === 'DISMISS' ? 'Dismiss' : 'Resolve'} violation — rule {dialog.ruleNumber}
              </h3>
              <p className="muted">{dialog.title}</p>
              <p className="muted">
                {dialog.action === 'CONFIRM'
                  ? 'You agree this non-compliance is real. It continues to drive a NON-COMPLIANT decision. This is audited with your name.'
                  : dialog.action === 'DISMISS'
                    ? 'You determine this is NOT a violation. A reason is required; the deterministic engine is re-run and the finding is excluded from the decision (never a silent override).'
                    : 'The issue has been rectified / addressed. A reason is required; the finding is excluded from the decision. This is audited with your name.'}
              </p>
              <div className="field">
                <label htmlFor="dlg-vio-reason">
                  Reason {dialog.action === 'CONFIRM' ? '(optional)' : '(required)'}
                </label>
                <textarea
                  id="dlg-vio-reason"
                  rows={3}
                  value={dialogReason}
                  onChange={(e) => setDialogReason(e.target.value)}
                  placeholder={dialog.action === 'CONFIRM' ? 'e.g. Physical examination confirms the declaration is missing' : 'e.g. Declaration is present and legible; OCR association produced a false positive'}
                />
              </div>
              <div className="flex" style={{ justifyContent: 'flex-end' }}>
                <button className="btn sm secondary" onClick={() => setDialog(null)}>Cancel</button>
                <button
                  className={`btn sm ${dialog.action === 'CONFIRM' ? 'danger' : ''}`}
                  disabled={busy || (dialog.action !== 'CONFIRM' && !dialogReason.trim())}
                  onClick={submitDialog}
                >
                  {dialog.action === 'CONFIRM' ? 'Confirm violation' : dialog.action === 'DISMISS' ? 'Dismiss violation' : 'Mark resolved'}
                </button>
              </div>
            </>
          )}
          {dialog.kind === 'finalize' && (
            <>
              <h3>Finalize decision — {dialog.decision.replace(/_/g, ' ')}</h3>
              <p className="muted">
                Your reason and identity are recorded permanently in the audit trail. This supersedes the
                system-proposed decision.
              </p>
              <div className="field">
                <label htmlFor="dlg-fin-reason">Reason (required)</label>
                <textarea
                  id="dlg-fin-reason"
                  rows={3}
                  value={dialogReason}
                  onChange={(e) => setDialogReason(e.target.value)}
                  placeholder="e.g. Verified all applicable declarations against the physical package"
                />
              </div>
              <div className="flex" style={{ justifyContent: 'flex-end' }}>
                <button className="btn sm secondary" onClick={() => setDialog(null)}>Cancel</button>
                <button
                  className={`btn sm ${dialog.decision === 'COMPLIANT' ? 'success' : 'danger'}`}
                  disabled={busy || !dialogReason.trim()}
                  onClick={submitDialog}
                >
                  Finalize
                </button>
              </div>
            </>
          )}
        </Modal>
      )}

      {toastNode}
    </>
  )
}
