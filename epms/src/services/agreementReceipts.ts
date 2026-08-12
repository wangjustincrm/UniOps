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

// Statuses the backend still accepts an edit on — mirrors
// crud/agreement_receipt.py's EDITABLE tuple, which is the same pair as its
// VOIDABLE (see VOIDABLE_STATUSES in components/agreements/ReceiptTable.tsx:
// "既然 void 只放行这两个状态,编辑没道理更宽松"). A `reconciled` receipt is
// desynced from the invoice that matched it if edited; `rejected`/`voided`
// are terminal. ReceiptDetailPage swaps its form for a read-only view — with
// the reason spelled out — outside this set, rather than firing a PATCH the
// backend answers with 409.
export const EDITABLE_STATUSES = new Set<ReceiptStatus>(['open', 'pending_ap_review'])

// The backend (schemas/agreement_receipt.py::validate_totals) checks
// amount + tax_amount == total_amount as EXACT Decimal equality against a
// Numeric(15,2) column — equality to the cent. A plain float `===` (or a 0.01
// tolerance, which is NOT tight enough: 10.00 + 1.30 vs 11.31 differs by
// 0.009999999999999787, which slips under a 0.01 threshold and then 422s
// server-side) fails to match that. Comparing rounded-to-cent integers kills
// float noise (0.1 + 0.2 !== 0.3) while staying exactly as strict as the
// backend, so a value the frontend accepts never bounces off the API.
//
// Lives here rather than in one of the two forms that need it (ReceiptEntryForm
// for create, ReceiptDetailPage for edit): both post to the same validator, so
// two copies could only ever drift apart.
export function receiptTotalsMatch(total: number, amount: number, tax: number): boolean {
  return Math.round(total * 100) === Math.round(amount * 100) + Math.round(tax * 100)
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
// agreement's human number + currency alongside the receipt, since this list
// has no agreement_id in its URL to lean on the way the per-agreement page
// does. Never render agreement_id itself; render agreement_number.
//
// currency (fix round 1, Critical): this list spans MULTIPLE agreements,
// which can each carry a different currency (see AgreementCreatePage's
// currency dropdown — CAD/USD/EUR/RMB are real, user-chosen options, not a
// constant). amount/tax_amount/total_amount are bare numbers with no
// currency of their own — every render site must pair them with THIS row's
// currency, never a hardcoded one.
//
// invoice_ref (fix round 1, Important 2): the linked invoice's human
// reference, resolved server-side (LEFT joined — most rows have none until
// `reconciled`). Same "never render a bare id" rule as agreement_number:
// fall back to `invoice_id.slice(0, 8)` only when this is null, matching the
// `x_number ?? x_id.slice(0, 8)` convention used throughout
// InvoiceDetailPage.tsx for PO/agreement references.
//
// attachment_count (whole-branch review I2): how many photos/proof files are
// on this receipt, counted server-side in the same query as the row (see
// crud/agreement_receipt.py list_all). ReceiptListPage is the only surface
// that can approve or reject a receipt sitting in pending_ap_review, and
// "does it have a photo at all" is the fact that decision turns on — without
// this the page would have to fire one attachments GET per visible row.
export interface ApiReceiptWithAgreement extends ApiReceipt {
  agreement_number: string
  currency: string
  invoice_ref: string | null
  attachment_count: number
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
  // Was missing until Task 12 even though the backend's ReceiptUpdate has
  // always accepted it — ReceiptDetailPage's edit form lets the recorder fix a
  // receipt filed under the wrong type (a delivery note keyed as a counter
  // slip), which was previously only settable at creation time.
  receipt_type?: ReceiptType
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

  // GET /agreement-receipts/{receipt_id} — single receipt, no agreement in the
  // URL (Task 12). ReceiptDetailPage is reached from the cross-agreement list,
  // whose rows carry only the receipt id, so it cannot use the agreement-scoped
  // read. Returns the SAME enriched shape as a listing row
  // (ApiReceiptWithAgreement) — agreement_number / currency / invoice_ref /
  // attachment_count included — so the page needs no second fetch and no
  // second type.
  get: (receiptId: string) =>
    api.get<ApiReceiptWithAgreement>(`/agreement-receipts/${receiptId}`),

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
