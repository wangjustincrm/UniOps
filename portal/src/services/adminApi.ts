// Multi-service admin client. Each business API (EPMS, OA, VMS) exposes the same
// /api/v1/admin/* surface; this client merges their entity catalogs and routes
// every call to the right service by the entity's `system` tag.

export interface FieldSpec {
  name: string
  type: 'string' | 'number' | 'decimal' | 'bool' | 'date' | 'datetime' | 'uuid' | 'json' | 'enum'
  editable: boolean
  label: string
  options: string[] | null
}

export interface EntitySchema {
  key: string
  label: string
  system: string
  number_field: string
  list_columns: string[]
  search_fields: string[]
  order_by: string
  allow_edit?: boolean   // false = delete-only (no edit). Missing → editable.
  fields: FieldSpec[]
}

export interface ListResult { items: Record<string, unknown>[]; total: number }
export type CascadeSummary = Record<string, number>

// ── Per-system API host + path prefix (mirror lib/api.ts defaults) ─────────────
// Most services mount admin under /api/v1; finance-api mounts under /finance/v1.
const HOST: Record<string, string> = {
  epms:    (import.meta.env.VITE_EPMS_API_URL    as string | undefined) || 'http://localhost:8000',
  oa:      (import.meta.env.VITE_OA_API_URL      as string | undefined) || 'http://localhost:8006',
  vms:     (import.meta.env.VITE_VMS_API_URL     as string | undefined) || 'http://localhost:8008',
  finance: (import.meta.env.VITE_FINANCE_API_URL as string | undefined) || 'http://localhost:8004',
}
const PREFIX: Record<string, string> = {
  epms: '/api/v1', oa: '/api/v1', vms: '/api/v1', finance: '/finance/v1',
}
const SYSTEMS = Object.keys(HOST)

function getToken(): string | null {
  try {
    const raw = localStorage.getItem('portal-auth')
    return raw ? (JSON.parse(raw)?.state?.token ?? null) : null
  } catch { return null }
}

async function request<T>(system: string, method: string, path: string, body?: unknown): Promise<T> {
  const host = HOST[system]
  if (!host) throw new Error(`Unknown system '${system}'`)
  const token = getToken()
  const res = await fetch(`${host}${PREFIX[system]}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    // A stuck/unreachable host (e.g. a build-time fallback that silently
    // pointed at localhost) must not hang forever — Promise.allSettled in
    // entities() only skips a system if its request actually settles.
    signal: AbortSignal.timeout(15000),
  })
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({ detail: res.statusText }))
    const detail = errBody?.detail
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`)
  }
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T
  return res.json()
}

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export const adminApi = {
  /** Merge entity catalogs from every service; skip any that are down/forbidden. */
  entities: async (): Promise<EntitySchema[]> => {
    const results = await Promise.allSettled(
      SYSTEMS.map((system) => request<EntitySchema[]>(system, 'GET', '/admin/entities')),
    )
    return results.flatMap((r) => (r.status === 'fulfilled' ? r.value : []))
  },
  list: (system: string, entity: string, params: { page: number; page_size: number; search?: string }) =>
    request<ListResult>(system, 'GET', `/admin/${entity}${qs(params)}`),
  get: (system: string, entity: string, id: string) =>
    request<Record<string, unknown>>(system, 'GET', `/admin/${entity}/${id}`),
  edit: (system: string, entity: string, id: string, patch: Record<string, unknown>) =>
    request<Record<string, unknown>>(system, 'PATCH', `/admin/${entity}/${id}`, patch),
  preview: (system: string, entity: string, id: string) =>
    request<{ preview: boolean; cascade: CascadeSummary }>(system, 'DELETE', `/admin/${entity}/${id}?preview=1`)
      .then((r) => r.cascade),
  remove: (system: string, entity: string, id: string) =>
    request<{ cascade: CascadeSummary }>(system, 'DELETE', `/admin/${entity}/${id}`).then((r) => r.cascade),
  bulkDelete: (system: string, entity: string, ids: string[]) =>
    request<{ deleted: number; cascade: CascadeSummary }>(system, 'POST', `/admin/${entity}/bulk-delete`, { ids }),
  /** Collect every matching record id across all pages (for "select all matching"). */
  allIds: async (system: string, entity: string, search?: string): Promise<string[]> => {
    const pageSize = 200
    const ids: string[] = []
    for (let page = 1; ; page++) {
      const res = await request<ListResult>(system, 'GET', `/admin/${entity}${qs({ page, page_size: pageSize, search })}`)
      ids.push(...res.items.map((r) => String(r.id)))
      if (page * pageSize >= res.total || res.items.length === 0) break
    }
    return ids
  },
}
