// API client.
//
// The session lives in an HttpOnly cookie set by the backend — no access/refresh token is kept in
// browser storage (localStorage only holds non-sensitive display state such as the role label used
// to render the shell). Authorization is decided by the server on every request.
import type {
  AssistantContext,
  AssistantReply,
  AssistantWelcome,
  InspectionRevision,
  MigrationJob,
  ReprocessPreview,
  ReprocessResult,
  BillScanResponse,
  ComplaintListResponse,
  FontSizeCompliance,
  GroceryDetail,
  GroceryListResponse,
  ImageAnalysis,
  ListingConsistency,
  ListingDeclarationCheck,
  ListingOut,
  ListingRow,
  LoginResponse,
  ProductHistory,
  RepositoryList,
  ScanDetailPayload,
  SessionInfo,
  User,
} from '../types'

const BASE = '/api'

let currentRole: string | null = localStorage.getItem('pocket_role')
let currentName: string | null = localStorage.getItem('pocket_name')
let currentUsername: string | null = localStorage.getItem('pocket_username')
let currentPerms: string[] = readPerms()

function readPerms(): string[] {
  try {
    const raw = localStorage.getItem('pocket_perms')
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function setAuth(auth: LoginResponse) {
  currentRole = auth.role
  currentName = auth.full_name
  currentUsername = auth.username
  currentPerms = auth.permissions ?? []
  localStorage.setItem('pocket_authed', '1')
  localStorage.setItem('pocket_role', auth.role)
  localStorage.setItem('pocket_name', auth.full_name ?? '')
  localStorage.setItem('pocket_username', auth.username ?? '')
  localStorage.setItem('pocket_perms', JSON.stringify(currentPerms))
}

/** Refresh the cached role/permissions from the authoritative /auth/me response. */
export function setUser(user: User) {
  currentRole = user.role
  currentName = user.full_name
  currentUsername = user.username
  currentPerms = user.permissions ?? []
  localStorage.setItem('pocket_authed', '1')
  localStorage.setItem('pocket_role', user.role)
  localStorage.setItem('pocket_name', user.full_name ?? '')
  localStorage.setItem('pocket_username', user.username ?? '')
  localStorage.setItem('pocket_perms', JSON.stringify(currentPerms))
  if (user.home) localStorage.setItem('pocket_home', user.home)
}

export function clearAuth() {
  currentRole = null
  currentName = null
  currentUsername = null
  currentPerms = []
  for (const k of ['pocket_authed', 'pocket_role', 'pocket_name', 'pocket_username', 'pocket_perms', 'pocket_home']) {
    localStorage.removeItem(k)
  }
}

export function isLoggedIn(): boolean {
  return localStorage.getItem('pocket_authed') === '1'
}

export function getRole(): string | null {
  return currentRole
}

export function getUserLabel(): string {
  return currentName || currentUsername || ''
}

export function getUsername(): string {
  return currentUsername || ''
}

export function getPermissions(): string[] {
  return currentPerms
}

/** Client-side capability check — a convenience for rendering, NOT the security boundary. */
export function hasPermission(permission: string): boolean {
  return currentPerms.includes(permission) || currentPerms.includes('*')
}

export function hasAnyPermission(...permissions: string[]): boolean {
  return permissions.some(hasPermission)
}

/** Where the authenticated role belongs. */
export function getHome(): string {
  return localStorage.getItem('pocket_home') || '/dashboard'
}

/** Where the dev proxy forwards API calls — shown in errors so the cause is actionable. */
const BACKEND_HINT =
  'Start the PACKCHECK AI backend (start.bat, or: .venv\\Scripts\\python -m uvicorn backend.main:app --port 8001), then retry.'

const AUTH_PATHS = ['/auth/login', '/auth/mfa', '/auth/password', '/auth/refresh']

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {}
  if (!(options.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  let res: Response
  try {
    // `include` sends the HttpOnly session cookie on every call.
    res = await fetch(`${BASE}${path}`, { ...options, headers, credentials: 'include' })
  } catch {
    throw new Error(`Cannot reach the inspection server. ${BACKEND_HINT}`)
  }
  if (res.status === 401 && !AUTH_PATHS.some((p) => path.startsWith(p))) {
    clearAuth()
    throw new Error('Session expired. Please log in again.')
  }
  if (!res.ok) {
    const ct = res.headers.get('content-type') || ''
    let detail = ''
    if (ct.includes('application/json')) {
      try {
        const body = await res.json()
        if (typeof body.detail === 'string') detail = body.detail
        else if (Array.isArray(body.detail) && body.detail[0]?.msg) detail = String(body.detail[0].msg)
      } catch {
        /* fall through to the diagnoses below */
      }
    }
    if (!detail) {
      if (res.status >= 500 || res.status === 504) {
        detail = `The inspection server did not respond (HTTP ${res.status}). ${BACKEND_HINT}`
      } else {
        detail = `Request failed (${res.status})`
      }
    }
    throw new Error(detail)
  }
  const ct = res.headers.get('content-type') || ''
  return ct.includes('application/json') ? res.json() : (res.blob() as unknown as T)
}

export interface SystemStatus {
  status: 'ok' | 'degraded'
  app: string
  version: string
  components: Record<string, boolean>
  ocr_available: boolean
  ai?: AIStatus
}

export interface AIStatus {
  enabled: boolean
  provider: string
  model: string
  reason: string
  mode: 'vision' | 'ocr_only'
  fields: string[]
}

/** Honest backend health probe used to explain unreachable-backend errors up front. */
export async function probeBackend(): Promise<SystemStatus | null> {
  try {
    const res = await fetch(`${BASE}/status`)
    if (!res.ok) return null
    return (await res.json()) as SystemStatus
  } catch {
    return null
  }
}

export const api = {
  // ---- authentication ----
  login: (username: string, password: string, roleHint?: string) =>
    request<LoginResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, role_hint: roleHint || null }),
    }),
  mfaVerify: (mfaToken: string, code: string, recoveryCode = '') =>
    request<LoginResponse>('/auth/mfa/verify', {
      method: 'POST',
      body: JSON.stringify({ mfa_token: mfaToken, code: code || null, recovery_code: recoveryCode || null }),
    }),
  refresh: () => request<LoginResponse>('/auth/refresh', { method: 'POST' }),
  logout: () => request<{ message: string }>('/auth/logout', { method: 'POST' }),
  logoutAll: () => request<{ message: string }>('/auth/logout-all', { method: 'POST' }),
  me: () => request<User>('/auth/me'),
  sessions: () => request<{ items: SessionInfo[] }>('/auth/sessions'),
  revokeSession: (sid: string) => request<{ message: string }>(`/auth/sessions/${sid}`, { method: 'DELETE' }),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ message: string }>('/auth/password/change', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
  requestPasswordReset: (identifier: string) =>
    request<{ message: string; reset_token?: string; delivery?: string }>('/auth/password/reset/request', {
      method: 'POST',
      body: JSON.stringify({ identifier }),
    }),
  confirmPasswordReset: (token: string, newPassword: string) =>
    request<{ message: string }>('/auth/password/reset/confirm', {
      method: 'POST',
      body: JSON.stringify({ token, new_password: newPassword }),
    }),
  mfaSetup: () => request<{ secret: string; otpauth_uri: string }>('/auth/mfa/setup', { method: 'POST' }),
  mfaEnable: (code: string) =>
    request<{ recovery_codes: string[] }>('/auth/mfa/enable', { method: 'POST', body: JSON.stringify({ code }) }),
  mfaDisable: (code: string) =>
    request<{ message: string }>('/auth/mfa/disable', { method: 'POST', body: JSON.stringify({ code }) }),
  mfaRecoveryCodes: (code: string) =>
    request<{ recovery_codes: string[] }>('/auth/mfa/recovery-codes', { method: 'POST', body: JSON.stringify({ code }) }),

  // ---- generic helpers ----
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body === undefined ? undefined : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  postForm: <T>(path: string, form: FormData) => request<T>(path, { method: 'POST', body: form }),

  // ---- vision provider status ----
  aiStatus: () => request<AIStatus>('/ai/status'),

  // ---- image analysis (capture -> enhance -> OCR) ----
  analyze: (file: File, mode: 'auto' | 'full' | 'text' = 'auto') => {
    const form = new FormData()
    form.append('file', file)
    form.append('mode', mode)
    return request<{ analysis: ImageAnalysis }>('/analysis', { method: 'POST', body: form })
  },
  analyses: () => request<{ items: ImageAnalysis[]; total: number }>('/analysis'),
  analysis: (id: number) => request<ImageAnalysis>(`/analysis/${id}`),

  // ---- bill scanner ----
  scanBill: (file: File, storeName = '') => {
    const form = new FormData()
    form.append('file', file)
    form.append('store_name', storeName)
    return request<BillScanResponse>('/bills/scan', { method: 'POST', body: form })
  },

  // ---- grocery ----
  grocery: () => request<GroceryListResponse>('/grocery'),
  groceryItem: (id: number) => request<GroceryDetail>(`/grocery/${id}`),

  // ---- product scan repository + compliance history ----
  repository: (params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) if (v !== '' && v !== undefined && v !== null) qs.set(k, String(v))
    return request<RepositoryList>(`/repository/scans${qs.toString() ? `?${qs}` : ''}`)
  },
  repositoryScan: (id: number) => request<ScanDetailPayload>(`/repository/scans/${id}`),
  repositoryRemarks: (id: number, remarks: string) =>
    request<{ message: string; scan: ScanDetailPayload }>(`/repository/scans/${id}/remarks`, {
      method: 'POST',
      body: JSON.stringify({ remarks }),
    }),
  repositoryFinalize: (id: number, decision: string, remarks: string) =>
    request<{ message: string; scan: ScanDetailPayload }>(`/repository/scans/${id}/finalize`, {
      method: 'POST',
      body: JSON.stringify({ decision, remarks }),
    }),
  repositoryProduct: (productId: number) => request<ProductHistory>(`/repository/products/${productId}`),
  repositorySummary: () =>
    request<{ total_scans: number; pending_finalization: number; finalized: number; facets: RepositoryList['facets'] }>('/repository/summary'),

  // ---- Rule 7 font-size compliance + calibration ----
  fontSizeCompliance: (inspectionId: number) => request<FontSizeCompliance>(`/repository/font-size/${inspectionId}`),
  measurementPreview: (inspectionId: number) =>
    request<{
      available: boolean
      px_per_mm: number | null
      px_per_mm_source: string
      px_per_mm_note: string
      panel_area_cm2: number | null
      panel_area_source: string
      panel_area_note: string
      packaging_form: string
      quantity_family: string | null
      measurements: unknown[]
      smallest_line: { text: string; line_height_mm: number; letter_height_mm: number } | null
      method: string
      cap_height_ratio: number
    }>(`/repository/inspections/${inspectionId}/measurement`),
  calibrate: (
    inspectionId: number,
    body: {
      px_per_mm?: number | null
      reference_mm?: number | null
      reference_px?: number | null
      panel_width_mm?: number | null
      panel_height_mm?: number | null
      pdp_area_cm2?: number | null
      packaging_form?: string
      note?: string
    },
  ) => request<{ message: string; scan_id: number | null }>(`/repository/inspections/${inspectionId}/calibration`, { method: 'POST', body: JSON.stringify(body) }),
  repositoryBackfill: () => request<{ created: number; details: string[] }>('/repository/backfill', { method: 'POST' }),

  // ---- PRO: in-application assistant ----
  // The context names the record the user has open; the server resolves it inside the caller's
  // own scope, so PRO can answer about THIS inspection without ever describing someone else's.
  assistantWelcome: (context?: AssistantContext) => {
    const qs = new URLSearchParams()
    if (context?.path) qs.set('path', context.path)
    if (context?.inspection_id) qs.set('inspection_id', String(context.inspection_id))
    if (context?.scan_id) qs.set('scan_id', String(context.scan_id))
    return request<AssistantWelcome>(`/assistant/welcome${qs.toString() ? `?${qs}` : ''}`)
  },
  assistantAsk: (message: string, context?: AssistantContext) =>
    request<AssistantReply>('/assistant/chat', {
      method: 'POST',
      body: JSON.stringify({ message, context: context ?? null }),
    }),

  // ---- reprocessing / migration (bring existing inspections onto the corrected pipeline) ----
  reprocessPreview: () => request<ReprocessPreview>('/reprocess/preview'),
  reprocessInspection: (inspectionId: number, reason: string, refreshOcr = false) =>
    request<ReprocessResult>(`/reprocess/inspections/${inspectionId}`, {
      method: 'POST',
      body: JSON.stringify({ reason, refresh_ocr: refreshOcr }),
    }),
  inspectionRevisions: (inspectionId: number) =>
    request<{ inspection_id: number; revisions: InspectionRevision[] }>(
      `/reprocess/inspections/${inspectionId}/revisions`,
    ),
  startReprocessBatch: (body: { reason: string; limit?: number; refresh_ocr?: boolean }) =>
    request<MigrationJob>('/reprocess/batch', { method: 'POST', body: JSON.stringify(body) }),
  reprocessJob: (jobId: string) => request<MigrationJob>(`/reprocess/jobs/${jobId}`),
  reprocessJobs: () => request<{ jobs: MigrationJob[] }>('/reprocess/jobs'),

  // ---- complaints ----
  complaints: () => request<ComplaintListResponse>('/complaints'),

  // ---- online listing / e-commerce capture + package-vs-listing consistency ----
  listings: (q = '') =>
    request<{ items: ListingRow[]; total: number; page: number; page_size: number }>(
      `/listings?q=${encodeURIComponent(q)}`,
    ),
  listingCatalog: () =>
    request<{ checks: (Omit<ListingDeclarationCheck, 'status' | 'reason' | 'declarations'> & { watched_fields?: string[] })[] }>(
      '/listings/catalog',
    ),
  listingCreate: (body: {
    text: string
    source?: string
    source_url?: string
    platform?: string
    inspection_id?: number | null
  }) => request<ListingOut>('/listings', { method: 'POST', body: JSON.stringify(body) }),
  listing: (id: number) => request<ListingOut>(`/listings/${id}`),
  listingsForInspection: (inspectionId: number) =>
    request<{ items: ListingOut[]; total: number }>(`/listings/by-inspection/${inspectionId}`),
  listingUpdate: (id: number, text: string) =>
    request<ListingOut>(`/listings/${id}`, { method: 'PUT', body: JSON.stringify({ text }) }),
  listingAttach: (id: number, inspectionId: number) =>
    request<ListingOut>(`/listings/${id}/attach`, {
      method: 'POST',
      body: JSON.stringify({ inspection_id: inspectionId }),
    }),
  listingDeclarations: (id: number) => request<ListingDeclarationCheck>(`/listings/${id}/declarations`),
  listingConsistency: (id: number) =>
    request<ListingConsistency>(`/listings/${id}/consistency`, { method: 'POST' }),
  listingDelete: (id: number) => request<{ deleted: boolean; id: number }>(`/listings/${id}`, { method: 'DELETE' }),

  /** <img> tags cannot set headers, but they DO send the session cookie on same-origin paths. */
  fileUrl: (kind: string, filename: string) => `${BASE}/files/${kind}/${encodeURIComponent(filename)}`,
}
