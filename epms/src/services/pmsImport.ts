import { api } from '@/lib/api'

export type PmsPhase = 'full' | 'incremental'
export type PmsStatus = 'running' | 'success' | 'error'

export interface PmsReport {
  dry_run: boolean
  inserted: Record<string, number>
  updated: Record<string, number>
  skipped_existing: Record<string, number>
  skipped_conflict: Record<string, number>
  pr_missing_number: number
  pa_orphan_no_po: number
  invoices_no_vendor: number
  invoices_discarded_no_po?: number
  created_vendors: number
  applier_fallback: number
  unmatched_vendors: { count: number; sample: string[] }
  unmapped_cost_centers: { count: number; sample: string[] }
  unmatched_appliers: { count: number; sample: string[] }
  xwalk_emails_missing: { count: number; sample: string[] }
  watermark_advanced_to?: string
  attachments?: {
    uploaded: number
    skipped_existing: number
    no_invoice: number
    failed: number
    samples_failed: string[]
  }
}

export interface PmsRun {
  id: string
  phase: PmsPhase
  dry_run: boolean
  triggered_by: string
  status: PmsStatus
  started_at: string
  finished_at: string | null
  step: string
  error: string | null
  report?: PmsReport | null
}

export interface PmsConfig {
  sharepoint_configured: boolean
  site: string
  user: string | null
  last_sync: string | null
}

export const pmsImportService = {
  getConfig: () => api.get<PmsConfig>('/pms-import/config'),
  getStatus: () => api.get<{ current: PmsRun | null }>('/pms-import/status'),
  listRuns: () => api.get<{ runs: PmsRun[] }>('/pms-import/runs'),
  getRun: (id: string) => api.get<PmsRun>(`/pms-import/runs/${id}`),
  run: (phase: PmsPhase, dry_run: boolean) =>
    api.post<PmsRun>('/pms-import/run', { phase, dry_run }),
}
