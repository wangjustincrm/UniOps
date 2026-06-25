/**
 * Reports service — thin wrappers around the shared downloadCsv helper.
 * All actual HTTP + auth logic lives in src/lib/api.ts.
 */
import { downloadCsv } from '@/lib/api'

export type ReportType = 'pr' | 'po' | 'gr' | 'invoices' | 'pa' | 'budget' | 'vendors'

export interface ReportFilters {
  status?: string
  vendor_id?: string
  date_from?: string
  date_to?: string
  cost_center_id?: string
  po_type?: number
  gr_type?: string
  pa_type?: string
  category?: string
  active_only?: boolean
  [key: string]: string | number | boolean | undefined
}

const today = () => new Date().toISOString().slice(0, 10)

export const reportService = {
  downloadPr:       (f?: ReportFilters) => downloadCsv('/reports/pr',       f, `pr-report-${today()}.csv`),
  downloadPo:       (f?: ReportFilters) => downloadCsv('/reports/po',       f, `po-report-${today()}.csv`),
  downloadGr:       (f?: ReportFilters) => downloadCsv('/reports/gr',       f, `gr-report-${today()}.csv`),
  downloadInvoices: (f?: ReportFilters) => downloadCsv('/reports/invoices', f, `invoice-report-${today()}.csv`),
  downloadPa:       (f?: ReportFilters) => downloadCsv('/reports/pa',       f, `pa-report-${today()}.csv`),
  downloadBudget:   (f?: ReportFilters) => downloadCsv('/reports/budget',   f, `budget-report-${today()}.csv`),
  downloadVendors:  (f?: ReportFilters) => downloadCsv('/reports/vendors',  f, `vendor-report-${today()}.csv`),
}
