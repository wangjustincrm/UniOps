import { financeApi } from '@/lib/api'

export interface QboRun {
  id: string; mode: string; status: string
  started_at: string | null; finished_at: string | null
  counters: Record<string, { inserted: number; updated: number; deleted?: number }>
  watermarks: Record<string, string | null>; error: string | null
}
export interface QboStatus {
  can_sync: boolean; configured: boolean
  current_run: QboRun | null; last_run: QboRun | null
}
export interface QboPage<T = Record<string, unknown>> {
  items: T[]; total: number; page: number; page_size: number
}
export interface QboDetail {
  header: Record<string, unknown>
  lines: Record<string, unknown>[]
  attachments: { attachment_qbo_id: string; txn_type: string | null }[]
}
export interface QboBackfillResult {
  updated: { code: string; name: string; email: string }[]
  skipped_has_value: number
  ambiguous: { side: 'qbo' | 'epms'; name: string }[]
  unmatched_qbo: string[]
}

export const ENTITY_TABS: { slug: string; label: string }[] = [
  { slug: 'bills', label: 'Bills' },
  { slug: 'bill-payments', label: 'Bill Payments' },
  { slug: 'vendor-credits', label: 'Vendor Credits' },
  { slug: 'purchases', label: 'Purchases' },
  { slug: 'invoices', label: 'Invoices' },
  { slug: 'payments', label: 'Payments' },
  { slug: 'credit-memos', label: 'Credit Memos' },
  { slug: 'deposits', label: 'Deposits' },
  { slug: 'transfers', label: 'Transfers' },
  { slug: 'journal-entries', label: 'Journal Entries' },
  { slug: 'vendors', label: 'Vendors' },
  { slug: 'accounts', label: 'Accounts' },
]

export const qboApi = {
  status: () => financeApi.get<QboStatus>('/qbo/sync/status'),
  triggerSync: (mode: 'full' | 'incremental', confirm?: string) =>
    financeApi.post<{ status: string }>('/qbo/sync', { mode, confirm }),
  browse: (entity: string, params: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') qs.set(k, String(v)) })
    return financeApi.get<QboPage>(`/qbo/${entity}?${qs.toString()}`)
  },
  detail: (entity: string, id: string) => financeApi.get<QboDetail>(`/qbo/${entity}/${id}`),
  /** CSV of the whole filtered population, not just the visible page. Pass the
   * same `q` the table is showing so the file matches the screen. */
  exportPath: (entity: string, q?: string) =>
    `/qbo/${entity}/export${q ? `?q=${encodeURIComponent(q)}` : ''}`,
  backfillVendorEmails: () =>
    financeApi.post<QboBackfillResult>('/qbo/vendor-emails/backfill', {}),
  fileUrl: (attachmentId: string) => `/qbo/attachments/${attachmentId}/file`,
}
