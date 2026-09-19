// Vision analysis panel.
//
// Shows what the ON-DEVICE vision engine actually measured on each package face — the prominent
// printed block, the candidate display region, graphic marks, and readability — plus whether the
// optional provider contributed. Every row can be opened on the image so the inspector sees the
// exact region the observation refers to. Nothing here is a declaration value or a legal verdict:
// a region is a fact about the photograph.
import { useMemo, useState } from 'react'
import { api } from '../services/api'
import type { EvidenceOut, ImageOut, VisionPayload, VisionRegionOut } from '../types'
import Modal from './Modal'
import { StatusBadge } from './ui'

const STATUS_LABEL: Record<string, { text: string; tone: 'ok' | 'warn' | 'bad' }> = {
  COMPLETED_WITH_PROVIDER: { text: 'On-device vision + provider', tone: 'ok' },
  COMPLETED: { text: 'On-device vision', tone: 'ok' },
  ON_DEVICE_ONLY_NO_PROVIDER: { text: 'On-device vision (no provider configured)', tone: 'ok' },
  ON_DEVICE_ONLY_PROVIDER_FAILED: { text: 'On-device vision — provider failed', tone: 'warn' },
  FAILED: { text: 'Vision failed — OCR only', tone: 'bad' },
}

const KIND_LABEL: Record<string, string> = {
  PANEL: 'Candidate display region',
  TEXT_BLOCK: 'Text block',
  SYMBOL: 'Graphic mark',
  LEGIBILITY: 'Readability',
}

function parseBbox(s: string): [number, number, number, number] | null {
  const m = (s || '').match(/-?\d+(?:\.\d+)?/g)
  if (!m || m.length < 4) return null
  const [x1, y1, x2, y2] = m.slice(0, 4).map(Number)
  if ([x1, y1, x2, y2].some((n) => !Number.isFinite(n))) return null
  if (x2 <= x1 || y2 <= y1) return null
  return [x1, y1, x2, y2]
}

export default function VisionPanel({
  vision,
  images,
  evidence,
}: {
  vision: VisionPayload | undefined
  images: ImageOut[]
  evidence?: EvidenceOut[]
}) {
  const [open, setOpen] = useState<VisionRegionOut | null>(null)
  const [showAll, setShowAll] = useState(false)

  const status = vision?.status || ''
  const meta = STATUS_LABEL[status] || { text: status || 'Not analysed by vision', tone: 'warn' as const }
  const byImage = useMemo(() => new Map(images.map((i) => [i.id, i])), [images])

  const regions = vision?.regions || []
  if (!vision || (!regions.length && !vision.note)) {
    return (
      <div className="empty">
        <div className="headline">No vision analysis recorded for this inspection</div>
        <div className="how">
          The inspection predates the on-device vision engine, or the images could not be decoded. Reprocess it to
          run the visual analysis over the original images.
        </div>
      </div>
    )
  }

  const textBlocks = regions.filter((r) => r.kind === 'TEXT_BLOCK')
  const other = regions.filter((r) => r.kind !== 'TEXT_BLOCK')
  const shown = showAll ? regions : [...textBlocks.slice(0, 2), ...other]

  const openImage = open?.image_id != null ? byImage.get(open.image_id) : undefined
  const openBox = open ? parseBbox(open.bbox) : null
  const openRect =
    openImage && openBox
      ? {
          left: `${(openBox[0] / openImage.width) * 100}%`,
          top: `${(openBox[1] / openImage.height) * 100}%`,
          width: `${((openBox[2] - openBox[0]) / openImage.width) * 100}%`,
          height: `${((openBox[3] - openBox[1]) / openImage.height) * 100}%`,
        }
      : null
  const openCrop = open
    ? (evidence || []).find((e) => e.related_type === 'vision' && e.image_id === open.image_id && e.bbox === open.bbox)
    : undefined

  return (
    <div>
      <div className="vision-head">
        <span className={`badge ${meta.tone === 'ok' ? 'PASS' : meta.tone === 'warn' ? 'UNCERTAIN' : 'FAIL'}`}>
          <span className="bdot" aria-hidden="true">●</span>
          {meta.text}
        </span>
        <span className="chip mono">{vision.engine || 'engine not recorded'}</span>
        {vision.provider_status && (
          <span className="chip">
            Provider: {vision.provider_status.replace(/_/g, ' ').toLowerCase()}
          </span>
        )}
      </div>
      {vision.note && <p className="muted" style={{ marginTop: 8 }}>{vision.note}</p>}
      {vision.provider_note && (
        <p className="muted" style={{ marginTop: 4 }}>
          <b>Provider note:</b> {vision.provider_note}
        </p>
      )}

      <table className="tbl" style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th>Observation</th>
            <th>Image</th>
            <th>Measured</th>
            <th>Text in region</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => {
            const img = r.image_id != null ? byImage.get(r.image_id) : undefined
            return (
              <tr key={r.id}>
                <td>
                  <b>{KIND_LABEL[r.kind] || r.kind}</b>
                  <div className="muted">{r.label}</div>
                </td>
                <td className="muted">{img ? img.role.replace(/_/g, ' ') : '—'}</td>
                <td className="muted">
                  {r.prominence > 0 && <>prominence {r.prominence.toFixed(2)} · </>}
                  {r.contrast > 0 && <>contrast {r.contrast.toFixed(2)} · </>}
                  {r.sharpness > 0 && <>sharpness {Math.round(r.sharpness)} · </>}
                  {r.text_density > 0 && <>ink {r.text_density.toFixed(3)}</>}
                </td>
                <td className="mono" style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {r.text ? r.text.split('\n')[0].slice(0, 60) : '—'}
                </td>
                <td>
                  {r.bbox ? (
                    <button className="btn sm secondary" onClick={() => setOpen(r)}>
                      View evidence
                    </button>
                  ) : (
                    <span className="muted">whole image</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {!showAll && regions.length > shown.length && (
        <button className="btn sm secondary mt" onClick={() => setShowAll(true)}>
          Show all {regions.length} observations
        </button>
      )}

      {open && (
        <Modal title={`Vision observation — ${KIND_LABEL[open.kind] || open.kind}`} onClose={() => setOpen(null)}>
          {openImage ? (
            <>
              <div className="ev-stage">
                <img src={api.fileUrl('originals', openImage.stored_filename)} alt={openImage.original_filename} />
                {openRect && <div className="rect" style={openRect} aria-hidden="true" />}
              </div>
              <div className="kv mt">
                <dt>Image</dt>
                <dd>{openImage.role.replace(/_/g, ' ')} · {openImage.original_filename}</dd>
                <dt>Region box</dt>
                <dd className="mono">{open.bbox}</dd>
                <dt>Engine</dt>
                <dd className="mono">{open.engine || vision.engine}</dd>
              </div>
              {openCrop && (
                <div className="mt">
                  <div className="muted">Retained crop</div>
                  <img
                    className="evidence-crop"
                    src={api.fileUrl('crops', openCrop.stored_filename)}
                    alt="Vision region crop"
                  />
                </div>
              )}
              <p className="muted mt">{open.note}</p>
              {open.text && <p className="mono mt">{open.text}</p>}
            </>
          ) : (
            <p className="muted">
              This observation is an image-level measurement, not a region — there is nothing to highlight.
            </p>
          )}
        </Modal>
      )}
    </div>
  )
}

/** A compact one-line summary used on the scan workspace. */
export function VisionStatusLine({ vision, durationMs }: { vision?: VisionPayload; durationMs?: number }) {
  if (!vision) return null
  const meta = STATUS_LABEL[vision.status] || { text: vision.status, tone: 'warn' as const }
  const badge = vision.regions.filter((r) => r.kind === 'LEGIBILITY').length
  return (
    <div className="flex" style={{ alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <StatusBadge value={meta.tone === 'ok' ? 'PASS' : meta.tone === 'warn' ? 'UNCERTAIN' : 'FAIL'} />
      <span className="muted">
        {meta.text}
        {vision.regions.length > 0 && ` · ${vision.regions.length} observation${vision.regions.length === 1 ? '' : 's'}`}
        {badge > 0 && ' · readability measured'}
        {durationMs ? ` · ${(durationMs / 1000).toFixed(1)} s total` : ''}
      </span>
    </div>
  )
}
