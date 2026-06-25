import Anthropic from '@anthropic-ai/sdk'
import * as pdfjsLib from 'pdfjs-dist'
// ?url tells Vite to bundle the worker file and return its hashed URL.
import pdfjsWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorkerUrl

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
}

export type ParseResult =
  | { ok: true;  fields: ParsedInvoiceFields }
  | { ok: false; error: string }

// ─── Prompt ───────────────────────────────────────────────────────────────────

const SYSTEM_PROMPT = `You are an invoice data extraction assistant for an enterprise procurement system.
Extract structured fields from the invoice image and return ONLY a JSON object — no explanation, no markdown, no code fences.

Return this exact schema:
{
  "vendorName": string | null,
  "vendorInvoiceNumber": string | null,
  "poNumber": string | null,
  "invoiceDate": "YYYY-MM-DD" | null,
  "dueDate": "YYYY-MM-DD" | null,
  "paymentTermsNetDays": number | null,
  "amount": number | null,
  "taxAmount": number | null,
  "currency": "CAD" | "USD" | "EUR" | "RMB" | null,
  "lineItems": [
    {
      "description": string,
      "quantity": number,
      "unit": string | null,
      "unit_price": number,
      "line_total": number
    }
  ] | null
}

Rules:
- Return null for any field you cannot read clearly from the image.
- Convert all dates to YYYY-MM-DD format (e.g. "March 15 2026" → "2026-03-15", "15/03/2026" → "2026-03-15").
- "amount" is the pre-tax subtotal. If the invoice shows only a single grand total with no tax breakdown, put that total in "amount".
- "taxAmount" is the tax-only portion (HST, GST, VAT, etc.). Return the numeric value shown on the tax line (including 0 if the line explicitly reads "$0.00" or "Tax Exempt"). Return null only when the invoice has no tax section at all — i.e. a bare lump-sum with zero mention of tax.
- "currency": prefer CAD if ambiguous between CAD/USD. Map "$" to CAD unless clearly US-context.
- "poNumber" is a purchase order reference the vendor may print on the invoice — often starts with "PO-".
- "paymentTermsNetDays": if the invoice shows payment terms like "NET 30", "NET 45", "Net 60", "Due in 30 days" etc., extract the number of days (e.g. 30). Return null if no such terms are found. If an explicit "dueDate" is visible, you may still return paymentTermsNetDays if the terms are printed.
- "dueDate": extract only if an explicit due date is printed. Do NOT calculate it from NET terms — that is done by the application.
- "lineItems": extract the itemised line items from the invoice body. Each item should have description, quantity (default 1 if not shown), unit (e.g. "pcs", "hrs", null if not shown), unit_price, and line_total. If the invoice has no itemised table (only a lump-sum total), return null.
- Do not fabricate or guess values. When uncertain, return null.`

// ─── File → base64 helpers ────────────────────────────────────────────────────

async function pdfToBase64Png(file: File): Promise<string> {
  const data     = new Uint8Array(await file.arrayBuffer())
  const pdfDoc   = await pdfjsLib.getDocument({ data }).promise
  const page     = await pdfDoc.getPage(1)
  // scale 2.0 → ~144 DPI, sufficient for Claude to read fine print
  const viewport = page.getViewport({ scale: 2.0 })
  const canvas   = document.createElement('canvas')
  canvas.width   = viewport.width
  canvas.height  = viewport.height
  await page.render({ canvas, viewport }).promise
  return canvas.toDataURL('image/png').split(',')[1]
}

async function imageToBase64(file: File): Promise<{ base64: string; mediaType: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = reader.result as string
      const [header, base64] = dataUrl.split(',')
      const mediaType = header.match(/:(.*?);/)?.[1] ?? 'image/jpeg'
      resolve({ base64, mediaType })
    }
    reader.onerror = () => reject(new Error('FileReader failed'))
    reader.readAsDataURL(file)
  })
}

// ─── Main export ──────────────────────────────────────────────────────────────

export async function parseInvoiceFile(file: File): Promise<ParseResult> {
  const apiKey = import.meta.env.VITE_ANTHROPIC_API_KEY as string | undefined
  if (!apiKey) return { ok: false, error: 'VITE_ANTHROPIC_API_KEY not configured' }

  // NOTE: API key is exposed to the browser bundle.
  // Acceptable for this prototype/internal tool.
  // For production, replace with a /api/parse-invoice backend proxy route.
  const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true })

  try {
    const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
    let base64: string
    let mediaType: string

    if (ext === 'pdf') {
      base64    = await pdfToBase64Png(file)
      mediaType = 'image/png'
    } else {
      ;({ base64, mediaType } = await imageToBase64(file))
    }

    const message = await client.messages.create({
      model:      'claude-sonnet-4-6',
      max_tokens: 2048,
      system:     SYSTEM_PROMPT,
      messages: [
        {
          role: 'user',
          content: [
            {
              type:   'image',
              source: {
                type:       'base64',
                media_type: mediaType as 'image/png' | 'image/jpeg' | 'image/gif' | 'image/webp',
                data:       base64,
              },
            },
            { type: 'text', text: 'Extract the invoice fields from this image.' },
          ],
        },
      ],
    })

    const raw = message.content[0]
    if (raw.type !== 'text') return { ok: false, error: 'Unexpected response type from API' }

    // Strip markdown fences in case Claude wraps the JSON anyway
    const jsonText = raw.text.trim().replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/, '')
    const fields   = JSON.parse(jsonText) as ParsedInvoiceFields
    return { ok: true, fields }
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : 'Unknown error' }
  }
}
