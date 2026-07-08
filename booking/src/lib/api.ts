// In dev: Vite proxy routes /api → http://localhost:8010 (no CORS).
// In prod: set VITE_API_URL to the absolute booking-api URL.
const BASE = (import.meta.env.VITE_API_URL as string | undefined) || ''
const EPMS_BASE = (import.meta.env.VITE_EPMS_API_URL as string | undefined) || 'http://localhost:8000'
const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL as string | undefined) || 'http://localhost:5174'

/**
 * Structured API error that carries the raw response body.
 * Used by BookingCreatePage to extract conflict details from 400 responses.
 */
export class ApiError extends Error {
  status: number
  body: unknown

  constructor(message: string, status: number, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

function getToken(): string | null {
  try {
    // Prefer Booking's own persisted store, fall back to portal-auth (right after
    // a Portal SSO handoff, booking-auth may not be hydrated yet).
    for (const key of ['booking-auth', 'portal-auth']) {
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

async function request<T>(base: string, method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${base}${path}`, {
    method,
    headers: headers(!!body),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) {
    localStorage.removeItem('booking-auth')
    window.location.href = `${PORTAL_URL}/logout`
    throw new Error('Session expired. Redirecting to portal…')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(err.detail ?? `HTTP ${res.status}`, res.status, err)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

/** booking-api client. */
export const api = {
  get:    <T>(path: string)                  => request<T>(BASE, 'GET', path),
  post:   <T>(path: string, body: unknown)   => request<T>(BASE, 'POST', path, body),
  put:    <T>(path: string, body: unknown)   => request<T>(BASE, 'PUT', path, body),
  patch:  <T>(path: string, body: unknown)   => request<T>(BASE, 'PATCH', path, body),
  delete: <T>(path: string)                  => request<T>(BASE, 'DELETE', path),
}

function epmsRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  return request<T>(EPMS_BASE, method, `/api/v1${path}`, body)
}

/** epms-api client (used for role-permissions matrix, branding, etc.). */
export const epmsApi = {
  get:   <T>(path: string)                  => epmsRequest<T>('GET', path),
  post:  <T>(path: string, body: unknown)   => epmsRequest<T>('POST', path, body),
  patch: <T>(path: string, body: unknown)   => epmsRequest<T>('PATCH', path, body),
}
