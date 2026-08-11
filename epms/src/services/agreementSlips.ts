import { api, EXPENSE_BASE } from '@/lib/api'
import { useAuthStore } from '@/stores/auth.store'

// Decimal fields (amount, tax_amount, total_amount) arrive from the API as
// JSON strings — same Pydantic-Decimal serialisation as ApiAgreement /
// ApiScheduleRow (see services/agreement.ts). Number()-coerce before any
// arithmetic/comparison.
export type SlipStatus = 'pending_ap_review' | 'open' | 'reconciled' | 'voided' | 'rejected'

export interface ApiSlip {
  id: string
  agreement_id: string
  slip_date: string
  slip_ref: string | null
  amount: string
  tax_amount: string
  total_amount: string
  picked_by: string
  missing_slip_reason: string | null
  ap_reviewed_by: string | null
  ap_reviewed_at: string | null
  status: SlipStatus
  invoice_id: string | null
  notes: string | null
  created_by: string
  created_at: string
}

export interface SlipListResponse {
  items: ApiSlip[]
  total: number
}

export interface SlipListFilters {
  status?: SlipStatus
}

// Request bodies: the server (Pydantic) accepts plain JSON numbers for
// Decimal fields — only RESPONSES serialise Decimal as a string. Same
// convention as CreateAgreementBody/UpdateAgreementBody in services/agreement.ts.
export interface CreateSlipBody {
  slip_date: string
  slip_ref?: string | null
  amount: number
  tax_amount: number
  total_amount: number
  picked_by: string
  missing_slip_reason?: string | null
  notes?: string | null
}

export interface UpdateSlipBody {
  slip_date?: string
  slip_ref?: string | null
  amount?: number
  tax_amount?: number
  total_amount?: number
  picked_by?: string
  missing_slip_reason?: string | null
  notes?: string | null
}

export type SlipApReviewAction = 'approve' | 'reject'

export const agreementSlipService = {
  list: (agreementId: string, opts?: SlipListFilters) =>
    api.get<SlipListResponse>(
      `/agreements/${agreementId}/slips`,
      opts as Record<string, string | number | boolean | null | undefined>,
    ),

  create: (agreementId: string, body: CreateSlipBody) =>
    api.post<ApiSlip>(`/agreements/${agreementId}/slips`, body),

  update: (agreementId: string, slipId: string, body: UpdateSlipBody) =>
    api.patch<ApiSlip>(`/agreements/${agreementId}/slips/${slipId}`, body),

  // DELETE /slips/{slip_id} is a void (soft-cancel), not a hard delete — the
  // backend flips status to 'voided' rather than removing the row.
  void: (agreementId: string, slipId: string) =>
    api.delete<void>(`/agreements/${agreementId}/slips/${slipId}`),

  apReview: (agreementId: string, slipId: string, action: SlipApReviewAction) =>
    api.post<ApiSlip>(`/agreements/${agreementId}/slips/${slipId}/ap-review`, { action }),
}

// ─── OCR (expense-api) ──────────────────────────────────────────────────────

// Shape returned by expense-api POST /api/v1/ocr/slip (see
// ocr_service.extract_slip). The endpoint already unwraps the LLM's internal
// {value, confidence} pairs before responding — the response body itself is
// flat. amount/tax_amount/total_amount come back as JSON numbers (not
// Pydantic-Decimal strings — this route has no response_model and never
// touches the ORM), so they are plain `number | null` here, unlike ApiSlip's
// string amounts.
export interface OcrSlipFields {
  slip_ref: string | null
  date: string | null
  amount: number | null
  tax_amount: number | null
  total_amount: number | null
  currency: string
}

export const ocrService = {
  slip: async (file: File): Promise<OcrSlipFields> => {
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
