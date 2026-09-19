import { useEffect, useState } from 'react'
import { api, getRole, hasPermission } from '../services/api'
import { Card } from '../components/Badges'
import { Icon } from '../components/Icon'
import { MigrationCard } from '../components/ReprocessPanel'
import { Skeleton } from '../components/ui'
import type { AIStatus } from '../types'

export default function Settings() {
  const [root, setRoot] = useState<{ name?: string; version?: string; ocr_available?: boolean } | null>(null)
  const [ai, setAi] = useState<AIStatus | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api
      .get<{ name: string; version: string; ocr_available: boolean }>('/status')
      .then(setRoot)
      .catch(() => setRoot(null))
      .finally(() => setLoading(false))
    api.aiStatus().then(setAi).catch(() => setAi(null))
  }, [])

  const role = getRole()

  return (
    <>
      <div className="page-title">Settings</div>
      <div className="page-sub">System information, access model, analysis provenance and documented limitations.</div>

      {/* Migration control: regenerate existing inspections that were analysed by an older engine.
          The bulk run rewrites many records at once, so it is shown only to the administrative role
          that may start it (the API enforces the same capability). */}
      {hasPermission('users.manage') ? (
        <MigrationCard />
      ) : (
        <Card title="Reprocessing &amp; migration">
          <p className="muted" style={{ fontSize: 12.5 }}>
            Bulk reprocessing of existing inspections is an administrative action. Individual
            inspections can be regenerated from their own page by an inspector, and every
            regeneration is recorded in that inspection&apos;s audit history.
          </p>
        </Card>
      )}

      <Card title="System">
        {loading ? (
          <Skeleton lines={4} />
        ) : (
        <dl className="kv">
          <dt>Application</dt><dd>PACKCHECK AI — AI-Assisted Legal Metrology Inspection System</dd>
          <dt>Problem statement</dt><dd>SIH26034 — Legal Metrology (Packaged Commodities) Rules, 2011</dd>
          <dt>Version</dt><dd>{root?.version || '—'}</dd>
          <dt>OCR engine</dt><dd>{root?.ocr_available ? 'RapidOCR (local, offline) — primary reader' : 'unavailable'}</dd>
          <dt>Deterministic rules</dt><dd>In-process, versioned rule library — the only component that decides compliance</dd>
          <dt>Your role</dt><dd>{role}</dd>
        </dl>
        )}
      </Card>

      {/* AI configuration is environment-driven; the browser never holds a provider key. */}
      <Card title="AI extraction configuration">
        {!ai ? (
          <Skeleton lines={3} />
        ) : (
          <>
            <div className={ai.enabled ? 'alert success' : 'alert info'} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <Icon name={ai.enabled ? 'sparkles' : 'cpu'} />
              <span>
                <b>{ai.enabled ? 'Vision provider configured' : 'On-device OCR only'}</b>
                <div style={{ marginTop: 4 }}>{ai.reason}</div>
              </span>
            </div>
            <dl className="kv">
              <dt>Provider</dt><dd>{ai.provider}</dd>
              <dt>Model</dt><dd className="ident">{ai.model}</dd>
              <dt>Mode</dt><dd>{ai.mode === 'vision' ? 'AI vision + on-device OCR' : 'on-device OCR'}</dd>
              <dt>Key location</dt><dd>Server environment only (<code>GEMINI_API_KEY</code>) — never exposed to this browser</dd>
              <dt>Fields a provider may report</dt><dd>{ai.fields.length} tracked declarations</dd>
            </dl>
            <p className="muted" style={{ marginTop: 12, lineHeight: 1.6 }}>
              To enable vision extraction, set <code>GEMINI_API_KEY</code> (and optionally{' '}
              <code>AI_MODEL</code>) in the server environment and restart the backend. Provider
              readings enter the pipeline as candidates: they are validated, ranked against the OCR
              evidence and marked for human review when the two disagree — they never decide
              compliance.
            </p>
          </>
        )}
      </Card>

      <Card title="Role permissions">
        <table className="tbl">
          <thead>
            <tr>
              <th>Capability</th>
              <th>Admin</th><th>Inspector</th><th>Viewer</th>
              <th>Enforcement</th><th>Entity</th><th>Internal</th>
            </tr>
          </thead>
          <tbody>
            <tr><td>View inspections, products, reports</td><td>✓</td><td>✓</td><td>✓</td><td>✓</td><td>own org</td><td>own org</td></tr>
            <tr><td>Create inspections / upload images</td><td>✓</td><td>✓</td><td>—</td><td>✓</td><td>—</td><td>—</td></tr>
            <tr><td>Review, correct fields, finalize decisions</td><td>✓</td><td>✓</td><td>—</td><td>✓</td><td>—</td><td>—</td></tr>
            <tr><td>Enforcement records &amp; flagged products</td><td>✓</td><td>✓</td><td>view</td><td>✓</td><td>—</td><td>—</td></tr>
            <tr><td>Own-organization compliance submissions</td><td>✓</td><td>—</td><td>—</td><td>—</td><td>✓</td><td>—</td></tr>
            <tr><td>Internal compliance review workflows</td><td>✓</td><td>—</td><td>—</td><td>—</td><td>—</td><td>✓</td></tr>
            <tr><td>Generate PDF reports</td><td>✓</td><td>✓</td><td>—</td><td>✓</td><td>—</td><td>✓</td></tr>
            <tr><td>Read the audit trail</td><td>✓</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>
          </tbody>
        </table>
        <p className="muted" style={{ marginTop: 10, fontSize: 12, lineHeight: 1.5 }}>
          Roles are decided by the server from the account record. Selecting a role at sign-in is a
          routing hint only, and changing a URL never grants a capability.
        </p>
      </Card>

      <Card title="Documented limitations">
        <ul style={{ margin: '0 0 0 18px', lineHeight: 1.7, fontSize: 13 }}>
          <li>An image can show a declaration but cannot prove the physical quantity inside the package.</li>
          <li>The actual price charged at sale cannot be verified from a label image.</li>
          <li>Physical font height in millimetres requires scale calibration — images provide pixels only.</li>
          <li>Declaration authenticity and transaction-level facts are not verifiable by image.</li>
          <li>Missing OCR evidence is <b>not</b> proof of a missing declaration — uncertain cases route to manual review.</li>
          <li>PACKCHECK AI is inspection-assistance software, not a legal authority or a replacement for an authorized inspector.</li>
          <li>Vision-model readings that on-device OCR cannot corroborate are offered for review, never published as detected facts.</li>
          <li>A “duration” best-before (e.g. 12 months) is only converted into a date when a date to count it from exists.</li>
        </ul>
      </Card>
    </>
  )
}
