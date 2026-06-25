// Invoice OCR — runs server-side via expense-api POST /api/v1/ocr/invoice.
// The Anthropic API key now lives on the server; it is no longer shipped in the
// browser bundle. The backend handles PDFs natively, so no client-side rasterization.
import { api } from '@/lib/api'

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
  paymentTermsNetDays: number | null
  amount:              number | null   // pre-tax subtotal
  taxAmount:           number | null
  currency:            string | null   // "CAD" | "USD" | "EUR" | "RMB"
  lineItems:           ParsedLineItem[] | null
  lowConfidenceFields: string[]        // server-flagged fields (< 0.75)
}

export type ParseResult =
  | { ok: true;  fields: ParsedInvoiceFields }
  | { ok: false; error: string }

// ─── Server response shape (expense-api ocr_service.extract_invoice) ────────────

interface OcrLineItem {
  line_number?: number
  description?:  string
  quantity?:     number
  unit_price?:   number
  amount?:       number
  tax_amount?:   number
}

interface OcrInvoiceResponse {
  vendor_name?:           string | null
  invoice_number?:        string | null
  invoice_date?:          string | null
  due_date?:              string | null
  currency?:              string | null
  subtotal?:              number | null
  tax_amount?:            number | null
  total_amount?:          number | null
  line_items?:            OcrLineItem[]
  ocr_confidence?:        number
  low_confidence_fields?: string[]
}

// ─── Main export ──────────────────────────────────────────────────────────────

export async function parseInvoiceFile(file: File): Promise<ParseResult> {
  try {
    const form = new FormData()
    form.append('file', file)
    const r = await api.postForm<OcrInvoiceResponse>('/api/v1/ocr/invoice', form)

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
      poNumber:            null, // not part of the invoice OCR schema
      invoiceDate:         r.invoice_date ?? null,
      dueDate:             r.due_date ?? null,
      paymentTermsNetDays: null,
      amount:              r.subtotal ?? null,
      taxAmount:           r.tax_amount ?? null,
      currency:            r.currency ?? null,
      lineItems,
      lowConfidenceFields: r.low_confidence_fields ?? [],
    }
    return { ok: true, fields }
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : 'Unknown error' }
  }
}
