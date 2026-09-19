import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  api,
  clearAuth,
  getHome,
  getRole,
  getUserLabel,
  hasPermission,
  isLoggedIn,
  setAuth,
  setUser,
} from './services/api'
import { Icon } from './components/Icon'
import { EmptyState } from './components/ui'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import EnforcementDashboard from './pages/EnforcementDashboard'
import EntityDashboard from './pages/EntityDashboard'
import InternalDashboard from './pages/InternalDashboard'
import NewInspection from './pages/NewInspection'
import Inspections from './pages/Inspections'
import InspectionDetail from './pages/InspectionDetail'
import Products from './pages/Products'
import Violations from './pages/Violations'
import Reports from './pages/Reports'
import RuleLibrary from './pages/RuleLibrary'
import Settings from './pages/Settings'
import Account from './pages/Account'
import AuditLog from './pages/AuditLog'
import BillScanner from './pages/BillScanner'
import Grocery from './pages/Grocery'
import GroceryDetail from './pages/GroceryDetail'
import ComplaintCenter from './pages/ComplaintCenter'
import Analysis from './pages/Analysis'
import Repository from './pages/Repository'
import ScanDetail from './pages/ScanDetail'
import Listings from './pages/Listings'
import ProductHistory from './pages/ProductHistory'
import { Assistant } from './components/Assistant'

/** Page context for the top bar — static titles only, no fabricated status. */
const PAGE_TITLES: Record<string, string> = {
  '/dashboard': 'Dashboard',
  '/enforcement': 'Enforcement Dashboard',
  '/entity': 'Entity Dashboard',
  '/internal': 'Compliance Dashboard',
  '/inspections/new': 'New Inspection',
  '/inspections': 'Inspections',
  '/products': 'Products',
  '/violations': 'Violations',
  '/reports': 'Reports',
  '/repository': 'Compliance Repository',
  '/analysis': 'Image Analysis',
  '/bills': 'Bill Scanner',
  '/grocery': 'Grocery',
  '/complaints': 'Complaint Center',
  '/rules': 'Rule Library',
  '/audit': 'Audit Log',
  '/account': 'Account & Security',
  '/settings': 'Settings',
}

/**
 * Navigation is filtered by PERMISSION, not by a hard-coded role. The same permission names the
 * backend enforces are used here, so the menu reflects exactly what the account may do — and the
 * server rejects the operation regardless of what the menu shows.
 */
const NAV_GROUPS: { group: string; items: { to: string; label: string; icon: string; perm: string }[] }[] = [
  {
    group: 'Main',
    items: [
      { to: '/dashboard', label: 'Dashboard', icon: 'clipboard-list', perm: 'dashboard.view' },
      { to: '/enforcement', label: 'Enforcement Portal', icon: 'shield-check', perm: 'enforcement.view' },
      { to: '/entity', label: 'Entity Portal', icon: 'store', perm: 'entity.view' },
      { to: '/internal', label: 'Compliance Portal', icon: 'badge-check', perm: 'internal.view' },
      { to: '/inspections/new', label: 'New Inspection', icon: 'scan-line', perm: 'inspections.manage' },
      { to: '/inspections', label: 'Inspections', icon: 'layers', perm: 'inspections.view' },
      { to: '/repository', label: 'Compliance Repository', icon: 'history', perm: 'compliance.view' },
      { to: '/listings', label: 'Online Listings', icon: 'shopping-cart', perm: 'listings.use' },
      { to: '/products', label: 'Products', icon: 'package', perm: 'inspections.view' },
      { to: '/violations', label: 'Violations', icon: 'alert-triangle', perm: 'enforcement.view' },
      { to: '/reports', label: 'Reports', icon: 'file-text', perm: 'reports.view' },
    ],
  },
  {
    group: 'Tools',
    items: [
      { to: '/analysis', label: 'Image Analysis', icon: 'camera', perm: 'analysis.run' },
      { to: '/bills', label: 'Bill Scanner', icon: 'receipt', perm: 'tools.use' },
      { to: '/grocery', label: 'Grocery', icon: 'shopping-cart', perm: 'tools.use' },
      { to: '/complaints', label: 'Complaint Center', icon: 'message-square-warning', perm: 'tools.use' },
    ],
  },
  {
    group: 'Knowledge',
    items: [{ to: '/rules', label: 'Rule Library', icon: 'shield-check', perm: 'rules.view' }],
  },
  {
    group: 'System',
    items: [
      { to: '/audit', label: 'Audit Log', icon: 'list', perm: 'audit.view' },
      { to: '/account', label: 'Account & Security', icon: 'users', perm: 'dashboard.view' },
      { to: '/settings', label: 'Settings', icon: 'settings', perm: 'dashboard.view' },
    ],
  },
]

/** Renders a route only when the account holds the permission; otherwise a clear 403 panel. */
function Guard({ perm, children }: { perm: string; children: React.ReactNode }) {
  if (!hasPermission(perm)) {
    return (
      <div className="card">
        <EmptyState
          glyph="✕"
          headline="Not authorized for this area"
          how="Your role does not include this capability. Authorization is enforced on the server, so this page cannot be reached by editing the URL either."
          action={<a className="btn sm secondary" href={getHome()}>Go to my dashboard</a>}
        />
      </div>
    )
  }
  return <>{children}</>
}

function Shell({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()
  const role = getRole()
  const [aiMode, setAiMode] = useState<{ enabled: boolean; reason: string } | null>(null)

  const pageTitle =
    PAGE_TITLES[location.pathname] ??
    (location.pathname.startsWith('/inspections/')
      ? 'Inspection Detail'
      : location.pathname.startsWith('/products/')
        ? 'Product Detail'
        : location.pathname.startsWith('/grocery/')
          ? 'Product Details'
          : 'PACKCHECK AI')

  // The AI-mode pill states plainly whether a vision provider is contributing readings.
  useEffect(() => {
    api
      .aiStatus()
      .then((s) => setAiMode({ enabled: s.enabled, reason: s.reason }))
      .catch(() => setAiMode(null))
  }, [])

  async function logout() {
    try {
      await api.logout()
    } catch {
      /* the session may already be gone */
    }
    clearAuth()
    navigate('/login')
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="lockup">
            <span className="mark" aria-hidden="true">
              <Icon name="scan-line" size={18} />
            </span>
            <div>
              <div className="brand-name">
                PACKCHECK <span className="ai">AI</span>
              </div>
            </div>
          </div>
          <p>AI-Assisted Legal Metrology Inspection System</p>
          <span className="tagline">Scan → Extract → Verify → Alert → Report</span>
        </div>
        <nav className="nav" aria-label="Main navigation">
          {NAV_GROUPS.map((group) => {
            const items = group.items.filter((it) => hasPermission(it.perm))
            if (items.length === 0) return null
            return (
              <div key={group.group}>
                <div className="group">{group.group}</div>
                {items.map((it) => (
                  <NavLink key={it.to} to={it.to} end={it.to === '/inspections'}>
                    <Icon name={it.icon} size={16} />
                    <span className="lbl">{it.label}</span>
                  </NavLink>
                ))}
              </div>
            )
          })}
          <div className="spacer" />
          <div className="userbox">
            <div className="who">
              <b>{getUserLabel()}</b>
              {role}
            </div>
            <a href="#logout" onClick={(e) => { e.preventDefault(); logout() }}>
              <Icon name="log-out" size={16} />
              <span className="lbl">Logout</span>
            </a>
          </div>
        </nav>
      </aside>

      <header className="topbar">
        <span className="crumb"><b>{pageTitle}</b></span>
        <span className="spacer" />
        {aiMode && (
          <span
            className={`mode-pill ${aiMode.enabled ? 'vision' : 'offline'}`}
            title={aiMode.reason}
          >
            <Icon name={aiMode.enabled ? 'sparkles' : 'cpu'} size={13} />
            {aiMode.enabled ? 'AI vision + on-device OCR' : 'On-device OCR (no vision provider)'}
          </span>
        )}
        <span className="whoami">Signed in as <b>{getUserLabel()}</b></span>
        <span className="role-chip">{role}</span>
      </header>

      <main className="main">{children}</main>
      {/* The assistant is mounted once for the whole authenticated shell, so it is reachable from
          every page without duplicating a widget per route. */}
      <Assistant />
    </div>
  )
}

/**
 * Shown while the session cookie is being validated. Rendering the login form during this window
 * would flash a signed-out screen to an already-authenticated user (and invite a pointless re-login).
 */
function SessionBoot() {
  return (
    <div className="login-wrap">
      <div className="login-card">
        <h1 style={{ margin: 0 }}>PACKCHECK <span style={{ color: 'var(--accent)' }}>AI</span></h1>
        <div className="sub">Restoring your secure session…</div>
        <div className="tagline" style={{ marginTop: 12 }} role="status">Checking your session…</div>
      </div>
    </div>
  )
}

export default function App() {
  // The SESSION COOKIE is the source of truth, not a browser-storage flag: localStorage only speeds
  // up the first paint. On mount we always ask the server who we are, so a valid session is honoured
  // in a new tab, after a browser restart, or when the cached display state was cleared.
  const [auth, setAuthState] = useState<'checking' | 'in' | 'out'>(isLoggedIn() ? 'in' : 'checking')
  // Bumped once the server's authoritative identity is known, so the shell re-renders with the real
  // role/permissions instead of whatever was cached in browser storage from an earlier session.
  const [identity, setIdentity] = useState(0)

  useEffect(() => {
    let cancelled = false
    const cachedRole = getRole()
    const cachedName = getUserLabel() || null
    api
      .me()
      .then((user) => {
        if (cancelled) return
        const roleChanged = cachedRole !== null && cachedRole !== user.role
        const identityChanged =
          roleChanged || cachedName !== null && cachedName !== (user.full_name ?? '')
        setUser(user)
        setAuthState('in')
        // Only re-render/remount when the trusted identity differs from the cached one — the normal
        // reload path keeps the same identity and pays no extra refetch.
        if (identityChanged) setIdentity((n) => n + 1)
        // A different account now owns this browser session (e.g. signing in as another role in the
        // same tab): send them to their own portal rather than leaving them on a stale page.
        if (roleChanged) window.location.replace(getHome())
      })
      .catch(() => {
        if (cancelled) return
        // No valid session (expired, revoked, or never signed in) — fall back to the login screen.
        clearAuth()
        setAuthState('out')
      })
    return () => {
      cancelled = true
    }
    // Runs exactly once per page load: the session is re-validated on every full load and after
    // signing in (which navigates, producing a fresh load).
  }, [])

  if (auth === 'checking') return <SessionBoot />

  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/login"
          element={
            auth === 'in' ? (
              <Navigate to={getHome()} replace />
            ) : (
              <Login
                onLogin={(resp) => {
                  // Cache the display state, then reload so the shell boots from the new session
                  // cookie and the server's authoritative role/permission answer.
                  setAuth(resp)
                  setAuthState('in')
                  window.location.assign(resp.home || '/dashboard')
                }}
              />
            )
          }
        />
        <Route
          path="*"
          element={
            auth === 'in' ? (
              <Shell key={identity}>
                <Routes>
                  <Route path="/" element={<Navigate to={getHome()} replace />} />
                  <Route path="/dashboard" element={<Guard perm="dashboard.view"><Dashboard /></Guard>} />
                  <Route path="/enforcement" element={<Guard perm="enforcement.view"><EnforcementDashboard /></Guard>} />
                  <Route path="/entity" element={<Guard perm="entity.view"><EntityDashboard /></Guard>} />
                  <Route path="/internal" element={<Guard perm="internal.view"><InternalDashboard /></Guard>} />
                  <Route path="/inspections/new" element={<Guard perm="inspections.manage"><NewInspection /></Guard>} />
                  <Route path="/inspections" element={<Guard perm="inspections.view"><Inspections /></Guard>} />
                  <Route path="/inspections/:id" element={<Guard perm="inspections.view"><InspectionDetail /></Guard>} />
                  <Route path="/products" element={<Guard perm="inspections.view"><Products /></Guard>} />
                  <Route path="/products/:id" element={<Guard perm="inspections.view"><Products /></Guard>} />
                  <Route path="/repository" element={<Guard perm="compliance.view"><Repository /></Guard>} />
                  <Route path="/repository/scans/:id" element={<Guard perm="compliance.view"><ScanDetail /></Guard>} />
                  <Route path="/repository/products/:id" element={<Guard perm="compliance.view"><ProductHistory /></Guard>} />
                  <Route path="/listings" element={<Guard perm="listings.use"><Listings /></Guard>} />
                  <Route path="/violations" element={<Guard perm="enforcement.view"><Violations /></Guard>} />
                  <Route path="/reports" element={<Guard perm="reports.view"><Reports /></Guard>} />
                  <Route path="/analysis" element={<Guard perm="analysis.run"><Analysis /></Guard>} />
                  <Route path="/bills" element={<Guard perm="tools.use"><BillScanner /></Guard>} />
                  <Route path="/grocery" element={<Guard perm="tools.use"><Grocery /></Guard>} />
                  <Route path="/grocery/:id" element={<Guard perm="tools.use"><GroceryDetail /></Guard>} />
                  <Route path="/complaints" element={<Guard perm="tools.use"><ComplaintCenter /></Guard>} />
                  <Route path="/rules" element={<Guard perm="rules.view"><RuleLibrary /></Guard>} />
                  <Route path="/audit" element={<Guard perm="audit.view"><AuditLog /></Guard>} />
                  <Route path="/account" element={<Guard perm="dashboard.view"><Account /></Guard>} />
                  <Route path="/settings" element={<Guard perm="dashboard.view"><Settings /></Guard>} />
                  <Route path="*" element={<Navigate to={getHome()} replace />} />
                </Routes>
              </Shell>
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
      </Routes>
    </BrowserRouter>
  )
}
