import type { ApiEvent } from '@/services/pr'
import { api, fetchAllPages } from '@/lib/api'

export type AgreementStatus =
  | 'draft'
  | 'submitted'
  | 'in_review'
  | 'active'
  | 'expired'
  | 'closed'
  | 'cancelled'
  | 'returned'
  | 'rejected'

export type AgreementType = 'house_account' | 'recurring' | 'milestone'

export type AgreementAction = 'submit' | 'approve' | 'return' | 'cancel'

export type RecurringType = 'weekly' | 'monthly' | 'quarterly' | 'yearly'

// Decimal fields (expected_amount, tolerance_pct, amount_pct) arrive from the
// API as JSON strings — same Pydantic-Decimal serialisation as ApiAgreement.
// Number()-coerce before any arithmetic/comparison.
export interface ApiScheduleRow {
  id: string
  agreement_id: string
  schedule_type: 'period' | 'milestone'
  sequence: number
  expected_amount: string | null
  expected_date: string | null
  expected_timing: string | null
  status: 'pending' | 'received' | 'overdue' | 'waived'
  invoice_id: string | null
  period_label: string | null
  tolerance_pct: string | null
  overdue_after_days: number | null
  milestone_name: string | null
  amount_pct: string | null
  accepted_by: string | null
  accepted_at: string | null
}

export interface MilestoneRowIn {
  milestone_name: string
  expected_timing?: string | null
  expected_amount?: string | null
  amount_pct?: string | null
}

// Decimal fields (not_to_exceed, consumed_amount, tax_rate) arrive from the API
// as JSON strings — Pydantic serialises Decimal that way. Number()-coerce at
// every arithmetic/comparison site (see AgreementListPage/DetailPage).
export interface ApiAgreement {
  id: string
  number: string
  title: string
  agreement_type: AgreementType
  contract_no?: string | null
  contact_email?: string | null
  vendor_id: string
  vendor_name: string
  vendor_reference?: string | null
  valid_from: string
  valid_to: string
  grace_days: number
  not_to_exceed: string | null
  consumed_amount: string
  currency: string
  tax_code?: string | null
  tax_rate: string | null
  department_id?: string | null
  budget_code?: string | null
  owner_id?: string | null
  cost_center_id?: string | null
  recurring_type?: RecurringType | null
  expected_invoice_day?: number | null
  anchor_month?: number | null
  expected_amount_per_period?: string | null
  tolerance_pct?: string | null
  overdue_after_days?: number | null
  status: AgreementStatus
  approval_step_idx: number
  notes?: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface CreateAgreementBody {
  title: string
  agreement_type: AgreementType
  vendor_id: string
  contract_no?: string
  contact_email?: string
  vendor_reference?: string
  valid_from: string
  valid_to: string
  grace_days?: number
  not_to_exceed?: number
  currency?: string
  tax_code?: string | null
  tax_rate?: number | null
  department_id?: string
  budget_code?: string
  owner_id?: string
  notes?: string
  cost_center_id?: string
  recurring_type?: RecurringType
  expected_invoice_day?: number
  anchor_month?: number
  expected_amount_per_period?: number
  tolerance_pct?: number
  overdue_after_days?: number
  milestones?: MilestoneRowIn[]
}

// PATCH /agreements/{id} applies `model_dump(exclude_unset=True)`, so an
// omitted key leaves the stored value alone and an explicit `null` clears the
// column. Every optional field here is therefore `| null`: the edit form has to
// be able to CLEAR Not-to-Exceed / tax code / department / owner, and sending
// `undefined` (which JSON.stringify drops) silently kept the old value. Same
// convention PoEditPage already follows for tax_code.
export interface UpdateAgreementBody {
  title?: string
  contract_no?: string | null
  contact_email?: string | null
  vendor_reference?: string | null
  valid_from?: string
  valid_to?: string
  grace_days?: number
  not_to_exceed?: number | null
  tax_code?: string | null
  tax_rate?: number | null
  department_id?: string | null
  budget_code?: string | null
  owner_id?: string | null
  notes?: string | null
  cost_center_id?: string | null
  recurring_type?: RecurringType | null
  expected_invoice_day?: number | null
  anchor_month?: number | null
  expected_amount_per_period?: number | null
  tolerance_pct?: number | null
  overdue_after_days?: number | null
  milestones?: MilestoneRowIn[] | null
}

export interface AgreementActionBody {
  action: AgreementAction
  comment?: string
}

export interface AgreementFilters {
  status?: AgreementStatus
  vendor_id?: string
  agreement_type?: AgreementType
  search?: string
  page?: number
  page_size?: number
}

export interface AgreementListResponse {
  items: ApiAgreement[]
  total: number
}

export const agreementService = {
  list: (filters?: AgreementFilters) =>
    api.get<AgreementListResponse>('/agreements', filters as Record<string, string | number | boolean | null | undefined>),

  listAll: (filters?: Omit<AgreementFilters, 'page' | 'page_size'>): Promise<AgreementListResponse> =>
    fetchAllPages((page, page_size) => agreementService.list({ ...filters, page, page_size })),

  get: (id: string) =>
    api.get<ApiAgreement>(`/agreements/${id}`),

  create: (body: CreateAgreementBody) =>
    api.post<ApiAgreement>('/agreements', body),

  update: (id: string, body: UpdateAgreementBody) =>
    api.patch<ApiAgreement>(`/agreements/${id}`, body),

  action: (id: string, body: AgreementActionBody) =>
    api.post<ApiAgreement>(`/agreements/${id}/action`, body),

  schedule: (id: string) =>
    api.get<{ items: ApiScheduleRow[] }>(`/agreements/${id}/schedule`),

  confirmPeriod: (agreementId: string, rowId: string) =>
    api.post<ApiScheduleRow>(`/agreements/${agreementId}/schedule/${rowId}/confirm`),

  // The approval trail, actor names resolved server-side (GET
  // /agreements/{id}/events). Same payload as GET /pr/{id}/events, because the
  // detail pages share one ApprovalTimeline component.
  events: (id: string) =>
    api.get<ApiEvent[]>(`/agreements/${id}/events`),
}
