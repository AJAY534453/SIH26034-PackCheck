import { useEffect, useState } from 'react'
import { api, clearAuth, getUsername, getRole, getUserLabel } from '../services/api'
import { Card } from '../components/Badges'
import { Skeleton, StatusBadge, useToast } from '../components/ui'
import { Icon } from '../components/Icon'
import type { SessionInfo, User } from '../types'

const ROLE_LABELS: Record<string, string> = {
  ADMIN: 'Administrator',
  INSPECTOR: 'Inspector',
  VIEWER: 'Viewer',
  ENFORCEMENT_OFFICER: 'Enforcement Officer',
  REGULATED_ENTITY: 'Regulated Entity',
  INTERNAL_COMPLIANCE: 'Internal Compliance',
}

export default function Account() {
  const [me, setMe] = useState<User | null>(null)
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const { show, node: toast } = useToast()

  // password change
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')

  // MFA
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null)
  const [code, setCode] = useState('')
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([])

  function load() {
    api.me().then(setMe).catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
    api.sessions().then((d) => setSessions(d.items)).catch(() => setSessions([]))
    setLoading(false)
  }
  useEffect(load, [])

  async function changePassword() {
    if (next !== confirm) {
      setError('The new password and its confirmation do not match.')
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.changePassword(current, next)
      clearAuth()
      window.location.assign('/login')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not change the password')
    } finally {
      setBusy(false)
    }
  }

  async function beginSetup() {
    setBusy(true)
    setError('')
    try {
      setSetup(await api.mfaSetup())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start MFA setup')
    } finally {
      setBusy(false)
    }
  }

  async function enableMfa() {
    setBusy(true)
    setError('')
    try {
      const res = await api.mfaEnable(code)
      setRecoveryCodes(res.recovery_codes)
      setSetup(null)
      setCode('')
      load()
      show('Two-factor authentication enabled')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not enable MFA')
    } finally {
      setBusy(false)
    }
  }

  async function disableMfa() {
    setBusy(true)
    setError('')
    try {
      await api.mfaDisable(code)
      setCode('')
      setRecoveryCodes([])
      load()
      show('Two-factor authentication disabled')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not disable MFA')
    } finally {
      setBusy(false)
    }
  }

  async function revoke(sid: string) {
    try {
      await api.revokeSession(sid)
      load()
      show('Session ended')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not end the session')
    }
  }

  async function logoutAll() {
    try {
      await api.logoutAll()
    } catch {
      /* ignore */
    }
    clearAuth()
    window.location.assign('/login')
  }

  if (loading) return <Card><Skeleton lines={6} /></Card>

  return (
    <>
      <div className="page-title">Account &amp; Security</div>
      <div className="page-sub">
        Manage your password, two-factor authentication and signed-in sessions. Every change here is
        recorded in the audit trail.
      </div>

      {error && <div className="alert error">{error}</div>}
      {toast}

      <Card title="Profile">
        <dl className="kv">
          <dt>Name</dt><dd>{getUserLabel()}</dd>
          <dt>Username</dt><dd className="mono">{getUsername()}</dd>
          <dt>Email</dt><dd>{me?.email || '—'}</dd>
          <dt>Role</dt><dd>{ROLE_LABELS[getRole() || me?.role || ''] ?? getRole()}</dd>
          <dt>Account status</dt><dd><StatusBadge value={me?.status ?? 'ACTIVE'} /></dd>
          <dt>Organization</dt><dd>{me?.organization ? `${me.organization.name} (${me.organization.kind})` : '— none —'}</dd>
          <dt>Permissions</dt><dd className="mono" style={{ fontSize: 11.5 }}>{(me?.permissions ?? []).join(', ')}</dd>
        </dl>
      </Card>

      <div className="grid cols-2">
        <Card title="Change password">
          <div className="field">
            <label htmlFor="cp-current">Current password</label>
            <input id="cp-current" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
          </div>
          <div className="field">
            <label htmlFor="cp-new">New password</label>
            <input id="cp-new" type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
          </div>
          <div className="field">
            <label htmlFor="cp-confirm">Confirm new password</label>
            <input id="cp-confirm" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
          </div>
          <button className="btn" onClick={changePassword} disabled={busy || !current || !next}>
            <Icon name="shield-check" /> Update password
          </button>
          <p className="muted" style={{ marginTop: 10, fontSize: 12, lineHeight: 1.5 }}>
            Changing your password signs out every device, including this one. Minimum length and a
            common-password check are enforced on the server.
          </p>
        </Card>

        <Card title="Two-factor authentication">
          {recoveryCodes.length > 0 && (
            <div className="alert warn">
              <b>Save these recovery codes now.</b> They are shown once and each works a single time.
              <div className="mono" style={{ marginTop: 8, fontSize: 12.5, lineHeight: 1.6 }}>
                {recoveryCodes.map((c) => <div key={c}>{c}</div>)}
              </div>
            </div>
          )}

          {me?.mfa_enabled ? (
            <>
              <div className="alert success"><b>Enabled.</b> A code from your authenticator app is required at sign-in.</div>
              <div className="field">
                <label htmlFor="mfa-code-off">Enter a code to disable MFA</label>
                <input id="mfa-code-off" value={code} inputMode="numeric" onChange={(e) => setCode(e.target.value)} />
              </div>
              <div className="flex">
                <button className="btn danger" onClick={disableMfa} disabled={busy || !code}>Disable MFA</button>
                <button className="btn secondary" onClick={async () => {
                  try { setRecoveryCodes((await api.mfaRecoveryCodes(code)).recovery_codes); show('New recovery codes issued') }
                  catch (e) { setError(e instanceof Error ? e.message : 'Could not issue codes') }
                }} disabled={busy || !code}>Reissue recovery codes</button>
              </div>
            </>
          ) : setup ? (
            <>
              <p className="muted">
                Add this secret to your authenticator app (Google Authenticator, Authy, 1Password),
                then enter the current code to confirm.
              </p>
              <dl className="kv">
                <dt>Secret</dt><dd className="mono" style={{ fontSize: 12 }}>{setup.secret}</dd>
              </dl>
              <div className="field">
                <label htmlFor="mfa-code-on">Authenticator code</label>
                <input id="mfa-code-on" value={code} inputMode="numeric" onChange={(e) => setCode(e.target.value)} />
              </div>
              <div className="flex">
                <button className="btn" onClick={enableMfa} disabled={busy || !code}>Confirm &amp; enable</button>
                <button className="btn secondary" onClick={() => { setSetup(null); setCode('') }}>Cancel</button>
              </div>
            </>
          ) : (
            <>
              <p className="muted">
                MFA is optional but strongly recommended. It uses TOTP, so it works offline with any
                authenticator app — no SMS and no email required.
              </p>
              <button className="btn" onClick={beginSetup} disabled={busy}>
                <Icon name="shield-check" /> Set up authenticator
              </button>
            </>
          )}
        </Card>
      </div>

      <Card title="Signed-in sessions" right={<button className="btn sm secondary" onClick={logoutAll}>Sign out everywhere</button>}>
        <table className="tbl">
          <thead><tr><th>Session</th><th>Started</th><th>Last used</th><th>IP</th><th>Device</th><th>MFA</th><th></th></tr></thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.session_id}>
                <td className="mono">{s.session_id.slice(0, 10)}… {s.current && <span className="badge PASS"><span className="bdot" aria-hidden="true">✓</span>this device</span>}</td>
                <td>{s.created_at.slice(0, 19).replace('T', ' ')}</td>
                <td>{s.last_used_at ? s.last_used_at.slice(0, 19).replace('T', ' ') : '—'}</td>
                <td className="mono">{s.ip_address || '—'}</td>
                <td className="muted" style={{ maxWidth: 240, overflowWrap: 'anywhere' }}>{s.user_agent || '—'}</td>
                <td>{s.mfa_verified ? 'yes' : 'no'}</td>
                <td>
                  {s.revoked
                    ? <span className="muted">ended ({s.revoke_reason})</span>
                    : <button className="btn sm secondary" onClick={() => revoke(s.session_id)}>End</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  )
}
