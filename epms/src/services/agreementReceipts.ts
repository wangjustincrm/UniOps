import { api, EXPENSE_BASE } from '@/lib/api'
import { useAuthStore } from '@/stores/auth.store'

// Decimal fields (amount, tax_amount, total_amount) arrive from the API as
// JSON strings — same Pydantic-Decimal serialisation as ApiAgreement /
// ApiScheduleRow (see services/agreement.ts). Number()-coerce before any
// arithmetic/comparison.
export type ReceiptStatus = 'pending_ap_review' | 'open' | 'reconciled' | 'voided' | 'rejected'

// A house_account agreement isn't necessarily a counter-pickup account — it
// may be a monthly delivery or an outsourced service instead. Mirrors the
// backend discriminator (epms-api/app/schemas/agreement_receipt.py
// _RECEIPT_TYPES) — keep in sync if the backend set ever changes.
export type ReceiptType = 'counter_slip' | 'delivery' | 'service'

export const RECEIPT_TYPE_LABELS: Record<ReceiptType, string> = {
  counter_slip: 'Counter slip',
  delivery:     'Delivery note',
  service:      'Service sign-off',
}

export interface ApiReceipt {
  id: string
  agreement_id: string
  receipt_type: ReceiptType
  receipt_date: string
  receipt_ref: string | null
  amount: string
  tax_amount: string
  total_amount: string
  received_by: string
  missing_receipt_reason: string | null
  ap_reviewed_by: string | null
  ap_reviewed_at: string | null
  status: ReceiptStatus
  invoice_id: string | null
  notes: string | null
  created_by: string
  created_at: string
}

// Cross-agreement listing only (GET /agreement-receipts) — carries the parent
// agreement's human number alongside the receipt, since this list has no
// agreement_id in its URL to lean on the way the per-agreement page does.
// Never render agreement_id itself; render agreement_number.
export interface ApiReceiptWithAgreement extends ApiReceipt {
  agreement_number: string
}

export interface ReceiptListResponse {
  items: ApiReceipt[]
  total: number
}

export interface ReceiptListAllResponse {
  items: ApiReceiptWithAgreement[]
  total: number
}

export interface ReceiptListFilters {
  status?: ReceiptStatus
}

export interface ReceiptListAllFilters {
  agreement_id?: string
  receipt_type?: ReceiptType
  status?: ReceiptStatus
  search?: string
  page?: number
  page_size?: number
}

// Request bodies: the server (Pydantic) accepts plain JSON numbers for
// Decimal fields — only RESPONSES serialise Decimal as a string. Same
// convention as CreateAgreementBody/UpdateAgreementBody in services/agreement.ts.
export interface CreateReceiptBody {
  // Optional — the backend defaults to 'counter_slip' when omitted
  // (schemas/agreement_receipt.py ReceiptCreate.receipt_type default).
  receipt_type?: ReceiptType
  receipt_date: string
  receipt_ref?: string | null
  amount: number
  tax_amount: number
  total_amount: number
  received_by: string
  missing_receipt_reason?: string | null
  notes?: string | null
}

export interface UpdateReceiptBody {
  receipt_date?: string
  receipt_ref?: string | null
  amount?: number
  tax_amount?: number
  total_amount?: number
  received_by?: string
  missing_receipt_reason?: string | null
  notes?: string | null
}

export type ReceiptApReviewAction = 'approve' | 'reject'

export const agreementReceiptService = {
  list: (agreementId: string, opts?: ReceiptListFilters) =>
    api.get<ReceiptListResponse>(
      `/agreements/${agreementId}/receipts`,
      opts as Record<string, string | number | boolean | null | undefined>,
    ),

  // GET /agreement-receipts — cross-agreement listing (Task 9), backs its own
  // menu entry/page. Deliberately a SEPARATE method, not a param-less overload
  // of list() above: list() stays URL-scoped to one agreement (ReceiptTable's
  // use), this one has no agreement in its path at all.
  listAll: (filters?: ReceiptListAllFilters) =>
    api.get<ReceiptListAllResponse>(
      '/agreement-receipts',
      filters as Record<string, string | number | boolean | null | undefined>,
    ),

  create: (agreementId: string, body: CreateReceiptBody) =>
    api.post<ApiReceipt>(`/agreements/${agreementId}/receipts`, body),

  update: (agreementId: string, receiptId: string, body: UpdateReceiptBody) =>
    api.patch<ApiReceipt>(`/agreements/${agreementId}/receipts/${receiptId}`, body),

  // DELETE /receipts/{receipt_id} is a void (soft-cancel), not a hard delete — the
  // backend flips status to 'voided' rather than removing the row.
  void: (agreementId: string, receiptId: string) =>
    api.delete<void>(`/agreements/${agreementId}/receipts/${receiptId}`),

  apReview: (agreementId: string, receiptId: string, action: ReceiptApReviewAction) =>
    api.post<ApiReceipt>(`/agreements/${agreementId}/receipts/${receiptId}/ap-review`, { action }),
}

// ─── OCR (expense-api) ──────────────────────────────────────────────────────

// Shape returned by expense-api POST /api/v1/ocr/slip (see
// ocr_service.extract_slip — deliberately NOT renamed, see the module
// docstring rationale at the top of this task's brief: this plan doesn't
// touch expense-api). The endpoint already unwraps the LLM's internal
// {value, confidence} pairs before responding — the response body itself is
// flat. amount/tax_amount/total_amount come back as JSON numbers (not
// Pydantic-Decimal strings — this route has no response_model and never
// touches the ORM), so they are plain `number | null` here, unlike ApiReceipt's
// string amounts.
export interface OcrReceiptFields {
  receipt_ref: string | null
  date: string | null
  amount: number | null
  tax_amount: number | null
  total_amount: number | null
  currency: string
}

export const ocrService = {
  receipt: async (file: File): Promise<OcrReceiptFields> => {
    const token = useAuthStore.getState().token
    const form = new FormData()
    form.append('file', file)

    const res = await fetch(`${EXPENSE_BASE}/api/v1/ocr/slip`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    })

    if (!res.ok) {
      let detail = res.statusText
      try {
        const err = await res.json()
        if (typeof err.detail === 'string') detail = err.detail
      } catch { /* ignore parse error */ }
      throw new Error(detail)
    }

    return res.json()
  },
}
