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
}
