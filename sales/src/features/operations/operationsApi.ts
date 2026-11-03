import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'

// Mirrors sales-api app/schemas/operations.py (hand-written, see partnersApi.ts).
export const DOCUMENT_KINDS = ['850_JSON', '940_EDI', '943_EDI', '944_EDI', '945_EDI', '997_EDI', '810_JSON',
  'INVOICE_EMAIL', 'SHOPIFY_ORDER_JSON', 'OTHER'] as const
export const INBOUND_STATUSES = ['Pending', 'Processing', 'Processed', 'Failed', 'Skipped', 'Duplicate'] as const

export interface DocumentRead {
  id: number
  direction: 'IN' | 'OUT'
  origin: string
  system: string | null
  connection_id: number | null
  kind: string
  remote_name: string | null
  size: number | null
  remote_mtime: string | null
  sha256: string
  has_content: boolean
  processing_status: string | null
  file_status: string | null
  ack_status: string | null
  isa13: string | null
  gs06: string | null
  st02: string | null
  partner_code: string | null
  error: string | null
  created_at: string
  created_by_name: string
}

/** One connection's pass (FR-OB-76): listed = the sum of the other six. */
export interface PassReport {
  listed: number
  too_new: number
  already_here: number
  not_ours: number
  stored: number
  duplicates: number
  failed: number
  status: 'ok' | 'latched' | 'list_failed'
}

export interface RunRead {
  id: number
  job: string
  trigger: 'SCHEDULE' | 'MANUAL'
  status: 'RUNNING' | 'SUCCEEDED' | 'PARTIAL' | 'FAILED'
  started_at: string
  heartbeat_at: string
  finished_at: string | null
  /** Before per-connection scheduling one cycle held every connection (`connections`); now a
   *  connection's run holds its own `pass` (and `connection`, its name), and every run a `text`. */
  summary: { connections?: Record<string, PassReport>; pass?: PassReport; connection?: string; text?: string;
    outbox?: Record<string, number> } | null
  error: string | null
  /** "connection:<id>" | "system" (none: the one cycle, before per-connection scheduling). */
  scope: string | null
}

export interface LogRead {
  id: number
  at: string
  run_id: number | null
  level: 'ERROR' | 'WARNING' | 'INFO'
  step: string
  connection_id: number | null
  document_id: number | null
  order_id: number | null
  transfer_id: number | null
  message: string
  context: Record<string, unknown> | null
}

export const documentsClient = createResourceClient<DocumentRead, never>(api, '/documents')
export const runsClient = createResourceClient<RunRead, never>(api, '/runs')
export const logClient = createResourceClient<LogRead, never>(api, '/log')
export const downloadDocument = (id: number, name: string) => api.download(`/documents/${id}/raw`, name)
export const downloadOriginal = (d: DocumentRead) => downloadDocument(d.id, d.remote_name ?? `document-${d.id}`)
export const runNow = () => api.post<{ run_id: number }>('/runs/run-now')
/** The system job alone, now (Health). */
export const runSystemNow = () => api.post<{ run_id: number }>('/runs/run-now/system')

/** What the run was for: the connection's name, the system job, or the one cycle (before 2026-11-02). */
export function runFor(r: RunRead): string {
  if (r.scope === 'system') return 'System job'
  if (r.scope?.startsWith('connection:')) return r.summary?.connection ?? r.scope
  return 'Cycle'
}
