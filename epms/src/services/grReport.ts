import { api, downloadFile } from '@/lib/api'

/** Today as YYYY-MM-DD in the reader's own zone (not UTC). */
function localDay(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/**
 * Warehouse receiving report — one row per physically received line item.
 *
 * Dates arrive as plain `YYYY-MM-DD` strings already resolved to the plant's
 * local day by the API. Render them with `formatDateOnly` / as-is; never through
 * `new Date(...)`, which reads a date-only string as UTC midnight and shows the
 * previous day in a UTC-4 browser.
 */
export interface ReceivingReportRow {
  gr_id: string
  gr_number: string
  po_id: string
  po_number: string
  material_id: string | null
  description: string
  supplier: string
  unit: string
  quantity: string
  department: string | null
  requested_by: string | null
  date_ordered: string | null
  arrival_date: string | null
  left_warehouse_date: string | null
  warehouse_receiver: string | null
  person_accepting: string | null
  lead_time_days: number | null
}

export interface ReceivingReportResponse {
  items: ReceivingReportRow[]
  total: number
  truncated: boolean
}

export interface ReceivingReportFilters {
  // Index signature so the object satisfies the query-param type `api.get` and
  // `downloadFile` take; without it every call site needs a cast.
  [key: string]: string | undefined
  date_from?: string
  date_to?: string
  department_id?: string
  vendor_id?: string
  search?: string
}

export const grReportService = {
  receiving: (filters?: ReceivingReportFilters) =>
    api.get<ReceivingReportResponse>('/gr/receiving-report', filters),

  exportReceiving: (filters?: ReceivingReportFilters) =>
    // The filename is passed explicitly: EPMS talks to epms-api cross-origin,
    // and a browser hides Content-Disposition from such a response unless the
    // server exposes it — leaving the shared helper to fall back on
    // "export.csv", an xlsx body under a CSV name that Excel then refuses.
    downloadFile('/gr/receiving-report/export', filters, `receiving-report-${localDay()}.xlsx`),
}
