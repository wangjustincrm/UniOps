import { api, fetchAllPages } from '@/lib/api'
import type { ApiEvent } from './pr'
import type { WorkflowNodeDef, CurrentStep } from '@/types'

export type PoStatus =
  | 'draft'
  | 'submitted'
  | 'in_review'
  | 'approved'
  | 'returned'
  | 'rejected'
  | 'issued'
  | 'partially_received'
  | 'fully_received'
  | 'closed'
  | 'cancelled'

export type PoAction = 'submit' | 'approve' | 'return' | 'reject' | 'issue' | 'cancel'

export interface ApiPoLineItem {
  id: string
  description: string
  material_id?: string
  supplier_item_id?: string
  // Buyer-supplied sample requirement, e.g. "500 g" / "2 ea" (NC-imported POs).
  sample?: string | null
  // Source PR line this PO line was created from (backend persists it).
  pr_line_id?: string | null
  qty: number
  unit: string
  unit_price: number
  line_total: number
  received_qty: number
  notes?: string
  // ERP-synced per-line arrival date (NC-imported POs only). Display-only —
  // never sent back to the server.
  planned_arrival_date?: string | null
  // 该 line 被其他发票累计分摊的税前额(仅 match-candidates 端点返回)
  already_allocated?: string | null
  // False on a line a buyer added to an NC-imported PO for a charge the ERP
  // cannot carry (a one-off mould/tooling quote). Those rows are fully
  // editable here; NC's own are not. Absent on responses that predate the
  // field, which reads as "not NC's" — see isNcSourced().
  nc_sourced?: boolean
}

// Fields the client posts per line. Server derives line_total/received_qty;
// pr_line_id is kept so PO lines stay linked to their originating PR line.
type PoLineItemInput = Omit<
  ApiPoLineItem,
  'id' | 'line_total' | 'received_qty' | 'already_allocated'
>

export interface ApiPo {
  id: string
  number: string
  title: string
  type: number
  status: PoStatus
  currency: string
  subtotal: number
  tax_rate: number
  tax_code?: string | null
  tax_amount: number
  total: number
  vendor_id: string
  vendor_name: string
  is_prepaid: boolean
  // This PO has an invoice a NEW payment could settle: unpaid AND not already
  // claimed by a live payment application. Both the list and the detail
  // endpoint fill it in, off one definition in the backend — the PA create
  // page's PO picker and the PO page's Create PA button both gate on it and
  // must not be able to disagree.
  has_unpaid_invoice: boolean
  // ANY invoice points at this PO — detail endpoint only, and regardless of
  // whether it is paid or claimed. A different question from has_unpaid_invoice.
  has_invoice?: boolean
  budget_code?: string
  expected_delivery?: string
  delivery_address?: string
  notes?: string
  // Buyer-supplied detail on NC-imported POs. `notes` stays NC-owned and may
  // contain internal [NC Paid] / [NC Closed] markers — never show it as
  // buyer text on an NC PO.
  buyer_notes?: string | null
  incoterms?: string | null
  buyer_edited_at?: string | null
  pr_id?: string
  pr_number?: string
  pr_requester_id?: string | null
  // Department the PO's approvals route through (PO → PR.department_id); null
  // for a PO with no PR. A payment is approved by ONE department's reviewers
  // (its primary PO's), so the PA screens refuse to mix departments on one
  // payment — mirrors epms-api pa.py::_assert_pos_coherent.
  pr_department_id?: string | null
  approval_step_idx: number
  line_items: ApiPoLineItem[]
  created_by_name?: string | null
  created_at: string
  updated_at: string
  // 该 PO 被其他发票累计分摊的税前总额(仅 match-candidates 端点返回)
  already_allocated_total?: string | null
  current_step?: CurrentStep | null
  // NC ERP provenance — 'nc' for POs mirrored from NC purchase orders, null/undefined
  // for POs created natively in UniOps.
  source?: string | null
}

export interface CreatePoBody {
  title: string
  type: number
  currency: string
  tax_rate: number
  tax_code?: string | null
  vendor_id: string
  is_prepaid?: boolean
  budget_code?: string
  expected_delivery?: string
  delivery_address?: string
  notes?: string
  pr_id?: string
  // subtotal/tax_amount/total/vendor_name are recomputed/derived server-side.
  line_items: PoLineItemInput[]
}

export interface UpdatePoBody {
  title?: string
  type?: number
  currency?: string
  tax_rate?: number
  tax_code?: string | null
  vendor_id?: string
  is_prepaid?: boolean
  budget_code?: string
  expected_delivery?: string
  delivery_address?: string
  notes?: string
  // subtotal/tax_amount/total/vendor_name are recomputed/derived server-side.
  line_items?: PoLineItemInput[]
}

export interface ImportedDetailsLineBody {
  id: string
  supplier_item_id?: string | null
  sample?: string | null
}

export interface ImportedDetailsBody {
  expected_delivery?: string | null
  delivery_address?: string | null
  incoterms?: string | null
  tax_code?: string | null
  tax_rate?: number | null
  is_prepaid?: boolean | null
  buyer_notes?: string | null
  lines: ImportedDetailsLineBody[]
  // The COMPLETE set of buyer-added lines, not a patch: omitting one deletes
  // it. Leave the key off entirely to say "don't touch them".
  manual_lines?: ManualLineBody[]
}

export interface ManualLineBody {
  // Absent on a row that has not been saved yet.
  id?: string
  description: string
  qty: number
  unit: string
  unit_price: number
  supplier_item_id?: string | null
  sample?: string | null
}

export interface PoActionBody {
  action: PoAction
  comment?: string
}

export interface PlaceOrderBody {
  method: 'email' | 'online'
  // email method
  to?: string
  cc?: string
  subject?: string
  body?: string
  // online method
  reference?: string
}

export interface PoFilters {
  status?: PoStatus
  pr_type?: number
  department_id?: string
  is_prepaid?: boolean
  vendor_id?: string
  pr_id?: string
  search?: string
  page?: number
  page_size?: number
}

export interface PoListResponse {
  items: ApiPo[]
  total: number
}

export const poService = {
  list: (filters?: PoFilters) =>
    api.get<PoListResponse>('/po', filters),

  listAll: (filters?: Omit<PoFilters, 'page' | 'page_size'>): Promise<PoListResponse> =>
    fetchAllPages((page, page_size) => poService.list({ ...filters, page, page_size })),

  get: (id: string) =>
    api.get<ApiPo>(`/po/${id}`),

  create: (body: CreatePoBody) =>
    api.post<ApiPo>('/po', body),

  update: (id: string, body: UpdatePoBody) =>
    api.patch<ApiPo>(`/po/${id}`, body),

  updateImportedDetails: (id: string, body: ImportedDetailsBody) =>
    api.patch<ApiPo>(`/po/${id}/imported-details`, body),

  action: (id: string, body: PoActionBody) =>
    api.post<ApiPo>(`/po/${id}/action`, body),

  events: (id: string) =>
    api.get<ApiEvent[]>(`/po/${id}/events`),

  placeOrder: (id: string, body: PlaceOrderBody) =>
    api.post<ApiPo>(`/po/${id}/place-order`, body),

  workflowSteps: (id: string) =>
    api.get<WorkflowNodeDef[]>(`/po/${id}/workflow-steps`),

  // ── Sign-off (NC-imported POs) ────────────────────────────────────────────
  signoff: (id: string) =>
    api.get<PoSignoffState>(`/po/${id}/signoff`),

  submitSignoff: (id: string, justification: string) =>
    api.post<PoSignoffState>(`/po/${id}/signoff/submit`, { justification }),

  signSignoff: (id: string, comment?: string) =>
    api.post<PoSignoffState>(`/po/${id}/signoff/sign`, { comment }),

  returnSignoff: (id: string, comment: string) =>
    api.post<PoSignoffState>(`/po/${id}/signoff/return`, { comment }),

  addSignoffNote: (id: string, comment: string) =>
    api.post<PoSignoffState>(`/po/${id}/signoff/note`, { comment }),
}

// ── Sign-off types ──────────────────────────────────────────────────────────
// signoff_status reuses the approval engine's own literals; the UI renders them
// as Not started / Awaiting signature / Signed rather than showing them raw.
export type PoSignoffStatus =
  | 'draft' | 'submitted' | 'in_review' | 'approved' | 'returned'
  | 'rejected' | 'cancelled'

export interface PoSignoffStep {
  id: string
  role: string
  label: string
  /** Where this step's signature lands on the PDF, null = nowhere. */
  sig_slot: 'initials' | 'signature' | null
  holder_count: number
  holders_without_signature: string[]
  signed_by_name: string | null
  signed_at: string | null
}

export interface PoSignoffThreadEntry {
  action: string
  actor_name: string | null
  actor_role: string
  comment: string | null
  at: string
}

export interface PoSignoffState {
  status: PoSignoffStatus
  step_idx: number
  submitted_by: string | null
  submitted_by_name: string | null
  submitted_at: string | null
  steps: PoSignoffStep[]
  thread: PoSignoffThreadEntry[]
  can_submit: boolean
  can_sign: boolean
  can_note: boolean
  /** Full sentences, shown verbatim — each says what to go and fix. */
  blockers: string[]
}
