// Absolute URLs from VITE_* env — NOT a Vite dev proxy. The proxy below only
// helps `npm run dev` on bare metal; once this app runs in Docker (dev or
// prod), VITE_API_URL is always set explicitly and the proxy is unused. This
// mirrors oa/src/lib/api.ts — see that file's note; the Vite-proxy-in-Docker
// trap has burned this project before.
const BASE = (import.meta.env.VITE_API_URL as string | undefined) || 'http://localhost:8011'
const EPMS_BASE = (import.meta.env.VITE_EPMS_API_URL as string | undefined) || 'http://localhost:8000'

function getToken(): string | null {
  try {
    // Read from MRP's own persisted store (mrp-auth), with fallback to portal-auth
    // (right after a Portal SSO handoff, mrp-auth may not be hydrated yet).
    for (const key of ['mrp-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const token = raw ? JSON.parse(raw)?.state?.token : null
      if (token) return token
    }
    return null
  } catch { return null }
}

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

// FastAPI `detail` may be a string, a structured dict (e.g. {message, ...}), or a
// 422 validation array. Coerce any of these to a readable string so callers never
// surface "[object Object]". The structured payload is attached to the Error for
// callers that want it (see ApiError.detail).
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
    const msgs = detail.map(d => (d && typeof d === 'object' && 'msg' in d ? String((d as any).msg) : String(d)))
    return msgs.join('; ') || fallback
  }
  if (typeof detail === 'object' && 'message' in (detail as any)) {
    return String((detail as any).message)
  }
  try { return JSON.stringify(detail) } catch { return fallback }
}

async function toApiError(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => ({ detail: res.statusText }))
  const detail = (body as any)?.detail
  return new ApiError(detailToMessage(detail, `HTTP ${res.status}`), res.status, detail)
}

async function authFetch(
  url: string,
  opts: { method?: string; body?: unknown; form?: FormData; handle401?: boolean } = {},
): Promise<Response> {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`

  let bodyInit: BodyInit | undefined
  if (opts.form) {
    bodyInit = opts.form                 // multipart — let the browser set the boundary
  } else if (opts.body !== undefined) {
    h['Content-Type'] = 'application/json'
    bodyInit = JSON.stringify(opts.body)
  }

  const res = await fetch(url, { method: opts.method ?? 'GET', headers: h, body: bodyInit })

  if (opts.handle401 !== false && res.status === 401) {
    localStorage.removeItem('mrp-auth')
    window.location.href = `${PORTAL_URL}/logout`
    throw new Error('Session expired. Redirecting to portal…')
  }
  return res
}

// mrp-api mounts everything under API_V1_PREFIX="/api/v1" — see mrp-api/app/main.py.
async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await authFetch(`${BASE}/api/v1${path}`, { method, body })
  if (!res.ok) throw await toApiError(res)
  if (res.status === 204) return undefined as T
  return res.json()
}

async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await authFetch(`${BASE}/api/v1${path}`, { method: 'POST', form })
  if (!res.ok) throw await toApiError(res)
  if (res.status === 204) return undefined as T
  return res.json()
}

async function getBlob(path: string): Promise<Blob> {
  const res = await authFetch(`${BASE}/api/v1${path}`, {})
  if (!res.ok) throw await toApiError(res)
  return res.blob()
}

/** mrp-api client. */
export const api = {
  get:    <T>(path: string)                => request<T>('GET', path),
  post:   <T>(path: string, body: unknown) => request<T>('POST', path, body),
  patch:  <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  delete: <T>(path: string)                => request<T>('DELETE', path),
  postForm,
  getBlob,
}

async function epmsRequest<T>(path: string): Promise<T> {
  const res = await authFetch(`${EPMS_BASE}${path}`, {})
  if (!res.ok) throw await toApiError(res)
  return res.json()
}

/** epms-api client (used for public branding — see hooks/useBranding.ts). */
export const epmsApi = {
  get: <T>(path: string) => epmsRequest<T>(path),
}
