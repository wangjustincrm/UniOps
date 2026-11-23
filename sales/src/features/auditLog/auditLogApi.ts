import { createResourceClient, type AuditLogDetailShape, type ListResource, type Page, type QuerySpec } from '@/platform-ui'
import { api } from '@/lib/api'

// Operations → Audit log (sales36, ≈ ABP Pro): sales.config for now (the user, 2026-11-23).
export const AUDIT = 'sales.config'

export interface AuditLogRow {
  /** A number for the list's row key (from the UUID); `uuid` is the log's real id. */
  id: number
  uuid: string
  kind: string
  name: string | null
  correlation_id: string
  user_name: string | null
  role: string | null
  client_ip: string | null
  http_method: string | null
  route: string | null
  http_status: number | null
  execution_time: string
  execution_duration_ms: number | null
  error_code: string | null
  has_exception: boolean
  entity_changes: number
}
export interface EntityChangeRow {
  id: number; at: string; user_name: string; action: string; entity: string; entity_id: string | null
  reason: string | null; audit_log_id: string | null; before?: Record<string, unknown> | null; after?: Record<string, unknown> | null
}

// The logs' ids are UUIDs; the list keys rows by a number made from the UUID (its first 48 bits: never collides in
// practice), and opens a log by its UUID.
const rowId = (uuid: string) => parseInt(uuid.replace(/-/g, '').slice(0, 12), 16)
const raw = createResourceClient<AuditLogRow & { id: unknown }, never>(api, '/audit-logs')
export const auditLogsClient: ListResource<AuditLogRow> = {
  basePath: raw.basePath,
  meta: raw.meta,
  list: async (spec: QuerySpec): Promise<Page<AuditLogRow>> => {
    const page = await raw.list(spec)
    return { ...page, items: page.items.map((r) => ({ ...r, uuid: String(r.id), id: rowId(String(r.id)) })) }
  },
}
export const entityChangesClient = createResourceClient<EntityChangeRow, never>(api, '/entity-changes')
export const fetchAuditLog = (uuid: string) => api.get<AuditLogDetailShape>(`/audit-logs/${uuid}/detail`)
