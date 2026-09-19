import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Card } from '../components/Badges'
import { Icon } from '../components/Icon'
import { Confidence, EmptyState, SectionHead, useToast } from '../components/ui'
import { api } from '../services/api'
import type {
  InspectionSummary,
  ListingConsistency,
  ListingDeclarationCheck,
  ListingOut,
  ListingRow,
} from '../types'

/**
 * Online listing / e-commerce inspection support.
 *
 * Three things this page is careful about, because they are the point of the feature:
 *
 *  1. A declaration that is absent from the CAPTURED TEXT is shown as "absent from captured text"
 *     — never as "not displayed". The capture may not cover the whole listing page.
 *  2. Every check renders the LEGAL STATUS of the rule behind it, so a citation the application has
 *     not confidently verified cannot look like settled law.
 *  3. A package-vs-listing difference is labelled a POTENTIAL INCONSISTENCY, with the explanation
 *     that the application does not determine a breach.
 */

const VERDICT_TONE: Record<string, string> = {
  MATCH: 'PASS',
  INCONSISTENCY_POTENTIAL: 'NEEDS_MANUAL_REVIEW',
  PACKAGE_ONLY: 'NOT_APPLICABLE',
  LISTING_ONLY: 'NOT_APPLICABLE',
  NOT_COMPARABLE: 'UNCERTAIN',
}

const VERDICT_LABEL: Record<string, string> = {
  MATCH: 'Agrees',
  INCONSISTENCY_POTENTIAL: 'Potential inconsistency',
  PACKAGE_ONLY: 'Package only',
  LISTING_ONLY: 'Listing only',
  NOT_COMPARABLE: 'Not comparable',
}

function LegalStatus({ status, label, note }: { status?: string; label?: string; note?: string }) {
  if (!status) return null
  const warn = status !== 'VERIFIED_PRIMARY'
  return (
    <div className={`legal-status${warn ? ' warn' : ''}`} title={note || ''}>
      <Icon name="alert-triangle" size={13} />
      <span>{label || status}</span>
    </div>
  )
}

export default function Listings() {
  const [params, setParams] = useSearchParams()
  const { show, node: toastNode } = useToast()
  const deepLinkInspection = Number(params.get('inspection') || 0) || null

  const [rows, setRows] = useState<ListingRow[]>([])
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<ListingOut | null>(null)
  const [declarations, setDeclarations] = useState<ListingDeclarationCheck | null>(null)
  const [consistency, setConsistency] = useState<ListingConsistency | null>(null)
  const [inspections, setInspections] = useState<InspectionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const [form, setForm] = useState({
    text: '',
    platform: '',
    source: 'MANUAL_TEXT',
    source_url: '',
    inspection_id: deepLinkInspection ? String(deepLinkInspection) : '',
  })

  const loadRows = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.listings(query)
      setRows(res.items)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load captured listings.')
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => {
    void loadRows()
  }, [loadRows])

  useEffect(() => {
    // The inspection selector needs real inspection numbers — never a free-text id.
    api
      .get<{ items: InspectionSummary[]; total: number }>('/inspections?page_size=100')
      .then((res) => setInspections(res.items ?? []))
      .catch(() => setInspections([]))
  }, [])

  const openListing = useCallback(async (id: number) => {
    setBusy(true)
    try {
      const [detail, decl] = await Promise.all([api.listing(id), api.listingDeclarations(id)])
      setSelected(detail)
      setDeclarations(decl)
      setConsistency(detail.consistency)
    } catch (err) {
      show(err instanceof Error ? err.message : 'Could not open the listing.')
    } finally {
      setBusy(false)
    }
  }, [show])

  useEffect(() => {
    if (rows.length && !selected) void openListing(rows[0].id)
  }, [rows, selected, openListing])

  const submit = async () => {
    if (!form.text.trim()) {
      show('Paste the listing text first — it is the evidence the extraction runs against.')
      return
    }
    setBusy(true)
    try {
      const created = await api.listingCreate({
        text: form.text,
        source: form.source,
        source_url: form.source_url,
        platform: form.platform,
        inspection_id: form.inspection_id ? Number(form.inspection_id) : null,
      })
      show(`Listing captured — ${Object.keys(created.extraction.fields).length} declaration(s) read.`)
      setForm({ ...form, text: '', source_url: '', platform: '' })
      setSelected(null)
      setConsistency(null)
      await loadRows()
      await openListing(created.id)
    } catch (err) {
      show(err instanceof Error ? err.message : 'Could not capture the listing.')
    } finally {
      setBusy(false)
    }
  }

  const runConsistency = async () => {
    if (!selected) return
    setBusy(true)
    try {
      const result = await api.listingConsistency(selected.id)
      setConsistency(result)
      if (result.status === 'NO_PACKAGE_SIDE') show(result.reason || 'No package side to compare.')
      else show(result.summary?.headline || 'Comparison complete.')
      await loadRows()
    } catch (err) {
      show(err instanceof Error ? err.message : 'Could not run the comparison.')
    } finally {
      setBusy(false)
    }
  }

  const attach = async (inspectionId: number) => {
    if (!selected) return
    setBusy(true)
    try {
      const updated = await api.listingAttach(selected.id, inspectionId)
      setSelected(updated)
      setConsistency(updated.consistency)
      show('Listing linked to the inspection — run the comparison to compare them.')
      await loadRows()
    } catch (err) {
      show(err instanceof Error ? err.message : 'Could not link the listing.')
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!selected) return
    setBusy(true)
    try {
      await api.listingDelete(selected.id)
      show('Listing deleted.')
      setSelected(null)
      setDeclarations(null)
      setConsistency(null)
      await loadRows()
    } catch (err) {
      show(err instanceof Error ? err.message : 'Could not delete the listing.')
    } finally {
      setBusy(false)
    }
  }

  const fieldsRead = useMemo(
    () => (selected ? Object.entries(selected.extraction.fields) : []),
    [selected],
  )

  return (
    <div className="page">
      {toastNode}
      <div className="page-head">
        <div>
          <h1>Online Listings</h1>
          <p className="muted">
            Capture a marketplace/product listing, read its declarations, and compare them with the
            package evidence of an inspection. A difference is reported as a{' '}
            <strong>potential inconsistency</strong> for an officer to assess — the application does
            not determine that any difference breaches a provision.
          </p>
        </div>
      </div>

      <div className="grid cols-2">
        <Card>
          <SectionHead no="1" title="Capture the listing" hint="paste the listing text as displayed" />
          <div className="field">
            <label htmlFor="listing-text">Listing text (as displayed on the page)</label>
            <textarea
              id="listing-text"
              rows={7}
              value={form.text}
              onChange={(e) => setForm({ ...form, text: e.target.value })}
              placeholder={'Medimix Ayurvedic Soap - 750 g\nBrand: Medimix\nMRP: ₹220.00\nNet Quantity: 750 g\nCountry of Origin: India'}
            />
          </div>
          <div className="row2">
            <div className="field">
              <label htmlFor="listing-source">Evidence source</label>
              <select
                id="listing-source"
                value={form.source}
                onChange={(e) => setForm({ ...form, source: e.target.value })}
              >
                <option value="MANUAL_TEXT">Pasted listing text</option>
                <option value="SCREENSHOT">Transcribed from a listing screenshot</option>
                <option value="LISTING_URL">Listing URL (text captured from the page)</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="listing-platform">Platform / marketplace</label>
              <input
                id="listing-platform"
                value={form.platform}
                onChange={(e) => setForm({ ...form, platform: e.target.value })}
                placeholder="e.g. example-marketplace"
              />
            </div>
          </div>
          {form.source === 'LISTING_URL' && (
            <div className="field">
              <label htmlFor="listing-url">Listing URL</label>
              <input
                id="listing-url"
                value={form.source_url}
                onChange={(e) => setForm({ ...form, source_url: e.target.value })}
                placeholder="https://…"
              />
            </div>
          )}
          <div className="field">
            <label htmlFor="listing-inspection">Compare against package inspection (optional)</label>
            <select
              id="listing-inspection"
              value={form.inspection_id}
              onChange={(e) => setForm({ ...form, inspection_id: e.target.value })}
            >
              <option value="">Not linked — file it on its own</option>
              {inspections.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.inspection_number} — {i.category || 'uncategorised'} · {i.status}
                </option>
              ))}
            </select>
          </div>
          <button className="btn" onClick={submit} disabled={busy}>
            <Icon name="scan-line" size={15} /> Read declarations from this listing
          </button>
        </Card>

        <Card>
          <SectionHead no="2" title="Captured listings" hint="search by title, platform, URL or text" />
          <div className="field">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search captured listings…"
            />
          </div>
          {loading ? (
            <p className="muted">Loading…</p>
          ) : error ? (
            <p className="muted">{error}</p>
          ) : rows.length === 0 ? (
            <EmptyState
              glyph="🛒"
              headline="No listings captured yet"
              how="Paste a product listing on the left and its declarations are read against the e-commerce rule catalog."
            />
          ) : (
            <div className="listing-rows">
              {rows.map((row) => (
                <button
                  key={row.id}
                  className={`listing-row${selected?.id === row.id ? ' active' : ''}`}
                  onClick={() => void openListing(row.id)}
                >
                  <span className="lr-title">{row.title || '(title not read)'}</span>
                  <span className="lr-meta">
                    {row.platform || row.source.replace('_', ' ').toLowerCase()} ·{' '}
                    {new Date(row.created_at).toLocaleDateString()} · {row.fields_read} field(s)
                  </span>
                  {row.consistency && (
                    <span className={`badge ${row.consistency.potential_inconsistencies ? 'NEEDS_MANUAL_REVIEW' : 'PASS'}`}>
                      <i className="bdot" />
                      <span>
                        {row.consistency.potential_inconsistencies
                          ? `${row.consistency.potential_inconsistencies} potential inconsistency`
                          : 'agrees with package'}
                      </span>
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </Card>
      </div>

      {selected && (
        <>
          <Card>
            <SectionHead
              no="3"
              title={`Declarations read from the listing (#${selected.id})`}
              hint={selected.inspection_id ? `linked to inspection #${selected.inspection_id}` : 'not linked to an inspection'}
            />
            <div className="listing-actions">
              {selected.inspection_id ? (
                <button className="btn" onClick={runConsistency} disabled={busy}>
                  <Icon name="shield-check" size={15} /> Run package vs listing comparison
                </button>
              ) : (
                <div className="inline-attach">
                  <select
                    value=""
                    onChange={(e) => e.target.value && void attach(Number(e.target.value))}
                    disabled={busy}
                  >
                    <option value="">Link to a package inspection…</option>
                    {inspections.map((i) => (
                      <option key={i.id} value={i.id}>
                        {i.inspection_number} — {i.category || 'uncategorised'} · {i.status}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <button className="btn ghost" onClick={() => setParams({})}>
                <Icon name="x" size={15} /> Clear deep link
              </button>
              <button className="btn danger" onClick={remove} disabled={busy}>
                <Icon name="trash" size={15} /> Delete listing
              </button>
            </div>

            {declarations && (
              <div className="decl-block">
                <div className="decl-head">
                  <strong>
                    {declarations.rule_number ? `Rule ${declarations.rule_number} — ` : ''}
                    {declarations.title || declarations.check_id}
                  </strong>
                  <span className={`badge ${declarations.status}`}>
                    <i className="bdot" />
                    <span>{declarations.status.replace(/_/g, ' ')}</span>
                  </span>
                </div>
                <p className="muted">{declarations.reason}</p>
                <LegalStatus
                  status={declarations.legal_status}
                  label={declarations.legal_status_label}
                  note={declarations.legal_status_note}
                />
                {declarations.source_reference && (
                  <p className="muted">Reference: {declarations.source_reference}</p>
                )}
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Declaration</th>
                      <th>In captured text</th>
                      <th>Value</th>
                      <th>Evidence line</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(declarations.declarations || []).map((d) => (
                      <tr key={d.field}>
                        <td>{d.field.replace(/_/g, ' ')}</td>
                        <td>
                          <span className={`badge ${d.status === 'FOUND' ? 'PASS' : 'UNCERTAIN'}`}>
                            <i className="bdot" />
                            <span>{d.status === 'FOUND' ? 'found' : 'absent from captured text'}</span>
                          </span>
                        </td>
                        <td>
                          {d.value || '—'}
                          {d.inferred && <span className="muted"> (inferred — confirm)</span>}
                          {d.role_uncertain && (
                            <div className="muted">role not established by the page — confirm whether this is the manufacturer / packer / importer</div>
                          )}
                          {d.related_price_evidence && (
                            <div className="muted">price shown: {d.related_price_evidence}</div>
                          )}
                        </td>
                        <td className="muted">{d.source_text || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {declarations.declarations?.some((d) => d.status !== 'FOUND') && (
                  <p className="muted">
                    A declaration absent from the captured text is not proof that it is not displayed on
                    the listing — the capture may not cover the whole page (details can sit in a tab or a
                    specification table). Confirm on the listing page.
                  </p>
                )}
              </div>
            )}
          </Card>

          <Card>
            <SectionHead
              no="4"
              title="Package vs listing consistency"
              hint="declaration by declaration"
            />
            {!consistency || consistency.status === 'NO_PACKAGE_SIDE' ? (
              <EmptyState
                glyph="⚖"
                headline="Nothing compared yet"
                how={
                  consistency?.reason ||
                  'Link this listing to a package inspection, then run the comparison.'
                }
              />
            ) : (
              <>
                <div className={`badge ${consistency.summary?.potential_inconsistencies ? 'NEEDS_MANUAL_REVIEW' : 'PASS'}`}>
                  <i className="bdot" />
                  <span>{consistency.summary?.headline}</span>
                </div>
                <LegalStatus
                  status={consistency.legal_status}
                  label={consistency.legal_status_label}
                  note={consistency.legal_status_note}
                />
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Declaration</th>
                      <th>Package</th>
                      <th>Listing</th>
                      <th>Verdict</th>
                      <th>How it was compared</th>
                    </tr>
                  </thead>
                  <tbody>
                    {consistency.comparisons.map((c) => (
                      <tr key={c.field}>
                        <td>{c.field.replace(/_/g, ' ')}</td>
                        <td>{c.package_value || '—'}</td>
                        <td>{c.listing_value || '—'}</td>
                        <td>
                          <span className={`badge ${VERDICT_TONE[c.verdict] || 'UNCERTAIN'}`}>
                            <i className="bdot" />
                            <span>{VERDICT_LABEL[c.verdict] || c.verdict}</span>
                          </span>
                          {c.note && <div className="muted">{c.note}</div>}
                        </td>
                        <td className="muted">{c.method}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {consistency.not_a_violation_note && (
                  <p className="muted">{consistency.not_a_violation_note}</p>
                )}
              </>
            )}
          </Card>

          <Card>
            <SectionHead no="5" title="Everything read from this listing" hint="with its source line" />
            <div className="grid cols-2">
          {fieldsRead.map(([name, value]) => (
            <div key={name} className="field-row">
              <div>
                <strong>{name.replace(/_/g, ' ')}</strong>
                <div className="muted">{value.source_text || value.method}</div>
              </div>
                  <div className="right">
                    <div>{value.display_value}</div>
                    {typeof value.confidence === 'number' && <Confidence value={value.confidence} />}
                    {value.inferred && <span className="muted">inferred — confirm</span>}
                  </div>
                </div>
              ))}
              {Object.entries(selected.extraction.listing_only).map(([name, value]) => (
                <div key={name} className="field-row">
                  <div>
                    <strong>{name.replace(/_/g, ' ')}</strong>
                    <div className="muted">{value.source_text || value.method}</div>
                  </div>
                  <div className="right">
                    <div>{value.display_value}</div>
                    {value.inferred && <span className="muted">inferred — confirm</span>}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
