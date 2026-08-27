/**
 * EPMS API client — thin fetch wrapper with auth injection and error handling.
 *
 * Base URL: VITE_API_URL env var, falling back to /api/v1 (Vite proxy in dev).
 */
import { useAuthStore } from '@/stores/auth.store'
import { globalSignOut } from '@/lib/signOut'

const BASE = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'

/** OA module base URL — only set when OA (expense-api + oa frontend) is deployed. */
export const OA_BASE_URL = (import.meta.env.VITE_OA_URL as string | undefined) || ''

/** Budget microservice base URL (budget-api :8007). */
export const BUDGET_BASE = (import.meta.env.VITE_BUDGET_API_URL as string | undefined) || 'http://localhost:8007'

/** MDM microservice base URL (mdm-api :8002). */
export const MDM_BASE = (import.meta.env.VITE_MDM_API_URL as string | undefined) || 'http://localhost:8002'

/** Finance microservice base URL (finance-api :8004) — bank accounts / payment sources. */
export const FINANCE_BASE = (import.meta.env.VITE_FINANCE_API_URL as string | undefined) || 'http://localhost:8004'

/**
 * Finance module FRONTEND origin. Budget pages are owned by the Finance module
 * (Portal-wrapped); the EPMS /budget* routes still exist but are only used as
 * the iframe target inside Finance's EpmsEmbed. Cross-app jumps (e.g. opening a
 * Budget Plan approval task from the Task Inbox) must land in Finance, not in
 * EPMS's bare budget page — see financeHandoffHref().
 */
export const FINANCE_URL = (import.meta.env.VITE_FINANCE_URL as string | undefined) || 'http://localhost:5177'

/**
 * Build a full-page URL into the Finance frontend with session handoff, mirroring
 * the `#__session=<base64json>` mechanism Portal uses to launch EPMS (consumed by
 * finance/src/main.tsx). When the user isn't authenticated yet we just return the
 * bare URL and let Finance redirect to its own login.
 */
// btoa() only handles Latin1; user data (e.g. Chinese full_name) is UTF-8, which
// makes btoa throw "characters outside of the Latin1 range". Encode the JSON as
// UTF-8 bytes before base64. Decoders mirror this (see finance/src/main.tsx).
function encodeUtf8Base64(str: string): string {
  const bytes = new TextEncoder().encode(str)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin)
}

export function financeHandoffHref(path: string): string {
  const { token, refreshToken, user } = useAuthStore.getState()
  const base = `${FINANCE_URL}${path}`
  if (!token || !user) return base
  const session = encodeUtf8Base64(JSON.stringify({ token, refreshToken: refreshToken ?? '', user }))
  return `${base}#__session=${session}`
}

/**
 * VMS module FRONTEND origin. The shared `tasks` table carries VMS-owned rows
 * (visit approvals, visitor check-out, PPE prep, training/PPE compliance), which
 * therefore surface in the EPMS Task Inbox too — but EPMS has no page for them,
 * so they must jump out to VMS. Build arg VITE_VMS_URL; see epms/Dockerfile
 * (ARG **and** ENV — an ARG alone does not survive into the build) and the
 * epms-web build args in docker-compose.prod.yml.
 */
export const VMS_URL = (import.meta.env.VITE_VMS_URL as string | undefined) || 'http://localhost:5176'

/**
 * Full-page URL into the VMS frontend with the same `#__session=` handoff Portal
 * uses to launch VMS (consumed by vms/src/main.tsx's bootstrapFromPortal).
 */
export function vmsHandoffHref(path: string): string {
  const { token, refreshToken, user } = useAuthStore.getState()
  const base = `${VMS_URL}${path}`
  if (!token || !user) return base
  const session = encodeUtf8Base64(JSON.stringify({ token, refreshToken: refreshToken ?? '', user }))
  return `${base}#__session=${session}`
}

/**
 * Expense/OA microservice base URL (expense-api :8006) — unified invoice storage
 * + attachments. Browser-reachable absolute URL: the dev `/oa-api` Vite proxy is
 * dead inside the dockerized frontend (proxies to container-localhost:8006).
 */
export const EXPENSE_BASE = (import.meta.env.VITE_EXPENSE_API_URL as string | undefined) || 'http://localhost:8006'

/** Read the stored JWT access token from the Zustand-persist auth slice. */
function getToken(): string | null {
  return useAuthStore.getState().token
}

function buildHeaders(hasBody: boolean): HeadersInit {
  const h: Record<string, string> = {}
  if (hasBody) h['Content-Type'] = 'application/json'
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

type ParamScalar = string | number | boolean | null | undefined
// Array values are emitted as REPEATED query params (?k=a&k=b), which is what
// FastAPI's `list[...] = Query()` expects (e.g. /users/directory?department_ids).
type Params = Record<string, ParamScalar | ParamScalar[]>

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  params?: Params,
): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.origin)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null) continue
      if (Array.isArray(v)) {
        for (const item of v) {
          if (item !== undefined && item !== null) url.searchParams.append(k, String(item))
        }
      } else {
        url.searchParams.set(k, String(v))
      }
    }
  }

  const hasBody = body !== undefined
  const res = await fetch(url.toString(), {
    method,
    headers: buildHeaders(hasBody),
    body: hasBody ? JSON.stringify(body) : undefined,
  })

  if (res.status === 401) {
    // Attempt silent token refresh before giving up
    const { refreshToken, updateTokens, logout } = useAuthStore.getState()
    if (refreshToken) {
      try {
        const refreshRes = await fetch(new URL(`${BASE}/auth/refresh`, window.location.origin).toString(), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refreshToken }),
        })
        if (refreshRes.ok) {
          const tokens = await refreshRes.json() as { access_token: string; refresh_token: string }
          updateTokens(tokens.access_token, tokens.refresh_token)
          // Retry original request with the new token
          const retryRes = await fetch(url.toString(), {
            method,
            headers: { ...(hasBody ? { 'Content-Type': 'application/json' } : {}), Authorization: `Bearer ${tokens.access_token}` },
            body: hasBody ? JSON.stringify(body) : undefined,
          })
          if (retryRes.status === 204) return undefined as T
          if (retryRes.ok) return retryRes.json() as Promise<T>
        }
      } catch { /* fall through to logout */ }
    }
    globalSignOut()
    throw new Error('Unauthorized')
  }

  if (res.status === 204) return undefined as T

  if (!res.ok) {
    let detail = res.statusText
    try {
      const err = await res.json()
      if (typeof err.detail === 'string') {
        detail = err.detail
      } else if (Array.isArray(err.detail) && err.detail.length > 0) {
        // FastAPI 422 validation error: detail is [{loc, msg, type}, ...]
        detail = err.detail
          .map((e: { msg?: string; loc?: string[] }) => {
            const field = e.loc ? e.loc.slice(1).join('.') : ''
            return field ? `${field}: ${e.msg ?? ''}` : (e.msg ?? '')
          })
          .filter(Boolean)
          .join('; ')
      } else if (err.message) {
        detail = err.message
      }
    } catch { /* ignore parse error */ }
    throw new Error(detail)
  }

  return res.json() as Promise<T>
}

/**
 * Download an epms-api file response, triggering a browser Save-As dialog.
 * The server's Content-Disposition names the file unless `filename` overrides it.
 */
export async function downloadFile(path: string, params?: Params, filename?: string): Promise<void> {
  return downloadCsv(path, params, filename)
}

/** Download a CSV file, triggering a browser Save-As dialog. */
export async function downloadCsv(path: string, params?: Params, filename?: string): Promise<void> {
  const url = new URL(`${BASE}${path}`, window.location.origin)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v))
    }
  }
  const token = getToken()
  const headers: HeadersInit = {}
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(url.toString(), { headers })
  if (!res.ok) throw new Error(`Export failed: ${res.statusText}`)

  const blob = await res.blob()
  const blobUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = blobUrl
  // Use Content-Disposition filename or fallback
  const cd = res.headers.get('content-disposition') ?? ''
  const match = cd.match(/filename="([^"]+)"/)
  a.download = filename ?? match?.[1] ?? 'export.csv'
  a.click()
  URL.revokeObjectURL(blobUrl)
}

/** Download a file (blob) from finance-api with auth, triggering a Save-As. */
export async function downloadFinanceFile(path: string, params?: Params, filename?: string): Promise<void> {
  const url = new URL(`${FINANCE_BASE}/finance/v1${path}`)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v))
    }
  }
  const token = getToken()
  const headers: HeadersInit = {}
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(url.toString(), { headers })
  if (!res.ok) throw new Error(`Export failed: ${res.statusText}`)

  const blob = await res.blob()
  const blobUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = blobUrl
  const cd = res.headers.get('content-disposition') ?? ''
  const match = cd.match(/filename="([^"]+)"/)
  a.download = filename ?? match?.[1] ?? 'export.xlsx'
  a.click()
  URL.revokeObjectURL(blobUrl)
}

export const api = {
  get:    <T>(path: string, params?: Params)          => request<T>('GET',    path, undefined, params),
  post:   <T>(path: string, body?: unknown)           => request<T>('POST',   path, body),
  put:    <T>(path: string, body?: unknown)           => request<T>('PUT',    path, body),
  patch:  <T>(path: string, body?: unknown)           => request<T>('PATCH',  path, body),
  delete: <T = void>(path: string, body?: unknown)    => request<T>('DELETE', path, body),
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


// ── Budget-api client (separate base URL — :8007) ─────────────────────────────

async function budgetRequest<T>(
  method: string, path: string, body?: unknown, params?: Params,
): Promise<T> {
  const url = new URL(`${BUDGET_BASE}/api/v1${path}`)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v))
    }
  }
  const hasBody = body !== undefined
  const token = getToken()
  const headers: Record<string, string> = {}
  if (hasBody) headers['Content-Type'] = 'application/json'
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(url.toString(), {
    method, headers, body: hasBody ? JSON.stringify(body) : undefined,
  })
  if (res.status === 204) return undefined as T
  if (!res.ok) {
    let detail = res.statusText
    try {
      const err = await res.json()
      if (typeof err.detail === 'string') detail = err.detail
    } catch { /* ignore */ }
    throw new Error(`budget-api: ${detail}`)
  }
  return res.json() as Promise<T>
}

export const budgetApi = {
  get:    <T>(path: string, params?: Params)       => budgetRequest<T>('GET',    path, undefined, params),
  post:   <T>(path: string, body?: unknown)        => budgetRequest<T>('POST',   path, body),
  put:    <T>(path: string, body?: unknown)        => budgetRequest<T>('PUT',    path, body),
  patch:  <T>(path: string, body?: unknown)        => budgetRequest<T>('PATCH',  path, body),
  delete: <T = void>(path: string, body?: unknown) => budgetRequest<T>('DELETE', path, body),
}


// ── MDM-api client (separate base URL — :8002) ────────────────────────────────

async function mdmRequest<T>(
  method: string, path: string, body?: unknown, params?: Params,
): Promise<T> {
  const url = new URL(`${MDM_BASE}/mdm/v1${path}`)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v))
    }
  }
  const hasBody = body !== undefined
  const token = getToken()
  const headers: Record<string, string> = {}
  if (hasBody) headers['Content-Type'] = 'application/json'
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(url.toString(), {
    method, headers, body: hasBody ? JSON.stringify(body) : undefined,
  })
  if (res.status === 204) return undefined as T
  if (!res.ok) {
    let detail = res.statusText
    try {
      const err = await res.json()
      if (typeof err.detail === 'string') detail = err.detail
    } catch { /* ignore */ }
    throw new Error(`mdm-api: ${detail}`)
  }
  return res.json() as Promise<T>
}

export const mdmApi = {
  get:    <T>(path: string, params?: Params)       => mdmRequest<T>('GET',    path, undefined, params),
  post:   <T>(path: string, body?: unknown)        => mdmRequest<T>('POST',   path, body),
}

// ── finance-api (:8004) — bank/card accounts, vendor credits ──────────────────
async function financeRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${FINANCE_BASE}/finance/v1${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (res.status === 204) return undefined as T
  if (!res.ok) {
    let detail = res.statusText
    try { const err = await res.json(); if (typeof err.detail === 'string') detail = err.detail } catch { /* ignore */ }
    const error = new Error(`finance-api: ${detail}`) as Error & { status?: number }
    error.status = res.status
    throw error
  }
  return res.json() as Promise<T>
}

export const financeApi = {
  get:  <T>(path: string)                 => financeRequest<T>('GET',  path),
  post: <T>(path: string, body?: unknown) => financeRequest<T>('POST', path, body),
}
