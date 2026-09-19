import { useEffect, useState } from 'react'
import { api, probeBackend, type SystemStatus } from '../services/api'
import { Icon } from '../components/Icon'
import type { LoginResponse } from '../types'

type BackendState = 'checking' | 'ok' | 'degraded' | 'down'
type Step = 'credentials' | 'mfa' | 'reset'

/**
 * Role selection on this screen is only a ROUTING HINT — the server decides the user's real role
 * from the account record and routes accordingly. Selecting a role here can never grant access to
 * it, which is why an unrecognised choice simply falls back to the server's answer.
 */
const ROLE_HINTS = [
  { value: '', label: 'Detect automatically' },
  { value: 'ENFORCEMENT_OFFICER', label: 'Enforcement Officer (regulatory)' },
  { value: 'REGULATED_ENTITY', label: 'Regulated Entity (business)' },
  { value: 'INTERNAL_COMPLIANCE', label: 'Internal Compliance (corporate)' },
  { value: 'INSPECTOR', label: 'Inspector (internal)' },
  { value: 'ADMIN', label: 'Administrator' },
  { value: 'VIEWER', label: 'Viewer' },
]

export default function Login({ onLogin }: { onLogin: (r: LoginResponse) => void }) {
  const [step, setStep] = useState<Step>('credentials')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [roleHint, setRoleHint] = useState('')
  const [mfaToken, setMfaToken] = useState('')
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState('')
  const [useRecovery, setUseRecovery] = useState(false)
  const [resetId, setResetId] = useState('')
  const [resetToken, setResetToken] = useState('')
  const [resetPassword, setResetPassword] = useState('')
  const [resetDelivery, setResetDelivery] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [backend, setBackend] = useState<BackendState>('checking')
  const [status, setStatus] = useState<SystemStatus | null>(null)

  const checkBackend = async () => {
    setBackend('checking')
    const s = await probeBackend()
    setStatus(s)
    setBackend(s === null ? 'down' : s.status === 'ok' ? 'ok' : 'degraded')
  }

  useEffect(() => {
    void checkBackend()
  }, [])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const resp = await api.login(username.trim(), password, roleHint)
      if (resp.mfa_required && resp.mfa_token) {
        setMfaToken(resp.mfa_token)
        setStep('mfa')
        setCode('')
      } else {
        onLogin(resp)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
      void checkBackend()
    } finally {
      setBusy(false)
    }
  }

  async function verifyMfa(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const resp = await api.mfaVerify(mfaToken, useRecovery ? '' : code, useRecovery ? recovery : '')
      onLogin(resp)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Verification failed')
    } finally {
      setBusy(false)
    }
  }

  async function requestReset() {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const res = await api.requestPasswordReset(resetId.trim())
      setNotice(res.message)
      if (res.reset_token) {
        setResetToken(res.reset_token)
        setResetDelivery(res.delivery || 'demo')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the reset')
    } finally {
      setBusy(false)
    }
  }

  async function confirmReset() {
    setBusy(true)
    setError('')
    try {
      const res = await api.confirmPasswordReset(resetToken.trim(), resetPassword)
      setNotice(res.message)
      setStep('credentials')
      setResetToken('')
      setResetPassword('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reset the password')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <span
            aria-hidden="true"
            style={{ width: 38, height: 38, borderRadius: 10, background: 'var(--accent)', color: '#fff', display: 'grid', placeItems: 'center' }}
          >
            <Icon name="scan-line" size={20} />
          </span>
          <h1 style={{ margin: 0 }}>PACKCHECK <span style={{ color: 'var(--accent)' }}>AI</span></h1>
        </div>
        <div className="sub">AI-Assisted Legal Metrology Inspection System</div>
        <div className="tagline" style={{ marginBottom: 16 }}>Scan → Extract → Verify → Alert → Report</div>

        {backend !== 'ok' && (
          <div className={backend === 'down' ? 'alert error' : 'alert warn'} role="status">
            {backend === 'checking' ? (
              'Checking backend connection…'
            ) : backend === 'down' ? (
              <>
                <strong>Backend unreachable.</strong> Sign-in cannot work until the API is running.
                Start it with <code>start.bat</code>, or run{' '}
                <code>.venv\Scripts\python -m uvicorn backend.main:app --port 8001</code> from the project
                folder.
                <button type="button" className="btn secondary sm" style={{ marginTop: 8 }} onClick={checkBackend}>
                  Retry connection
                </button>
              </>
            ) : (
              <>
                <strong>Backend degraded.</strong> The API answered, but not every subsystem is
                available:{' '}
                {status
                  ? Object.entries(status.components)
                      .filter(([, ok]) => !ok)
                      .map(([name]) => name)
                      .join(', ') || 'unknown'
                  : 'unknown'}
                . Extraction quality may be affected.
              </>
            )}
          </div>
        )}

        {step === 'credentials' && (
          <form onSubmit={submit}>
            <div className="field">
              <label htmlFor="login-user">Username or email</label>
              <input
                id="login-user"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
              />
            </div>
            <div className="field">
              <label htmlFor="login-pass">Password</label>
              <input
                id="login-pass"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </div>
            <div className="field">
              <label htmlFor="login-role">I am signing in as (routing hint)</label>
              <select id="login-role" value={roleHint} onChange={(e) => setRoleHint(e.target.value)}>
                {ROLE_HINTS.map((r) => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </select>
              <p className="muted" style={{ marginTop: 6, fontSize: 11.5 }}>
                Used only to take you to the right portal. The server still decides your actual role.
              </p>
            </div>
            {error && <div className="alert error">{error}</div>}
            {notice && <div className="alert success">{notice}</div>}
            <button className="btn" style={{ width: '100%' }} disabled={busy || !username || !password}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
            <button
              type="button"
              className="btn ghost sm"
              style={{ width: '100%', marginTop: 8 }}
              onClick={() => { setStep('reset'); setError(''); setNotice('') }}
            >
              Forgot password?
            </button>
          </form>
        )}

        {step === 'mfa' && (
          <form onSubmit={verifyMfa}>
            <div className="alert info" role="status">
              <strong>Two-factor authentication.</strong> Enter the current 6-digit code from your
              authenticator app to finish signing in.
            </div>
            {useRecovery ? (
              <div className="field">
                <label htmlFor="login-recovery">Recovery code</label>
                <input id="login-recovery" value={recovery} onChange={(e) => setRecovery(e.target.value)} autoFocus />
              </div>
            ) : (
              <div className="field">
                <label htmlFor="login-code">Authenticator code</label>
                <input
                  id="login-code"
                  value={code}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  onChange={(e) => setCode(e.target.value)}
                  autoFocus
                />
              </div>
            )}
            {error && <div className="alert error">{error}</div>}
            <button
              className="btn"
              style={{ width: '100%' }}
              disabled={busy || (useRecovery ? !recovery : !code)}
            >
              {busy ? 'Verifying…' : 'Verify and sign in'}
            </button>
            <button type="button" className="btn ghost sm" style={{ width: '100%', marginTop: 8 }}
              onClick={() => { setUseRecovery(!useRecovery); setError('') }}>
              {useRecovery ? 'Use an authenticator code instead' : 'Use a recovery code instead'}
            </button>
            <button type="button" className="btn ghost sm" style={{ width: '100%' }}
              onClick={() => { setStep('credentials'); setError('') }}>
              Back
            </button>
          </form>
        )}

        {step === 'reset' && (
          <form onSubmit={(e) => { e.preventDefault(); resetToken ? void confirmReset() : void requestReset() }}>
            <div className="field">
              <label htmlFor="reset-id">Username or email</label>
              <input id="reset-id" value={resetId} onChange={(e) => setResetId(e.target.value)} autoFocus />
            </div>
            {notice && <div className="alert info">{notice}</div>}
            {resetToken && (
              <>
                <div className="alert warn">
                  <strong>No email provider is configured in this deployment.</strong>{' '}
                  {resetDelivery === 'demo'
                    ? 'A one-time reset token is shown here so the flow can be completed (demo mode).'
                    : 'The token has been delivered out of band.'}
                </div>
                <div className="field">
                  <label htmlFor="reset-token">Reset token</label>
                  <input id="reset-token" value={resetToken} onChange={(e) => setResetToken(e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="reset-new">New password</label>
                  <input
                    id="reset-new"
                    type="password"
                    value={resetPassword}
                    onChange={(e) => setResetPassword(e.target.value)}
                    autoComplete="new-password"
                  />
                </div>
              </>
            )}
            {error && <div className="alert error">{error}</div>}
            <button className="btn" style={{ width: '100%' }} disabled={busy || !resetId}>
              {busy ? 'Working…' : resetToken ? 'Set new password' : 'Request reset'}
            </button>
            <button type="button" className="btn ghost sm" style={{ width: '100%', marginTop: 8 }}
              onClick={() => { setStep('credentials'); setError(''); setNotice(''); setResetToken('') }}>
              Back to sign in
            </button>
          </form>
        )}

        <div className="disclaimer">
          Inspection-assistance software (SIH26034). AI perceives → Python validates → Rules decide →
          Evidence explains. It does not replace an authorized Legal Metrology officer or formal
          physical inspection.
          <details style={{ marginTop: 8 }}>
            <summary style={{ cursor: 'pointer', fontSize: 11 }}>Demo credentials</summary>
            <div style={{ marginTop: 6 }}>
              admin / admin123 · inspector / inspector123 · viewer / viewer123
              <br />
              officer / officer123 (enforcement) · entity / entity123 (regulated entity) · compliance
              / compliance123 (internal compliance)
            </div>
          </details>
        </div>
      </div>
    </div>
  )
}
