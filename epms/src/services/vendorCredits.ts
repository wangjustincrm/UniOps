import { financeApi } from '@/lib/api'

export interface VendorCredit {
  id: string
  credit_number: string
  vendor_id: string
  vendor_name: string
  vendor_credit_number: string
  credit_date: string
  currency: string
  /** Decimal-as-string — Pydantic serialises Decimal to JSON string. Always Number() before arithmetic. */
  amount: string
  tax_amount: string
  total_amount: string
  applied_amount: string
  remaining_amount: string
  status: 'pending_review' | 'available' | 'exhausted' | 'void'
  po_id: string | null
  po_number: string | null
  line_items: unknown[]
  file_name: string | null
  notes: string | null
  source: 'upload' | 'qbo_import'
  opening_balance: boolean
  uploaded_by: string
  uploaded_by_name: string | null
  uploaded_at: string
  reviewed_by: string | null
  reviewed_by_name: string | null
  reviewed_at: string | null
  review_note: string | null
}

export interface VendorCreditListResponse {
  items: VendorCredit[]
  total: number
}

export interface CreateVendorCreditBody {
  vendor_id: string
  vendor_name: string
  vendor_credit_number: string
  credit_date: string
  currency: string
  amount: number
  tax_amount: number
  po_id?: string | null
  po_number?: string | null
  line_items?: unknown[]
  file_name?: string | null
  notes?: string | null
}

const qs = (params: Record<string, string | undefined>) => {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) sp.set(k, v)
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export const vendorCreditsService = {
  list:    (status?: string) =>
    financeApi.get<VendorCreditListResponse>(`/vendor-credits${qs({ status })}`),
  get:     (id: string) =>
    financeApi.get<VendorCredit>(`/vendor-credits/${id}`),
  create:  (body: CreateVendorCreditBody) =>
    financeApi.post<VendorCredit>('/vendor-credits', body),
  approve: (id: string, note?: string) =>
    financeApi.post<VendorCredit>(`/vendor-credits/${id}/approve`, { note: note ?? null }),
  reject:  (id: string, note: string) =>
    financeApi.post<VendorCredit>(`/vendor-credits/${id}/reject`, { note }),
  void:    (id: string, note: string) =>
    financeApi.post<VendorCredit>(`/vendor-credits/${id}/void`, { note }),
}
