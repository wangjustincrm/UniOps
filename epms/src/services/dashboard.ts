import { api } from '@/lib/api'

// ── Matches backend app/schemas/dashboard.py ────────────────────────────────

export interface KpiCard {
  title: string
  value: string
  subtitle?: string
  alert: boolean
}

export interface ApprovalItem {
  id: string
  doc_type: string      // PR | PO | PA
  number: string
  title: string
  amount: number
  currency: string
  status: string
  submitted_days_ago: number
  href: string
  requester_name: string
  dept_name: string
}

export interface BudgetGroupRow {
  l1_code: string
  l1_name: string
  annual_budget: number
  committed: number
  actual_spent: number
  available: number
  utilisation_pct: number
}

export interface BudgetOverview {
  total_budget: number
  total_committed: number
  total_spent: number
  utilisation_pct: number
  groups: BudgetGroupRow[]
}

export interface PaOverview {
  total_count: number
  pending_count: number
  pending_value: number
  processed_value: number
}

export interface PoRow {
  id: string
  number: string
  title: string
  vendor_name: string
  total: number
  currency: string
  status: string
  created_at: string
}

export interface GrRow {
  id: string
  number: string
  gr_type: string
  po_number: string
  vendor_name: string
  currency: string
  status: string
  created_at: string
}

export interface InvoiceRow {
  id: string
  internal_ref: string
  vendor_name: string
  po_number: string | null
  total_amount: number
  currency: string
  status: string
  created_at: string
}

export interface VendorRow {
  id: string
  code: string
  name: string
  category: string
  contact_name: string
  payment_terms: string
  is_active: boolean
  created_at: string
}

export interface PaRow {
  id: string
  pa_number: string
  vendor_name: string
  po_number: string
  // Every PO the payment settles (a PA may cover several); po_number above is
  // just the primary. Optional so a response cached from before multi-PO
  // shipped still type-checks.
  po_numbers?: string[]
  pa_type: string
  payment_amount: number
  currency: string
  status: string
  created_at: string
}

export interface PipelineGr { id: string; number: string; gr_type: string; status: string }
export interface PipelineInvoice { id: string; internal_ref: string; status: string; total_amount: number }
export interface PipelinePa { id: string; pa_number: string; status: string; payment_amount: number }
export interface PipelinePo {
  id: string; number: string; status: string; total: number; vendor_name: string
  grs: PipelineGr[]; invoices: PipelineInvoice[]; pas: PipelinePa[]
}
export interface PrPipelineItem {
  id: string; number: string; title: string; status: string
  amount: number; currency: string; pos: PipelinePo[]
}

export interface StatusBreakdown {
  pr: Record<string, number>
  po: Record<string, number>
  gr: Record<string, number>
  pa: Record<string, number>
}

export interface RoleCount { role: string; count: number }

export interface DashboardResponse {
  role: string
  kpis: KpiCard[]
  pending_approvals?: ApprovalItem[]
  budget_overview?: BudgetOverview
  pa_overview?: PaOverview
  recent_pos?: PoRow[]
  recent_grs?: GrRow[]
  recent_invoices?: InvoiceRow[]
  recent_vendors?: VendorRow[]
  pa_in_review?: PaRow[]
  pr_pipeline?: PrPipelineItem[]
  status_breakdown?: StatusBreakdown
  users_by_role?: RoleCount[]
}

export const dashboardService = {
  get: () => api.get<DashboardResponse>('/dashboard'),
}
