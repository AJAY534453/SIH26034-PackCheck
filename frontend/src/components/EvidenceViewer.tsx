// Evidence workspace: package image + field list. Selecting a field highlights its
// exact source region on the image — the reviewer can always answer "why this value?".
import { useEffect, useMemo, useState } from 'react'
import { api } from '../services/api'
import type { EvidenceOut, FieldOut, ImageOut } from '../types'
import { Confidence, StatusBadge } from './ui'

function parseBbox(s: string): [number, number, number, number] | null {
  if (!s) return null
  const m = s.match(/-?\d+(?:\.\d+)?/g)
  if (!m || m.length < 4) return null
  const [x1, y1, x2, y2] = m.slice(0, 4).map(Number)
  if ([x1, y1, x2, y2].some((n) => !Number.isFinite(n))) return null
  return [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)]
}

export default function EvidenceViewer({
  images,
  fields,
  evidence,
  initialField,
  focusEvidenceId,
  compact = false,
}: {
  images: ImageOut[]
  fields: FieldOut[]
  evidence: EvidenceOut[]
  /** Open already focused on one declaration (e.g. opened from a rule row). */
  initialField?: string
  /** Open focused on one exact evidence row (e.g. "View evidence" on a rule conclusion). */
  focusEvidenceId?: number
  /** Hide the declaration list — used when the panel is opened for ONE known field. */
  compact?: boolean
}) {
  const [imageId, setImageId] = useState<number | null>(images[0]?.id ?? null)
  const [fieldName, setFieldName] = useState<string | null>(null)
  const [zoom, setZoom] = useState(1)

  // Reset when the inspection data changes identity, and apply the requested focus: the region a
  // caller asked about is selected (and its image shown) before any interaction happens.
  useEffect(() => {
    const focused = focusEvidenceId != null ? evidence.find((e) => e.id === focusEvidenceId) : undefined
    const target = focused?.field_name ?? initialField ?? null
    setFieldName(target && fields.some((f) => f.field_name === target) ? target : null)
    setImageId(focused?.image_id ?? images[0]?.id ?? null)
    setZoom(focused ? 1.75 : 1)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-focus only when the requested
    // focus or the inspection's images change, never on an unrelated re-render.
  }, [images, initialField, focusEvidenceId])

  const imageById = useMemo(() => new Map(images.map((i) => [i.id, i])), [images])
  const evidenceByField = useMemo(() => {
    const m = new Map<string, EvidenceOut[]>()
    for (const ev of evidence) {
      const list = m.get(ev.field_name) || []
      list.push(ev)
      m.set(ev.field_name, list)
    }
    return m
  }, [evidence])

  const selected = fields.find((f) => f.field_name === fieldName) || null
  const evRows = fieldName ? evidenceByField.get(fieldName) || [] : []
  // prefer evidence anchored on the currently displayed image
  const activeEv = evRows.find((e) => e.image_id === imageId) || evRows[0] || null

  function selectField(f: FieldOut) {
    setFieldName(f.field_name)
    // jump to the image that anchors this field's evidence, if any
    const rows = evidenceByField.get(f.field_name) || []
    const target = rows.find((e) => e.image_id === imageId) || rows[0]
    if (target?.image_id != null && imageById.has(target.image_id)) setImageId(target.image_id)
  }

  const img = imageId != null ? imageById.get(imageId) : null
  const bbox = activeEv ? parseBbox(activeEv.bbox) : null
  const rect =
    img && bbox && bbox[2] > bbox[0] && bbox[3] > bbox[1]
      ? {
          left: `${(bbox[0] / img.width) * 100}%`,
          top: `${(bbox[1] / img.height) * 100}%`,
          width: `${((bbox[2] - bbox[0]) / img.width) * 100}%`,
          height: `${((bbox[3] - bbox[1]) / img.height) * 100}%`,
        }
      : null

  if (images.length === 0) {
    return (
      <div className="empty">
        <div className="headline">No images in this inspection</div>
        <div className="how">Evidence highlighting requires at least one uploaded package image.</div>
      </div>
    )
  }

  return (
    <div>
      <div className="ev-split">
        <div>
          <div className="ev-stage">
            {img && (
              <>
                <img
                  src={api.fileUrl('originals', img.stored_filename)}
                  alt={`${img.role.replace(/_/g, ' ')} — ${img.original_filename}`}
                  style={{ transform: `scale(${zoom})` }}
                />
                {rect && fieldName && <div className="rect" style={rect} aria-hidden="true" />}
                <span className="tag">{img.role.replace(/_/g, ' ')}</span>
              </>
            )}
          </div>
          <div className="ev-tools">
            <label className="muted" htmlFor="ev-img-select">Image:</label>
            <select
              id="ev-img-select"
              value={imageId ?? ''}
              onChange={(e) => setImageId(Number(e.target.value) || null)}
              style={{ maxWidth: 260 }}
            >
              {images.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.role.replace(/_/g, ' ')} · {i.original_filename}
                </option>
              ))}
            </select>
            <span className="spacer" style={{ flex: 1 }} />
            <button className="btn sm secondary" onClick={() => setZoom((z) => Math.max(1, +(z - 0.25).toFixed(2)))} disabled={zoom <= 1} aria-label="Zoom out">−</button>
            <span className="muted" aria-live="polite">{Math.round(zoom * 100)}%</span>
            <button className="btn sm secondary" onClick={() => setZoom((z) => Math.min(4, +(z + 0.25).toFixed(2)))} disabled={zoom >= 4} aria-label="Zoom in">+</button>
            <button className="btn sm secondary" onClick={() => setZoom(1)}>Fit</button>
          </div>
        </div>

        <div>
          {!compact && (
            <div className="muted" style={{ marginBottom: 8 }}>
              Select a declaration to highlight where it was found on the package.
            </div>
          )}
          <div className="ev-fields" role="listbox" aria-label="Extracted declarations" hidden={compact}>
            {fields.length === 0 && (
              <div className="empty" style={{ padding: 20 }}>
                <div className="headline">No declarations extracted</div>
                <div className="how">Run the scan pipeline or upload additional package sides.</div>
              </div>
            )}
            {fields.map((f) => {
              const hasValue = !!f.display_value?.trim()
              return (
                <button
                  type="button"
                  key={f.id}
                  role="option"
                  aria-selected={f.field_name === fieldName}
                  className={`ev-field-row ${f.field_name === fieldName ? 'selected' : ''}`}
                  onClick={() => selectField(f)}
                >
                  <span className="fname">{f.field_name.replace(/_/g, ' ')}</span>
                  <span className="fval">{hasValue ? f.display_value : '—'}</span>
                  <StatusBadge value={f.state} />
                </button>
              )
            })}
          </div>

          {selected && (
            <div className="ev-crop-view">
              {activeEv && (
                <>
                  <img
                    src={api.fileUrl('crops', activeEv.stored_filename)}
                    alt={`Evidence crop for ${selected.field_name.replace(/_/g, ' ')}`}
                    className="evidence-crop"
                  />
                  <div className="ev-meta">
                    <div>
                      <b>Raw OCR:</b>{' '}
                      <code style={{ background: 'var(--code-bg)', padding: '1px 5px', borderRadius: 3 }}>
                        {activeEv.raw_text || '—'}
                      </code>
                    </div>
                    <div>
                      <b>Interpreted as:</b> {selected.display_value || '—'}{' '}
                      {selected.conflict_status === 'CONFLICTING' && (
                        <span style={{ color: 'var(--amber)' }}>· conflicting candidates retained</span>
                      )}
                    </div>
                    <div>
                      <b>Source:</b> {activeEv.extraction_method || selected.source_engine}
                      {selected.preprocessing_variant && selected.preprocessing_variant !== 'original'
                        ? ` · ${selected.preprocessing_variant}`
                        : ''}
                      {activeEv.image_id != null && imageById.has(activeEv.image_id)
                        ? ` · ${imageById.get(activeEv.image_id)!.role.replace(/_/g, ' ')} image`
                        : ''}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <b>Confidence:</b>
                      {selected.display_value?.trim() && selected.state !== 'HUMAN_CONFIRMED_ABSENT' ? (
                        <Confidence value={selected.confidence} />
                      ) : (
                        <span>— (no extracted value; confidence not applicable)</span>
                      )}
                    </div>
                    {selected.uncertainty_reason && <div>⚠ {selected.uncertainty_reason}</div>}
                    {selected.extraction_reason && <div>{selected.extraction_reason}</div>}
                  </div>
                </>
              )}
              {!activeEv && (
                <div className="ev-meta">
                  No anchored evidence for <b>{selected.field_name.replace(/_/g, ' ')}</b> —{' '}
                  {selected.display_value?.trim()
                    ? 'the value was inferred without a retained crop.'
                    : 'nothing was detected in the supplied images (this does not prove absence).'}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
