// Absolute URLs from VITE_* env — NOT a Vite dev proxy (the proxy in
// vite.config.ts only helps `npm run dev` on bare metal; in Docker, dev or
// prod, VITE_API_URL is always set explicitly). Same shape as inventory/src/lib/api.ts.
//
//   * sales-api (VITE_API_URL)       — this module's API
//   * epms-api    (VITE_EPMS_API_URL)  — branding + GET /config/me/permissions
const BASE = (import.meta.env.VITE_API_URL as string | undefined) || 'http://localhost:8014'
const EPMS_BASE = (import.meta.env.VITE_EPMS_API_URL as string | undefined) || 'http://localhost:8000'

export const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

function getToken(): string | null {
  try {
    // This module's persisted store first; portal-auth covers the moment
    // right after a Portal SSO handoff, before sales-auth is hydrated.
    for (const key of ['sales-auth', 'portal-auth']) {
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
  /** The whole response body — e.g. {"error": {code, message, details}}. */
  body: unknown
  constructor(message: string, status: number, detail: unknown, body: unknown = undefined) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.body = body
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

// The body is read once, as text, and parsed as JSON only if it is JSON: a
// proxy's HTML error page or an empty 502 must still give a readable message
// (never "" from an empty statusText), and the raw body is kept on the error.
export async function toApiError(res: Response): Promise<ApiError> {
  const fallback = res.statusText?.trim() ? `HTTP ${res.status} ${res.statusText.trim()}` : `HTTP ${res.status}`
  const text = await res.text().catch(() => '')
  let body: unknown = text || undefined
  try {
    if (text) body = JSON.parse(text)
  } catch {
    // not JSON: keep the text
  }
  const obj = body && typeof body === 'object' ? (body as { detail?: unknown; error?: { message?: unknown } }) : undefined
  const detail = obj?.detail
  const message = typeof obj?.error?.message === 'string' && obj.error.message
    ? obj.error.message
    : detailToMessage(detail, fallback)
  return new ApiError(message, res.status, detail, body)
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
    localStorage.removeItem('sales-auth')
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

/** sales-api client. Mounted under API_V1_PREFIX="/api/v1" — pass paths
 *  without it. */
export const api = client(`${BASE}/api/v1`)

/** epms-api client (branding, permissions). Paths include /api/v1. */
export const epmsApi = client(EPMS_BASE)
