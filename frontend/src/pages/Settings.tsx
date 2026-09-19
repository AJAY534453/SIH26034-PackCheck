import { useEffect, useRef, useState } from 'react'
import { api, getRole, hasPermission } from '../services/api'
import { Card } from '../components/Badges'
import { Icon } from '../components/Icon'
import { MigrationCard } from '../components/ReprocessPanel'
import { Skeleton } from '../components/ui'
import type { AIStatus, VisionHealth, VisionTestResult } from '../types'

export default function Settings() {
  const [root, setRoot] = useState<{ name?: string; version?: string; ocr_available?: boolean } | null>(null)
  const [ai, setAi] = useState<AIStatus | null>(null)
  const [vision, setVision] = useState<VisionHealth | null>(null)
  const [test, setTest] = useState<VisionTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [testError, setTestError] = useState('')
  const [loading, setLoading] = useState(true)
  const testInput = useRef<HTMLInputElement>(null)

  function loadVision() {
    api.visionHealth().then(setVision).catch(() => setVision(null))
  }

  useEffect(() => {
    api
      .get<{ name: string; version: string; ocr_available: boolean }>('/status')
      .then(setRoot)
      .catch(() => setRoot(null))
      .finally(() => setLoading(false))
    api.aiStatus().then(setAi).catch(() => setAi(null))
    loadVision()
  }, [])

  /** Run both perception sources over one image, on demand. The response is the evidence. */
  async function runVisionTest(file: File | undefined) {
    if (!file) return
    setTesting(true)
    setTestError('')
    setTest(null)
    try {
      setTest(await api.visionTest(file))
      loadVision()
    } catch (e) {
      setTestError(e instanceof Error ? e.message : 'The vision self-test could not run')
    } finally {
      setTesting(false)
      if (testInput.current) testInput.current.value = ''
    }
  }

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
              The <b>on-device vision engine</b> ({vision?.engine || 'on-device-vision'}) always runs — it needs no
              key and no network, and it is what makes vision analysis part of every scan. Setting{' '}
              <code>GEMINI_API_KEY</code> (and optionally <code>AI_MODEL</code>) in the server environment adds the
              optional provider on top: its readings enter the pipeline as candidates, are validated and ranked
              against the OCR evidence, and are marked for human review when the two disagree. Neither source ever
              decides compliance.
            </p>
          </>
        )}
      </Card>

      {/* Vision perception health: the on-device engine is checked live (no network), and the
          optional provider is verified by an actual call — never reported as reachable on the
          strength of a config flag alone. */}
      <Card title="Vision engine health">
        {!vision ? (
          <Skeleton lines={3} />
        ) : (
          <>
            <div className={vision.status === 'OK' ? 'alert success' : 'alert info'} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <Icon name={vision.on_device.available ? 'sparkles' : 'cpu'} />
              <span>
                <b>
                  VISION ENGINE {vision.on_device.available ? 'ONLINE' : 'OFFLINE'} — {vision.engine}
                </b>
                <div style={{ marginTop: 4 }}>{vision.on_device.detail}</div>
              </span>
            </div>
            <dl className="kv">
              <dt>Always-on engine</dt><dd>{vision.engine} (local, offline, no API key)</dd>
              <dt>Provider</dt><dd>{vision.provider} · {vision.model}</dd>
              <dt>Provider configured</dt><dd>{vision.configured ? 'yes' : 'no — on-device vision still runs'}</dd>
              <dt>Provider reachability</dt>
              <dd className="muted">
                {vision.reachable === null ? vision.reachable_note : vision.reachable ? 'reachable' : 'unreachable'}
              </dd>
              <dt>Last vision run</dt>
              <dd>
                {vision.last_successful_run ? (
                  <>
                    {vision.last_successful_run.inspection_number} ·{' '}
                    {vision.last_successful_run.vision_status.replace(/_/g, ' ').toLowerCase()} ·{' '}
                    {vision.last_successful_run.vision_ms != null
                      ? `${(vision.last_successful_run.vision_ms / 1000).toFixed(1)} s vision`
                      : 'timing not recorded'}
                  </>
                ) : (
                  <span className="muted">No scan has recorded a perception status yet.</span>
                )}
              </dd>
              {vision.last_error && (
                <>
                  <dt>Last error</dt><dd className="muted">{vision.last_error}</dd>
                </>
              )}
            </dl>

            <div className="mt">
              <input
                ref={testInput}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                hidden
                onChange={(e) => runVisionTest(e.target.files?.[0])}
              />
              <button className="btn" onClick={() => testInput.current?.click()} disabled={testing}>
                <Icon name="scan-line" /> {testing ? 'Testing…' : 'Run a live vision self-test'}
              </button>
              <span className="muted" style={{ marginLeft: 10 }}>
                Upload one package image — both sources are exercised and the result is returned with
                its latency and any error.
              </span>
            </div>
            {testError && <div className="alert error mt">{testError}</div>}
            {test && (
              <div className="mt">
                <div className="muted">Self-test result</div>
                <dl className="kv">
                  <dt>On-device</dt>
                  <dd>
                    {test.on_device.status || '—'}
                    {test.on_device.latency_ms != null && ` · ${(test.on_device.latency_ms / 1000).toFixed(2)} s`}
                    {test.on_device.regions ? ` · ${test.on_device.regions.length} region(s)` : ''}
                    {test.on_device.readability ? ` · readability ${test.on_device.readability.replace(/^VISUALLY_/, '').toLowerCase()}` : ''}
                  </dd>
                  <dt>OCR lines used</dt><dd>{test.on_device.ocr_lines_used ?? 0}</dd>
                  <dt>Provider call</dt>
                  <dd>
                    {test.provider_call.attempted
                      ? `${test.provider_call.status}${test.provider_call.latency_ms != null ? ` · ${(test.provider_call.latency_ms / 1000).toFixed(2)} s` : ''} · ${test.provider_call.observations} observation(s)`
                      : `not attempted — ${test.provider_call.detail || 'no provider configured'}`}
                  </dd>
                  {test.on_device.metrics && (
                    <>
                      <dt>Readability metrics</dt>
                      <dd className="mono">
                        sharpness {test.on_device.metrics.sharpness} · contrast {test.on_device.metrics.contrast} · glare{' '}
                        {(test.on_device.metrics.glare * 100).toFixed(2)}%
                      </dd>
                    </>
                  )}
                  {test.on_device.hero_text && (
                    <>
                      <dt>Most prominent block</dt><dd className="mono">{test.on_device.hero_text}</dd>
                    </>
                  )}
                </dl>
                {test.errors.length > 0 && (
                  <div className="alert error mt">
                    {test.errors.map((e) => (
                      <div key={e}>{e}</div>
                    ))}
                  </div>
                )}
              </div>
            )}
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
