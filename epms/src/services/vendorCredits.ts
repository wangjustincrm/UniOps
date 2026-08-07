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

// ── Netting preview for the PA Process dialog (Phase B) ──────────────────────
//
// Read-only — `GET /vendor-credits/suggest` takes no locks and reports what
// finance-api's FIFO default would net off this payment application; it
// reserves nothing. The actual reservation happens inside
// `POST /epms/v1/pa/{id}/action` with `action: 'process'`, which forwards an
// optional `credit_ids` override built from what the operator deselected (see
// PaDetailPage.tsx's ProcessModal).

export interface CreditSuggestion {
  credit_id: string
  credit_number: string
  credit_date: string
  /** Decimal-as-string — Number() before arithmetic. */
  remaining: string
  /** Decimal-as-string — Number() before arithmetic. */
  apply: string
}

export interface CreditSuggestResponse {
  /** Decimal-as-string — Number() before arithmetic. */
  gross: string
  suggested: CreditSuggestion[]
  /** Decimal-as-string — Number() before arithmetic. */
  credit_applied: string
  /** Decimal-as-string — Number() before arithmetic. */
  net: string
}

/** `docKind` must be 'pa' or 'pa_dir' — the backend 422s for anything else
 * (in particular 'expense_claim', which never takes vendor credits). EPMS only
 * ever shows PO-backed PAs, so its one caller passes 'pa'. */
export const suggestCredits = (docKind: string, docId: string) =>
  financeApi.get<CreditSuggestResponse>(
    `/vendor-credits/suggest?doc_kind=${docKind}&doc_id=${docId}`)

export interface PlannedApplication {
  credit: CreditSuggestion
  /** What the server would ACTUALLY apply to this credit given the current
   * selection — not `credit.apply`. */
  take: number
}

/**
 * Re-run the server's capped FIFO over only the credits still selected.
 *
 * `suggestion.apply` is NOT reusable once the operator deselects anything: it
 * was computed inside the FULL FIFO context, where a later credit had already
 * been squeezed by an earlier one. When the server receives an explicit id
 * list it re-runs capped FIFO over just those ids, which is a different
 * allocation.
 *
 * Concrete: gross 100, c1 remaining 60, c2 remaining 80 → the preview says c1
 * apply 60, c2 apply 40. Deselect c1 and summing `apply` claims credits 40 /
 * net 60, while the server applies c2 = min(80, 100) = 80 → net 20. Hence this
 * loop, which mirrors finance-api's
 * app/crud/vendor_credit.py:select_credits_for_payment exactly: oldest first
 * (the payload is already in that order), each take capped at the remaining
 * base, stop once the base is exhausted.
 *
 * Kept byte-for-byte in step with finance/src/services/vendorCredits.ts — EPMS
 * and the Finance app are separate builds with no shared frontend package, and
 * both must agree with the server. Change one, change the other.
 *
 * Money fields are Decimal-as-string — Number() before every arithmetic op.
 */
export function planApplications(
  s: CreditSuggestResponse, off: Set<string>,
): PlannedApplication[] {
  const gross = Number(s.gross)
  const out: PlannedApplication[] = []
  let sum = 0
  for (const c of s.suggested) {
    if (off.has(c.credit_id)) continue
    const take = Math.min(Number(c.remaining), gross - sum)
    if (take <= 0) break
    sum += take
    out.push({ credit: c, take })
  }
  return out
}

export const plannedTotal = (plan: PlannedApplication[]): number =>
  plan.reduce((sum, p) => sum + p.take, 0)
