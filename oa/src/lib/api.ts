// In dev: Vite proxy routes /api → http://localhost:8006 (no CORS).
// In prod: set VITE_API_URL to the absolute expense-api URL.
const BASE = (import.meta.env.VITE_API_URL as string | undefined) || ''
const EPMS_BASE = (import.meta.env.VITE_EPMS_API_URL as string | undefined) || 'http://localhost:8000'
const BUDGET_BASE = (import.meta.env.VITE_BUDGET_API_URL as string | undefined) || 'http://localhost:8007'
const MDM_BASE = (import.meta.env.VITE_MDM_API_URL as string | undefined) || 'http://localhost:8002'
const FINANCE_BASE = (import.meta.env.VITE_FINANCE_API_URL as string | undefined) || 'http://localhost:8004'

function getToken(): string | null {
  try {
    // Read from OA's own persisted store (oa-auth), with fallback to portal-auth
    for (const key of ['oa-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const token = raw ? JSON.parse(raw)?.state?.token : null
      if (token) return token
    }
    return null
  } catch { return null }
}

function headers(hasBody = false): HeadersInit {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  if (hasBody) h['Content-Type'] = 'application/json'
  return h
}

const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'
export const EPMS_URL = (import.meta.env.VITE_EPMS_URL as string | undefined) || 'http://localhost:5173'

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

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: headers(!!body),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) {
    localStorage.removeItem('oa-auth')
    window.location.href = `${PORTAL_URL}/logout`
    throw new Error('Session expired. Redirecting to portal…')
  }
  if (!res.ok) {
    throw await toApiError(res)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

// Multipart upload — do NOT set Content-Type so the browser adds the boundary.
async function postForm<T>(path: string, form: FormData): Promise<T> {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${BASE}${path}`, { method: 'POST', headers: h, body: form })
  if (res.status === 401) {
    localStorage.removeItem('oa-auth')
    window.location.href = `${PORTAL_URL}/logout`
    throw new Error('Session expired. Redirecting to portal…')
  }
  if (!res.ok) {
    throw await toApiError(res)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

// Authenticated binary fetch — needed for file downloads, since an <a href> to an
// auth-required endpoint carries no bearer token and (in prod) points at the wrong origin.
async function getBlob(path: string): Promise<Blob> {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${BASE}${path}`, { headers: h })
  if (res.status === 401) {
    localStorage.removeItem('oa-auth')
    window.location.href = `${PORTAL_URL}/logout`
    throw new Error('Session expired. Redirecting to portal…')
  }
  if (!res.ok) throw await toApiError(res)
  return res.blob()
}

export const api = {
  get:    <T>(path: string)             => request<T>('GET', path),
  post:   <T>(path: string, body: unknown) => request<T>('POST', path, body),
  patch:  <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  delete: <T>(path: string)             => request<T>('DELETE', path),
  postForm,
  getBlob,
}

// Paginated list endpoints default to page_size=20 (cap 200) on the server, so
// any caller that needs the complete list must page through. Fetches page 1 to
// learn the total, then the remaining pages in parallel.
export async function fetchAllPages<T>(
  fetchPage: (page: number, pageSize: number) => Promise<{ items: T[]; total: number }>,
  pageSize = 200,
): Promise<{ items: T[]; total: number }> {
  const first = await fetchPage(1, pageSize)
  const items = [...first.items]
  const totalPages = Math.ceil(first.total / pageSize)
  if (totalPages > 1) {
    const rest = await Promise.all(
      Array.from({ length: totalPages - 1 }, (_, i) => fetchPage(i + 2, pageSize)),
    )
    for (const r of rest) items.push(...r.items)
  }
  return { items, total: items.length }
}

// Separate client for epms-api (vendors, POs, etc.)
async function epmsRequest<T>(path: string): Promise<T> {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${EPMS_BASE}${path}`, { headers: h })
  if (!res.ok) {
    throw await toApiError(res)
  }
  return res.json()
}

export const epmsApi = {
  get: <T>(path: string) => epmsRequest<T>(path),
}

// Separate client for budget-api (:8007) — catalog hierarchy, balance, etc.
async function budgetRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  if (body) h['Content-Type'] = 'application/json'
  const res = await fetch(`${BUDGET_BASE}/api/v1${path}`, {
    method, headers: h, body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    throw await toApiError(res)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const budgetApi = {
  get:    <T>(path: string)                 => budgetRequest<T>('GET', path),
  post:   <T>(path: string, body: unknown)  => budgetRequest<T>('POST', path, body),
  patch:  <T>(path: string, body: unknown)  => budgetRequest<T>('PATCH', path, body),
  delete: <T>(path: string)                 => budgetRequest<T>('DELETE', path),
}

// mdm-api (:8002) — tax codes from the B2 tax engine (A5 ITC line coding)
async function mdmRequest<T>(path: string): Promise<T> {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${MDM_BASE}/mdm/v1${path}`, { headers: h })
  if (!res.ok) {
    throw await toApiError(res)
  }
  return res.json()
}

export const mdmApi = { get: <T>(path: string) => mdmRequest<T>(path) }

// finance-api (:8004) — bank/card accounts for the payment-source picker
async function financeRequest<T>(path: string): Promise<T> {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${FINANCE_BASE}/finance/v1${path}`, { headers: h })
  if (!res.ok) throw await toApiError(res)
  return res.json()
}

export const financeApi = { get: <T>(path: string) => financeRequest<T>(path) }
