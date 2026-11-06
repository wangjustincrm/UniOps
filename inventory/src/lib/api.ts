// Absolute URLs from VITE_* env — NOT a Vite dev proxy (the proxy in
// vite.config.ts only helps `npm run dev` on bare metal; in Docker, dev or
// prod, VITE_API_URL is always set explicitly). Same shape as mrp/src/lib/api.ts.
//
// Three backends:
//   * inventory-api (VITE_API_URL)      — NC mirrors, settings, comparison
//   * mrp-api       (VITE_MRP_API_URL)  — the WMS snapshot (/inventory/*),
//     which stays where it is; this module only reads it (design §7.1)
//   * epms-api      (VITE_EPMS_API_URL) — branding + GET /config/me/permissions
const BASE = (import.meta.env.VITE_API_URL as string | undefined) || 'http://localhost:8013'
const MRP_BASE = (import.meta.env.VITE_MRP_API_URL as string | undefined) || 'http://localhost:8011'
const EPMS_BASE = (import.meta.env.VITE_EPMS_API_URL as string | undefined) || 'http://localhost:8000'

export const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

function getToken(): string | null {
  try {
    // Inventory's own persisted store first; portal-auth covers the moment
    // right after a Portal SSO handoff, before inventory-auth is hydrated.
    for (const key of ['inventory-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const token = raw ? JSON.parse(raw)?.state?.token : null
      if (token) return token
    }
    return null
  } catch { return null }
}

// FastAPI `detail` may be a string, a structured dict (e.g. {message, ...}), or a
// 422 validation array. Coerce any of these to a readable string so callers never
// surface "[object Object]". The structured payload stays on ApiError.detail.
export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(message: string, status: number, detail: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

function detailToMessage(detail: unknown, fallback: string): string {
  if (detail == null) return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d)))
    return msgs.join('; ') || fallback
  }
  if (typeof detail === 'object' && 'message' in (detail as object)) {
    return String((detail as { message: unknown }).message)
  }
  try { return JSON.stringify(detail) } catch { return fallback }
}

async function toApiError(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => ({ detail: res.statusText }))
  const detail = (body as { detail?: unknown })?.detail
  return new ApiError(detailToMessage(detail, `HTTP ${res.status}`), res.status, detail)
}

async function authFetch(url: string, opts: { method?: string; body?: unknown } = {}): Promise<Response> {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  let bodyInit: BodyInit | undefined
  if (opts.body !== undefined) {
    h['Content-Type'] = 'application/json'
    bodyInit = JSON.stringify(opts.body)
  }
  const res = await fetch(url, { method: opts.method ?? 'GET', headers: h, body: bodyInit })
  if (res.status === 401) {
    localStorage.removeItem('inventory-auth')
    window.location.href = `${PORTAL_URL}/logout`
    throw new Error('Session expired. Redirecting to portal…')
  }
  return res
}

async function request<T>(url: string, method: string, body?: unknown): Promise<T> {
  const res = await authFetch(url, { method, body })
  if (!res.ok) throw await toApiError(res)
  // A DELETE answers 204 with no body; parsing it as JSON would throw on what
  // was actually a success.
  if (res.status === 204) return undefined as T
  return res.json()
}

function client(base: string) {
  return {
    get:    <T>(path: string)                 => request<T>(`${base}${path}`, 'GET'),
    post:   <T>(path: string, body?: unknown) => request<T>(`${base}${path}`, 'POST', body),
    put:    <T>(path: string, body: unknown)  => request<T>(`${base}${path}`, 'PUT', body),
    delete: <T>(path: string)                 => request<T>(`${base}${path}`, 'DELETE'),
  }
}

/** inventory-api client. Mounted under API_V1_PREFIX="/api/v1" — pass paths
 *  without it (`/settings/params`). */
export const api = client(`${BASE}/api/v1`)

/** mrp-api client (WMS snapshot: /inventory/*). Same token; mrp-api also
 *  mounts under /api/v1 — pass paths without it. */
export const mrpApi = client(`${MRP_BASE}/api/v1`)

/** epms-api client (branding, permissions). Paths include /api/v1. */
export const epmsApi = client(EPMS_BASE)
