// Portal talks to EPMS API for auth + tasks, OA API for expense tasks
import { globalSignOut } from './signOut'

// FastAPI validation errors return detail as an array of {loc, msg, type} objects.
function extractDetail(detail: unknown, status: number): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((d: any) => {
      const loc = Array.isArray(d.loc) ? d.loc.slice(1).join('.') : ''
      return loc ? `${loc}: ${d.msg}` : d.msg
    }).join(' · ')
  }
  return `HTTP ${status}`
}

const EPMS_API     = (import.meta.env.VITE_EPMS_API_URL     as string | undefined) || 'http://localhost:8000'
const OA_API       = (import.meta.env.VITE_OA_API_URL       as string | undefined) || 'http://localhost:8006'
const BUDGET_API   = (import.meta.env.VITE_BUDGET_API_URL   as string | undefined) || 'http://localhost:8007'
const MDM_API      = (import.meta.env.VITE_MDM_API_URL      as string | undefined) || 'http://localhost:8002'

function getToken(): string | null {
  try {
    const raw = localStorage.getItem('portal-auth')
    if (!raw) return null
    return JSON.parse(raw)?.state?.token ?? null
  } catch { return null }
}

function authHeaders(hasBody = false): HeadersInit {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  if (hasBody) h['Content-Type'] = 'application/json'
  return h
}

async function epmsRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${EPMS_API}/api/v1${path}`, {
    method,
    headers: authHeaders(!!body),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) {
    const errBody = await res.json().catch(() => null)
    const detail = errBody?.detail
    // If the user had a stored token, the 401 means session expired — sign them out.
    // If there is no stored token this is likely the login endpoint: show the API message.
    if (getToken()) {
      globalSignOut()
      throw new Error('Session expired. Please log in again.')
    }
    throw new Error(typeof detail === 'string' ? detail : 'Incorrect email or password')
  }
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({ detail: res.statusText }))
    // Attach the raw `detail` (and status) to the thrown Error so callers that need
    // structured info (e.g. 409 {"locked":[{role,key}]} from the Access Control
    // Matrix) can inspect it beyond the flattened message string.
    const err = new Error(extractDetail(errBody.detail, res.status)) as Error & { detail?: unknown; status?: number }
    err.detail = errBody.detail
    err.status = res.status
    throw err
  }
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T
  return res.json()
}

async function oaRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${OA_API}${path}`, {
    method,
    headers: authHeaders(!!body),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const b = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractDetail(b.detail, res.status))
  }
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T
  return res.json()
}

async function budgetRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BUDGET_API}/api/v1${path}`, {
    method,
    headers: authHeaders(!!body),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) {
    if (getToken()) {
      globalSignOut()
      throw new Error('Session expired. Please log in again.')
    }
  }
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractDetail(errBody.detail, res.status))
  }
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T
  return res.json()
}

async function mdmRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${MDM_API}/mdm/v1${path}`, {
    method,
    headers: authHeaders(!!body),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) {
    if (getToken()) {
      globalSignOut()
      throw new Error('Session expired. Please log in again.')
    }
    throw new Error('Unauthorized')
  }
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractDetail(errBody.detail, res.status))
  }
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T
  return res.json()
}

export const mdmApi = {
  get:    <T>(path: string)                  => mdmRequest<T>('GET',    path),
  post:   <T>(path: string, body?: unknown)  => mdmRequest<T>('POST',   path, body),
  patch:  <T>(path: string, body?: unknown)  => mdmRequest<T>('PATCH',  path, body),
  delete: <T>(path: string)                  => mdmRequest<T>('DELETE', path),
}

export const epmsApi = {
  get:    <T>(path: string)                    => epmsRequest<T>('GET',    path),
  post:   <T>(path: string, body: unknown)     => epmsRequest<T>('POST',   path, body),
  patch:  <T>(path: string, body: unknown)     => epmsRequest<T>('PATCH',  path, body),
  put:    <T>(path: string, body: unknown)     => epmsRequest<T>('PUT',    path, body),
  delete: <T>(path: string)                    => epmsRequest<T>('DELETE', path),
}

/** Download a file from EPMS API and trigger a browser save dialog. */
export async function epmsDownload(path: string, filename: string): Promise<void> {
  const res = await fetch(`${EPMS_API}/api/v1${path}`, { headers: authHeaders() })
  if (res.status === 401) { globalSignOut(); return }
  if (!res.ok) throw new Error(`Export failed: HTTP ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

/**
 * POST a body to EPMS API and save the response as a file.
 *
 * Separate from epmsDownload because that one is a GET; an export sends the
 * query it should run. Both share authHeaders and the 401 sign-out so there is
 * only one definition of "signed in" in this file.
 *
 * The filename comes from the response when it offers one — the server names
 * the sheet after the entity and the day, which is more useful than anything
 * the caller could guess.
 */
export async function epmsPostDownload(
  path: string, body: unknown, fallbackName: string,
): Promise<void> {
  const res = await fetch(`${EPMS_API}/api/v1${path}`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (res.status === 401) { globalSignOut(); return }
  if (!res.ok) throw new Error(`Export failed: HTTP ${res.status}`)

  const blob = await res.blob()
  const named = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') ?? '')
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = named?.[1] ?? fallbackName
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoking immediately can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** POST a File as multipart/form-data to EPMS API. */
export async function epmsUpload<T>(path: string, file: File, fieldName = 'file'): Promise<T> {
  const form = new FormData()
  form.append(fieldName, file)
  const res = await fetch(`${EPMS_API}/api/v1${path}`, {
    method: 'POST',
    headers: authHeaders(),  // no Content-Type — browser sets multipart boundary
    body: form,
  })
  if (res.status === 401) { globalSignOut() }
  if (!res.ok) {
    const b = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractDetail(b.detail, res.status))
  }
  return res.json()
}

export const oaApi = {
  get:  <T>(path: string) => oaRequest<T>('GET', path),
  post: <T>(path: string, body: unknown) => oaRequest<T>('POST', path, body),
}

export const budgetApi = {
  get:    <T>(path: string)                => budgetRequest<T>('GET',    path),
  post:   <T>(path: string, body: unknown) => budgetRequest<T>('POST',   path, body),
  patch:  <T>(path: string, body: unknown) => budgetRequest<T>('PATCH',  path, body),
  delete: <T>(path: string)                => budgetRequest<T>('DELETE', path),
}

const FINANCE_API = (import.meta.env.VITE_FINANCE_API_URL as string | undefined) || 'http://localhost:8004'

async function financeRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${FINANCE_API}/finance/v1${path}`, {
    method,
    headers: authHeaders(!!body),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 && getToken()) {
    globalSignOut()
    throw new Error('Session expired. Please log in again.')
  }
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractDetail(errBody.detail, res.status))
  }
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T
  return res.json()
}

export const financeApi = {
  get:    <T>(path: string)                 => financeRequest<T>('GET',    path),
  post:   <T>(path: string, body: unknown)  => financeRequest<T>('POST',   path, body),
  patch:  <T>(path: string, body: unknown)  => financeRequest<T>('PATCH',  path, body),
  put:    <T>(path: string, body: unknown)  => financeRequest<T>('PUT',    path, body),
  delete: <T = void>(path: string)          => financeRequest<T>('DELETE', path),
}

// ── mrp-api ───────────────────────────────────────────────────────────────────
// Portal reaches MRP for exactly one thing: the WMS sync controls in Admin
// (status, the interval parameter, the manual run). MRP's own screens live in
// the separate mrp front-end at VITE_MRP_URL — this is the API host, a
// different value, wired as VITE_MRP_API_URL in portal/Dockerfile and both
// docker-compose files. A missing build arg here degrades to localhost:8011,
// which in a browser on somebody's desk means the section never loads; that
// exact omission has shipped three times in this repo, so the arg is added in
// all four places or not at all.
const MRP_API = (import.meta.env.VITE_MRP_API_URL as string | undefined) || 'http://localhost:8011'

async function mrpRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${MRP_API}/api/v1${path}`, {
    method,
    headers: authHeaders(!!body),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 && getToken()) {
    globalSignOut()
    throw new Error('Session expired. Please log in again.')
  }
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractDetail(errBody.detail, res.status))
  }
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T
  return res.json()
}

export const mrpApi = {
  get:  <T>(path: string)                => mrpRequest<T>('GET',  path),
  post: <T>(path: string, body: unknown) => mrpRequest<T>('POST', path, body),
  put:  <T>(path: string, body: unknown) => mrpRequest<T>('PUT',  path, body),
}

/** POST a File as multipart/form-data to Finance API (system CSV import convention). */
export async function financeUpload<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${FINANCE_API}/finance/v1${path}`, {
    method: 'POST',
    headers: authHeaders(),  // no Content-Type — browser sets multipart boundary
    body: form,
  })
  if (res.status === 401 && getToken()) {
    globalSignOut()
    throw new Error('Session expired. Please log in again.')
  }
  if (!res.ok) {
    const b = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractDetail(b.detail, res.status))
  }
  return res.json()
}

/** POST a File plus form fields as multipart/form-data to Finance API. */
export async function financeUploadFields<T>(
  path: string, file: File, fields?: Record<string, string>,
): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  for (const [k, v] of Object.entries(fields ?? {})) form.append(k, v)
  const res = await fetch(`${FINANCE_API}/finance/v1${path}`, {
    method: 'POST',
    headers: authHeaders(),  // no Content-Type — browser sets multipart boundary
    body: form,
  })
  if (res.status === 401 && getToken()) {
    globalSignOut()
    throw new Error('Session expired. Please log in again.')
  }
  if (!res.ok) {
    const b = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractDetail(b.detail, res.status))
  }
  return res.json()
}

/** GET a CSV from Finance API and trigger a browser download. */
export async function financeDownload(path: string, filename: string): Promise<void> {
  const res = await fetch(`${FINANCE_API}/finance/v1${path}`, { headers: authHeaders() })
  if (!res.ok) {
    const b = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractDetail(b.detail, res.status))
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** POST a File (plus optional form fields) as multipart/form-data to Budget API. */
export async function budgetUpload<T>(
  path: string, file: File, fields?: Record<string, string>,
): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  for (const [k, v] of Object.entries(fields ?? {})) form.append(k, v)
  const res = await fetch(`${BUDGET_API}/api/v1${path}`, {
    method: 'POST',
    headers: authHeaders(),  // no Content-Type — browser sets multipart boundary
    body: form,
  })
  if (res.status === 401 && getToken()) {
    globalSignOut()
    throw new Error('Session expired. Please log in again.')
  }
  if (!res.ok) {
    const b = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(extractDetail(b.detail, res.status))
  }
  return res.json()
}

export const BUDGET_API_URL = BUDGET_API

// URLs for building deep-links with token handoff
export const EPMS_URL = (import.meta.env.VITE_EPMS_URL as string | undefined) || 'http://localhost:5173'
export const OA_URL   = (import.meta.env.VITE_OA_URL   as string | undefined) || 'http://localhost:5175'
export const VMS_URL  = (import.meta.env.VITE_VMS_URL  as string | undefined) || 'http://localhost:5176'
export const FINANCE_URL = (import.meta.env.VITE_FINANCE_URL as string | undefined) || 'http://localhost:5177'
export const BOOKING_URL = (import.meta.env.VITE_BOOKING_URL as string | undefined) || 'http://localhost:5178'
export const MRP_URL  = (import.meta.env.VITE_MRP_URL  as string | undefined) || 'http://localhost:5179'

// btoa() only handles Latin1; user data (e.g. Chinese full_name) is UTF-8, which
// makes btoa throw "characters outside of the Latin1 range". Encode the JSON as
// UTF-8 bytes before base64. Decoders must mirror this (see each app's main.tsx).
function encodeUtf8Base64(str: string): string {
  const bytes = new TextEncoder().encode(str)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin)
}

// Encode a portal session for handoff to EPMS/OA via URL hash
export function encodeSession(token: string, refreshToken: string, user: object): string {
  return encodeUtf8Base64(JSON.stringify({ token, refreshToken, user }))
}

// ── returnUrl handoff ─────────────────────────────────────────────────────────
// Sub-apps that find themselves without a session send the user here as
// `${PORTAL_URL}?returnUrl=<their current href>`. Portal owns the login, then
// hands the session back to that origin via the same `#__session=` mechanism
// the nav links use, so the user lands on the page they originally asked for.

const RETURN_URL_ORIGINS = [EPMS_URL, OA_URL, VMS_URL, FINANCE_URL, BOOKING_URL, MRP_URL]

/**
 * Validate a caller-supplied returnUrl against the module origins we know.
 * Anything else — a foreign host, a javascript:/data: URL, a malformed string —
 * yields null, so a crafted link cannot turn the login page into an open
 * redirect. Portal's own origin is allowed (a module may bounce back an
 * in-portal path).
 */
export function safeReturnUrl(raw: string | null | undefined): string | null {
  if (!raw) return null
  let target: URL
  try {
    target = new URL(raw, window.location.origin)
  } catch {
    return null
  }
  if (target.protocol !== 'http:' && target.protocol !== 'https:') return null
  if (target.origin === window.location.origin) return target.toString()
  const known = RETURN_URL_ORIGINS.some((base) => {
    try {
      return new URL(base).origin === target.origin
    } catch {
      return false
    }
  })
  return known ? target.toString() : null
}

/**
 * Leave for a validated returnUrl, carrying the session when it points at a
 * different origin (cross-origin localStorage is not shared — the hash handoff
 * is how every sub-app receives a Portal session). Same-origin targets need no
 * handoff. Appends rather than overwrites the hash so an existing fragment
 * survives; every decoder matches `__session=([^&]+)`.
 */
export function goToReturnUrl(returnUrl: string, session: string): void {
  const target = new URL(returnUrl)
  if (target.origin !== window.location.origin) {
    const existing = target.hash.replace(/^#/, '')
    target.hash = existing ? `${existing}&__session=${session}` : `__session=${session}`
  }
  window.location.href = target.toString()
}
