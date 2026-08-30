/**
 * Safety API client.
 *
 * Absolute URLs, not a Vite proxy path: the proxy only exists in dev, and a
 * relative path silently resolves against the nginx container in production
 * where nothing is listening for it.
 */
import { useEhsAuth } from '@/store/auth'

export const BASE = (import.meta.env.VITE_API_URL as string) || 'http://localhost:8012'
export const EPMS_BASE = (import.meta.env.VITE_EPMS_API_URL as string) || 'http://localhost:8000'
export const MDM_BASE = (import.meta.env.VITE_MDM_API_URL as string) || 'http://localhost:8002'
export const FILE_BASE = (import.meta.env.VITE_FILE_API_URL as string) || 'http://localhost:8005'
export const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string) || 'http://localhost:5174'

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, message: string, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

/** Our own session first, then Portal's — a deep link can arrive before the
 * handoff has run. */
function getToken(): string | null {
  const own = useEhsAuth.getState().token
  if (own) return own
  try {
    const portal = localStorage.getItem('portal-auth')
    if (portal) return JSON.parse(portal)?.state?.token ?? null
  } catch {
    /* malformed storage is the same as no session */
  }
  return null
}

function onUnauthorized(): void {
  try {
    localStorage.removeItem('ehs-auth')
  } catch {
    /* ignore */
  }
  window.location.href = `${PORTAL_URL}/logout`
}

async function request<T>(base: string, path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const res = await fetch(`${base}${path}`, { ...init, headers })

  if (res.status === 401) {
    onUnauthorized()
    throw new ApiError(401, 'Your session has expired. Sign in again.', null)
  }

  if (res.status === 204) return undefined as T

  const text = await res.text()
  const body = text ? safeJson(text) : null

  if (!res.ok) {
    throw new ApiError(res.status, detailOf(body) ?? `Request failed (${res.status})`, body)
  }
  return body as T
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function detailOf(body: unknown): string | null {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    // FastAPI validation errors arrive as a list of objects.
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0] as { msg?: string }
      if (first?.msg) return first.msg
    }
  }
  return null
}

export const api = {
  get: <T>(path: string) => request<T>(BASE, path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(BASE, path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(BASE, path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(BASE, path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(BASE, path, { method: 'DELETE' }),
}

export const epmsApi = {
  get: <T>(path: string) => request<T>(EPMS_BASE, path),
}
