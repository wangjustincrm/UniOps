// Multi-service admin client. Each business API (EPMS, OA, VMS) exposes the same
// /api/v1/admin/* surface; this client merges their entity catalogs and routes
// every call to the right service by the entity's `system` tag.

export interface FieldSpec {
  name: string
  type: 'string' | 'number' | 'decimal' | 'bool' | 'date' | 'datetime' | 'uuid' | 'json' | 'enum' | 'reference'
  editable: boolean
  label: string
  options: string[] | null
  ref_source?: string | null
  ref_name_field?: string | null
}

export interface ChildSchema {
  table_label: string
  fk_field: string
  fields: FieldSpec[]
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
  child?: ChildSchema | null
}

export interface RefHit { id: string; label: string }

export interface WorkflowStep { id: string; role: string; label: string }
export interface WorkflowSteps {
  doc_type: string
  steps: WorkflowStep[]
  open_approve_tasks: number
}

export interface ApprovalStateResult {
  approval_step_idx: number | null
  reassigned_open_tasks?: number
  closed_stale_tasks?: number
  /** Present only when a step change triggered an engine resync. */
  routing_resync?: string
  resync_actions?: string[]
  resync_warning?: boolean
  final_step?: number | null
  /** The number that decides whether an approval button exists at all. */
  open_approve_tasks: number
}

export interface ListResult { items: Record<string, unknown>[]; total: number }
export type CascadeSummary = Record<string, number>

// ── Approval delegation (Task 12) ───────────────────────────────────────────
// approval-api's /delegations endpoints have no browser-facing subdomain/CORS
// (server-to-server only — same reasoning documented on the /approval-routing
// proxy in epms-api/app/api/v1/config.py), so epms-api gateways them at
// /config/approval-delegations. Shape mirrors approval-api's DelegationOut /
// DelegationCreate / DelegationUpdate schemas exactly.

export interface DelegationOut {
  id: string
  delegator_user_id: string
  delegate_user_id: string
  delegator_name: string | null
  delegate_name: string | null
  start_date: string   // YYYY-MM-DD, inclusive
  end_date: string      // YYYY-MM-DD, inclusive
  note: string | null
  revoked_at: string | null
  revoked_by: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface DelegationCreate {
  delegator_user_id: string
  delegate_user_id: string
  start_date: string
  end_date: string
  note?: string | null
}

export interface DelegationUpdate {
  start_date?: string
  end_date?: string
  note?: string | null
}

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
  lookup: (system: string, source: string, q: string) =>
    request<RefHit[]>(system, 'GET', `/admin/lookup/${source}${qs({ q, limit: 20 })}`),
  editWithOptions: (system: string, entity: string, id: string,
                    patch: Record<string, unknown>, opts?: { regeneratePoNumber?: boolean }) =>
    request<Record<string, unknown>>(system, 'PATCH',
      `/admin/${entity}/${id}${qs({ regenerate_po_number: opts?.regeneratePoNumber ? 1 : 0 })}`, patch),
  editApprovalState: (system: string, entity: string, id: string, patch: Record<string, unknown>) =>
    request<ApprovalStateResult>(system, 'PATCH', `/admin/${entity}/${id}/approval-state`, patch),
  workflowSteps: (system: string, entity: string, id: string) =>
    request<WorkflowSteps>(system, 'GET', `/admin/${entity}/${id}/workflow-steps`),
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
  // ── Approval delegation ───────────────────────────────────────────────────
  listDelegations: () =>
    request<DelegationOut[]>('epms', 'GET', '/config/approval-delegations'),
  createDelegation: (body: DelegationCreate) =>
    request<DelegationOut>('epms', 'POST', '/config/approval-delegations', body),
  updateDelegation: (id: string, body: DelegationUpdate) =>
    request<DelegationOut>('epms', 'PATCH', `/config/approval-delegations/${id}`, body),
  revokeDelegation: (id: string) =>
    request<DelegationOut>('epms', 'POST', `/config/approval-delegations/${id}/revoke`),
}
