import { EXPENSE_BASE } from '@/lib/api'
import { useAuthStore } from '@/stores/auth.store'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ParsedLineItem {
  description: string
  quantity:    number
  unit:        string | null
  unit_price:  number
  line_total:  number
}

export interface ParsedInvoiceFields {
  vendorName:          string | null
  vendorInvoiceNumber: string | null
  poNumber:            string | null
  invoiceDate:         string | null   // "YYYY-MM-DD"
  dueDate:             string | null   // "YYYY-MM-DD"
  paymentTermsNetDays: number | null   // e.g. 30 if "NET 30" found and no explicit due date
  amount:              number | null   // pre-tax subtotal
  taxAmount:           number | null
  currency:            string | null   // "CAD" | "USD" | "EUR" | "RMB"
  lineItems:           ParsedLineItem[] | null
  documentType:        'invoice' | 'credit_note'
}

/**
 * A failed parse is one of two very different things, and the banner says so:
 *   'file'    — the model could not read THIS document (422). Manual entry is
 *               the fix, and re-uploading the same file will not help.
 *   'service' — extraction is down or the AI account hit a usage/billing limit
 *               (503, or the request never reached the server). Nothing is
 *               wrong with the file; IT has to act. Calling this one "AI
 *               parsing failed" sent people hunting a fault in a blameless
 *               invoice for a whole afternoon (2026-09-08).
 */
export type ParseFailureKind = 'file' | 'service'

export type ParseResult =
  | { ok: true;  fields: ParsedInvoiceFields }
  | { ok: false; error: string; kind: ParseFailureKind }

// Shape returned by expense-api POST /api/v1/ocr/invoice (see ocr_service.extract_invoice).
interface OcrLineItem {
  description?: string | null
  quantity?:    number | null
  unit_price?:  number | null
  amount?:      number | null
  tax_amount?:  number | null
}

interface OcrInvoiceResponse {
  vendor_name?:            string | null
  invoice_number?:         string | null
  po_number?:              string | null
  invoice_date?:           string | null
  due_date?:               string | null
  payment_terms_net_days?: number | null
  currency?:               string | null
  subtotal?:               number | null
  tax_amount?:             number | null
  total_amount?:           number | null
  line_items?:             OcrLineItem[]
  ocr_confidence?:         number
  low_confidence_fields?:  string[]
  document_type?:          'invoice' | 'credit_note' | null
}

// ─── Main export ──────────────────────────────────────────────────────────────

/**
 * Parse an invoice/receipt via the server-side OCR proxy (expense-api).
 *
 * The Anthropic API key lives on the server (ANTHROPIC_API_KEY) — it is never
 * shipped to the browser bundle. This replaces the previous client-side
 * `@anthropic-ai/sdk` + VITE_ANTHROPIC_API_KEY approach, mirroring the OA module
 * fix (see SPRINT-OA-FIX P0-2). PDF rasterisation is handled server-side too, so
 * the browser no longer needs pdfjs for this path.
 */
export async function parseInvoiceFile(file: File): Promise<ParseResult> {
  try {
    const token = useAuthStore.getState().token
    const form  = new FormData()
    form.append('file', file)

    const res = await fetch(`${EXPENSE_BASE}/api/v1/ocr/invoice`, {
      method:  'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body:    form,
    })

    if (!res.ok) {
      let detail = res.statusText
      try {
        const err = await res.json()
        if (typeof err.detail === 'string') detail = err.detail
      } catch { /* ignore parse error */ }
      // 503 = service unavailable / account limit (see ocr.py's RuntimeError
      // branch). Anything else is about the document itself.
      return { ok: false, error: detail, kind: res.status === 503 ? 'service' : 'file' }
    }

    const r = await res.json() as OcrInvoiceResponse

    const lineItems: ParsedLineItem[] | null =
      Array.isArray(r.line_items) && r.line_items.length > 0
        ? r.line_items.map((li) => ({
            description: li.description ?? '',
            quantity:    li.quantity ?? 1,
            unit:        null,
            unit_price:  li.unit_price ?? 0,
            line_total:  li.amount ?? 0,
          }))
        : null

    const fields: ParsedInvoiceFields = {
      vendorName:          r.vendor_name ?? null,
      vendorInvoiceNumber: r.invoice_number ?? null,
      poNumber:            r.po_number ?? null,
      invoiceDate:         r.invoice_date ?? null,
      dueDate:             r.due_date ?? null,
      paymentTermsNetDays: r.payment_terms_net_days ?? null,
      amount:              r.subtotal ?? null,
      taxAmount:           r.tax_amount ?? null,
      currency:            r.currency ?? null,
      lineItems,
      documentType:        r.document_type === 'credit_note' ? 'credit_note' : 'invoice',
    }
    return { ok: true, fields }
  } catch (err) {
    // Never reached the server (network, CORS, expense-api down) — not the file.
    return {
      ok: false,
      error: err instanceof Error ? err.message : 'Unknown error',
      kind: 'service',
    }
  }
}
