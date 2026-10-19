/**
 * Courier freight payment (Freight) — epms-api /freight/*.
 *
 * Contract: epms-api/app/api/v1/freight.py + app/schemas/freight.py.
 * Decimal fields arrive as JSON strings (pydantic v2) — coerce with Number()
 * at use sites, never do arithmetic on them directly.
 */
import { api, EXPENSE_BASE } from '@/lib/api'
import { useAuthStore } from '@/stores/auth.store'

type Money = string | number

export type ChargeType = 'shipment' | 'pickup' | 'fee' | 'adjustment'
export type Direction = 'outbound' | 'inbound'
export type ShipmentStatus = 'pending' | 'billed' | 'void'
export type FreightInvoiceStatus = 'unmatched' | 'matched' | 'paid' | string
export type MatchStatus =
  | 'auto' | 'manual' | 'default' | 'unregistered' | 'duplicate_billing' | 'unmatched'

export interface FreightAccess {
  register: boolean
  manage: boolean
  admin: boolean
}

export interface FreightCarrier {
  id: string
  vendor_id: string
  vendor_name: string | null
  code: string
  name: string
  tracking_pattern: string | null
  default_cost_center_id: string | null
  is_active: boolean
}

export interface FreightShipment {
  id: string
  carrier_id: string
  carrier_code: string | null
  tracking_no: string
  direction: Direction
  ship_date: string | null
  cost_center_id: string
  cost_center_code: string | null
  cost_center_name: string | null
  department_id: string
  department_name: string | null
  note: string | null
  attachment_key: string | null
  source: string
  status: ShipmentStatus
  created_by: string
  created_by_name: string | null
  created_at: string
  invoice_id: string | null
  invoice_number: string | null
  billed_amount: Money | null
}

export interface ShipmentFilters {
  status?: ShipmentStatus
  carrier_id?: string
  search?: string
  mine?: boolean
  page?: number
  page_size?: number
}

export interface ShipmentCreateBody {
  carrier_id: string
  tracking_no: string
  cost_center_id: string
  direction: Direction
  note?: string | null
}

export interface ShipmentUpdateBody {
  tracking_no?: string
  cost_center_id?: string
  direction?: Direction
  note?: string | null
}

export interface DefaultCostCenter {
  cost_center_id: string | null
  reason: 'single' | 'freight_budget' | 'none' | string
  budgeted_cost_center_ids: string[]
}

export interface FreightLine {
  id: string
  sort_order: number
  charge_type: ChargeType
  tracking_no: string | null
  package_tracking_nos: string[]
  reference: string | null
  ship_date: string | null
  description: string | null
  amount: Money
  tax_amount: Money
  shipment_id: string | null
  linked_line_id: string | null
  cost_center_id: string | null
  cost_center_code: string | null
  cost_center_name: string | null
  department_name: string | null
  match_status: MatchStatus
  previously_billed_on: string | null
}

export interface CostCenterTotal {
  cost_center_id: string | null
  cost_center_code: string | null
  cost_center_name: string | null
  department_name: string | null
  amount: Money
  tax_amount: Money
}

export interface FreightInvoice {
  id: string
  internal_ref: string
  carrier_id: string
  carrier_code: string | null
  vendor_id: string
  vendor_name: string
  vendor_invoice_number: string
  invoice_date: string
  due_date: string
  currency: string
  amount: Money
  tax_amount: Money
  total_amount: Money
  status: FreightInvoiceStatus
  file_name: string | null
  notes: string | null
  uploaded_by_name: string | null
  created_at: string
  lines: FreightLine[]
  totals_by_cost_center: CostCenterTotal[]
  lines_amount_total: Money
  lines_tax_total: Money
  pa_id: string | null
  pa_number: string | null
  finalize_blockers: string[]
}

export interface FreightInvoiceListItem {
  id: string
  internal_ref: string
  carrier_id: string
  carrier_code: string | null
  vendor_invoice_number: string
  invoice_date: string
  due_date: string
  currency: string
  amount: Money
  tax_amount: Money
  total_amount: Money
  status: FreightInvoiceStatus
  line_count: number
  pa_id: string | null
  pa_number: string | null
  pa_status: string | null
}

export interface InvoiceFilters {
  status?: string
  carrier_id?: string
  page?: number
  page_size?: number
}

export interface FreightLineIn {
  charge_type: ChargeType
  tracking_no?: string | null
  package_tracking_nos: string[]
  reference?: string | null
  ship_date?: string | null
  description?: string | null
  amount: number
  tax_amount: number
}

export interface FreightInvoiceCreateBody {
  carrier_id: string
  vendor_invoice_number: string
  invoice_date: string
  due_date: string
  currency: string
  amount: number
  tax_amount: number
  file_name?: string | null
  file_size?: string | null
  notes?: string | null
  lines: FreightLineIn[]
}

/** Any field sent marks the line "manual" server-side (crud.update_line). */
export interface FreightLineUpdateBody {
  cost_center_id?: string
  shipment_id?: string
  linked_line_id?: string
  clear_shipment?: boolean
  clear_link?: boolean
  tax_amount?: number
  amount?: number
  tracking_no?: string
  package_tracking_nos?: string[]
}

export interface FreightPaCreateBody {
  invoice_ids: string[]
  title?: string | null
  notes?: string | null
}

export interface ListResponse<T> {
  items: T[]
  total: number
}

export const freightService = {
  me: () => api.get<FreightAccess>('/freight/me'),

  carriers: (includeInactive = false) =>
    api.get<FreightCarrier[]>('/freight/carriers', includeInactive ? { include_inactive: true } : undefined),

  defaultCostCenter: (departmentId: string) =>
    api.get<DefaultCostCenter>('/freight/default-cost-center', { department_id: departmentId }),

  shipments: (filters?: ShipmentFilters) =>
    api.get<ListResponse<FreightShipment>>('/freight/shipments', filters ? { ...filters } : undefined),

  createShipment: (body: ShipmentCreateBody) =>
    api.post<FreightShipment>('/freight/shipments', body),

  updateShipment: (id: string, body: ShipmentUpdateBody) =>
    api.patch<FreightShipment>(`/freight/shipments/${id}`, body),

  voidShipment: (id: string) =>
    api.post<FreightShipment>(`/freight/shipments/${id}/void`),

  invoices: (filters?: InvoiceFilters) =>
    api.get<ListResponse<FreightInvoiceListItem>>('/freight/invoices', filters ? { ...filters } : undefined),

  duplicateCheck: (carrierId: string, number: string) =>
    api.get<{ duplicate: boolean; detail: string | null }>('/freight/invoices/duplicate-check', {
      carrier_id: carrierId, vendor_invoice_number: number,
    }),

  createInvoice: (body: FreightInvoiceCreateBody) =>
    api.post<FreightInvoice>('/freight/invoices', body),

  invoice: (id: string) => api.get<FreightInvoice>(`/freight/invoices/${id}`),

  autoMatch: (id: string) => api.post<FreightInvoice>(`/freight/invoices/${id}/auto-match`),

  updateLine: (id: string, lineId: string, body: FreightLineUpdateBody) =>
    api.patch<FreightInvoice>(`/freight/invoices/${id}/lines/${lineId}`, body),

  finalize: (id: string) => api.post<FreightInvoice>(`/freight/invoices/${id}/finalize`),

  reopen: (id: string) => api.post<FreightInvoice>(`/freight/invoices/${id}/reopen`),

  deleteInvoice: (id: string) => api.delete(`/freight/invoices/${id}`),

  createPa: (body: FreightPaCreateBody) =>
    api.post<{ id: string; pa_number: string }>('/freight/pa', body),
}

// ─── Attachments (expense-api unified invoice storage) ───────────────────────
// Same endpoint and invoice_source as the EPMS invoice upload
// (pages/invoices/InvoiceListPage.tsx) — a freight invoice IS an EPMS invoice row.

export interface InvoiceAttachmentMeta {
  id: string
  file_name: string
  content_type: string
  file_size_bytes: number
  uploaded_at: string
}

const ATT_BASE = `${EXPENSE_BASE}/api/v1/invoice-attachments`

function authHeader(): Record<string, string> {
  const token = useAuthStore.getState().token
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export const freightAttachmentService = {
  list: async (invoiceId: string): Promise<InvoiceAttachmentMeta[]> => {
    const res = await fetch(`${ATT_BASE}?invoice_id=${invoiceId}&invoice_source=epms`, { headers: authHeader() })
    if (!res.ok) return []
    return res.json()
  },

  /** Throws on failure so the caller can tell the user the PDF did not attach. */
  upload: async (invoiceId: string, file: File): Promise<void> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${ATT_BASE}?invoice_id=${invoiceId}&invoice_source=epms`, {
      method: 'POST', headers: authHeader(), body: form,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(typeof err.detail === 'string' ? err.detail : `Upload failed (${res.status})`)
    }
  },

  /** Fetches the file with auth and opens it in a new browser tab. The tab is
   *  opened synchronously (inside the click) so popup blockers allow it, then
   *  pointed at the blob once it has downloaded. */
  open: async (attachmentId: string): Promise<void> => {
    const win = window.open('', '_blank')
    const res = await fetch(`${ATT_BASE}/${attachmentId}/file`, { headers: authHeader() })
    if (!res.ok) {
      win?.close()
      throw new Error(`Could not open the file (${res.status})`)
    }
    const url = URL.createObjectURL(await res.blob())
    if (win) win.location.href = url
    else window.open(url, '_blank')
    // Give the new tab time to load before releasing the blob.
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  },
}

// ─── OCR (expense-api /ocr/freight_invoice, /ocr/waybill) ─────────────────────
// Same call shape and failure split as lib/invoice-parser.ts: 503 (or never
// reaching the server) is the AI service, anything else is the document.

export type OcrFailureKind = 'file' | 'service'

export interface FreightOcrLine {
  charge_type: ChargeType
  tracking_no: string | null
  package_tracking_nos: string[]
  reference: string | null
  ship_date: string | null
  description: string | null
  amount: string
  tax_amount: string
}

export interface FreightOcrResult {
  carrier: string | null
  invoice_number: string | null
  account_number: string | null
  invoice_date: string | null
  due_date: string | null
  currency: string
  amount: string | null
  tax_amount: string | null
  lines: FreightOcrLine[]
  checks: Record<string, string | boolean | null>
  status: 'ok' | 'mismatch'
  warnings: string[]
}

export interface WaybillOcrResult {
  carrier: string | null
  tracking_numbers: string[]
}

export type OcrResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; kind: OcrFailureKind }

async function ocr<T>(mode: 'freight_invoice' | 'waybill', file: File): Promise<OcrResult<T>> {
  try {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${EXPENSE_BASE}/api/v1/ocr/${mode}`, {
      method: 'POST', headers: authHeader(), body: form,
    })
    if (!res.ok) {
      let detail = res.statusText
      try {
        const err = await res.json()
        if (typeof err.detail === 'string') detail = err.detail
      } catch { /* ignore parse error */ }
      return { ok: false, error: detail, kind: res.status === 503 ? 'service' : 'file' }
    }
    return { ok: true, data: await res.json() as T }
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : 'Unknown error', kind: 'service' }
  }
}

export const readFreightInvoice = (file: File) => ocr<FreightOcrResult>('freight_invoice', file)
export const readWaybill = (file: File) => ocr<WaybillOcrResult>('waybill', file)
