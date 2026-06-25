import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Loader2, Paperclip, Download, AlertTriangle } from 'lucide-react'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { api, epmsApi } from '@/lib/api'

// ── OA invoice (expense_invoices) ────────────────────────────────────────────

interface OaLine {
  id: string; line_number: number; description: string
  quantity: number; unit_price: number; amount: number; tax_amount: number
  budget_account_code: string | null; budget_account_name: string | null
}

interface OaInvoice {
  id: string; file_name: string; file_mime_type: string; file_size_bytes: number
  invoice_number: string | null; vendor_name: string | null
  invoice_date: string | null; due_date: string | null; currency: string
  subtotal: number; tax_amount: number; total_amount: number
  status: string; pa_number: string | null; created_at: string
  lines: OaLine[]
}

// ── EPMS invoice ──────────────────────────────────────────────────────────────

interface EpmsLine {
  description: string; quantity: number; unit_price: number; total: number
}

interface EpmsInvoice {
  id: string; internal_ref: string; vendor_invoice_number: string
  vendor_id: string; vendor_name: string
  invoice_date: string | null; due_date: string | null; currency: string
  tax_amount: number; total_amount: number; status: string
  po_number: string | null; po_id: string | null; created_at: string
  line_items: EpmsLine[]
}

// ── Status badge ──────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  unmatched: 'bg-yellow-50 text-yellow-700',
  matched:   'bg-green-50 text-green-700',
  exception: 'bg-red-50 text-red-700',
  approved:  'bg-green-50 text-green-700',
  paid:      'bg-neutral-100 text-neutral-500',
  uploaded:  'bg-neutral-100 text-neutral-600',
  reviewed:  'bg-blue-50 text-blue-700',
  used:      'bg-green-50 text-green-700',
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={cn('inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium',
      STATUS_COLORS[status] ?? 'bg-neutral-100 text-neutral-600')}>
      {status}
    </span>
  )
}

// ── Line items table ──────────────────────────────────────────────────────────

function LinesTable({ rows, currency }: {
  rows: { description: string; quantity: number; unit_price: number; amount: number; tax_amount?: number; budget?: string | null }[]
  currency: string
}) {
  if (rows.length === 0) return (
    <p className="px-4 py-4 text-xs text-neutral-400">No line items recorded for this invoice.</p>
  )

  const subtotal = rows.reduce((s, r) => s + r.amount, 0)
  const totalTax = rows.reduce((s, r) => s + (r.tax_amount ?? 0), 0)

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-neutral-100 bg-neutral-50/50">
          <th className="px-4 py-2 text-left text-[11px] font-semibold text-neutral-400 uppercase tracking-wide">Description</th>
          <th className="px-3 py-2 text-right text-[11px] font-semibold text-neutral-400 uppercase tracking-wide w-14">Qty</th>
          <th className="px-3 py-2 text-right text-[11px] font-semibold text-neutral-400 uppercase tracking-wide w-24">Unit Price</th>
          {totalTax > 0 && <th className="px-3 py-2 text-right text-[11px] font-semibold text-neutral-400 uppercase tracking-wide w-20">Tax</th>}
          <th className="px-4 py-2 text-right text-[11px] font-semibold text-neutral-400 uppercase tracking-wide w-24">Amount</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((line, i) => (
          <tr key={i} className="border-b border-neutral-50 last:border-0">
            <td className="px-4 py-2.5">
              <p className="text-neutral-800">{line.description}</p>
              {line.budget && <p className="text-[11px] text-neutral-400 font-mono mt-0.5">{line.budget}</p>}
            </td>
            <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-600">{line.quantity}</td>
            <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-600">{formatAmount(line.unit_price, currency)}</td>
            {totalTax > 0 && <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-500">{formatAmount(line.tax_amount ?? 0, currency)}</td>}
            <td className="px-4 py-2.5 text-right font-mono text-xs font-semibold text-neutral-900">{formatAmount(line.amount, currency)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr className="border-t border-neutral-200 bg-neutral-50">
          <td colSpan={totalTax > 0 ? 4 : 3} className="px-4 py-2 text-xs text-neutral-400 text-right">Subtotal</td>
          <td className="px-4 py-2 text-right font-mono text-xs font-semibold text-neutral-700">{formatAmount(subtotal, currency)}</td>
        </tr>
        {totalTax > 0 && (
          <tr className="bg-neutral-50">
            <td colSpan={4} className="px-4 py-1.5 text-xs text-neutral-400 text-right">Tax</td>
            <td className="px-4 py-1.5 text-right font-mono text-xs text-neutral-600">{formatAmount(totalTax, currency)}</td>
          </tr>
        )}
        <tr className="border-t border-neutral-100 bg-neutral-50">
          <td colSpan={totalTax > 0 ? 4 : 3} className="px-4 py-2 text-xs font-semibold text-neutral-700 text-right">Total</td>
          <td className="px-4 py-2 text-right font-mono text-sm font-bold text-[#085E5E]">
            {formatAmount(subtotal + totalTax, currency)}
          </td>
        </tr>
      </tfoot>
    </table>
  )
}

// ── Attachments ───────────────────────────────────────────────────────────────

interface InvoiceAttachment {
  id: string; file_name: string; file_size_bytes: number
  mime_type: string | null; invoice_source: string
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** Lists the files stored for an OA invoice in invoice_attachments (file-api). */
function InvoiceAttachments({ invoiceId, fallbackName }: { invoiceId: string; fallbackName?: string | null }) {
  const { data: attachments = [], isLoading } = useQuery<InvoiceAttachment[]>({
    queryKey: ['invoice-attachments', 'oa', invoiceId],
    queryFn: () => api.get<InvoiceAttachment[]>(`/api/v1/invoice-attachments?invoice_id=${invoiceId}&invoice_source=oa`),
    enabled: !!invoiceId,
  })

  const download = async (att: InvoiceAttachment) => {
    const blob = await api.getBlob(`/api/v1/invoice-attachments/${att.id}/file`)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = att.file_name; a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
      <div className="px-4 py-2.5 border-b border-neutral-100 bg-neutral-50">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
          Attachments {attachments.length > 0 && `(${attachments.length})`}
        </h3>
      </div>
      <div className="p-4">
        {isLoading ? (
          <div className="flex items-center justify-center py-6"><Loader2 className="h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : attachments.length === 0 ? (
          <div className="flex items-center gap-2 text-xs text-neutral-400">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            No attachment stored{fallbackName ? ` for ${fallbackName}` : ''}.
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {attachments.map(att => (
              <div key={att.id} className="flex items-center gap-3 rounded-lg border border-neutral-200 px-4 py-3">
                <Paperclip className="h-4 w-4 text-neutral-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-neutral-800 truncate">{att.file_name}</p>
                  <p className="text-xs text-neutral-400">{formatSize(att.file_size_bytes)}</p>
                </div>
                <button type="button" onClick={() => download(att)}
                  className="flex items-center gap-1 text-xs font-medium text-primary-600 hover:text-primary-800 shrink-0">
                  <Download className="h-3.5 w-3.5" />Download
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── OA detail view ────────────────────────────────────────────────────────────

function OaDetailView({ id }: { id: string }) {
  const { data: inv, isLoading, error } = useQuery<OaInvoice>({
    queryKey: ['invoice-oa', id],
    queryFn: () => api.get<OaInvoice>(`/api/v1/invoices/${id}`),
  })

  if (isLoading) return <div className="flex items-center justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-neutral-400" /></div>
  if (error || !inv) return <div className="py-12 text-center text-sm text-red-500">Invoice not found</div>

  const lines = (inv.lines ?? []).map(l => ({
    description: l.description, quantity: l.quantity, unit_price: l.unit_price,
    amount: l.amount, tax_amount: l.tax_amount,
    budget: l.budget_account_code ? `${l.budget_account_code}${l.budget_account_name ? ` — ${l.budget_account_name}` : ''}` : null,
  }))

  return (
    <>
      {/* Header meta */}
      <div className="rounded-xl border border-neutral-200 bg-white p-6">
        <div className="flex items-start justify-between gap-4 mb-5">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-amber-50 text-amber-700">OA</span>
              <h1 className="text-xl font-bold text-neutral-900 font-mono">
                {inv.invoice_number ?? '—'}
              </h1>
            </div>
            <p className="text-sm text-neutral-500">{inv.file_name}</p>
          </div>
          <StatusBadge status={inv.status} />
        </div>

        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
          <div><dt className="text-neutral-400 text-xs">Vendor</dt><dd className="mt-0.5 font-medium text-neutral-900">{inv.vendor_name ?? '—'}</dd></div>
          <div><dt className="text-neutral-400 text-xs">Invoice Date</dt><dd className="mt-0.5 text-neutral-900">{inv.invoice_date ? formatDate(inv.invoice_date) : '—'}</dd></div>
          <div><dt className="text-neutral-400 text-xs">Due Date</dt><dd className="mt-0.5 text-neutral-900">{inv.due_date ? formatDate(inv.due_date) : '—'}</dd></div>
          <div><dt className="text-neutral-400 text-xs">Currency</dt><dd className="mt-0.5 text-neutral-900">{inv.currency}</dd></div>
          {inv.pa_number && <div><dt className="text-neutral-400 text-xs">PA Reference</dt><dd className="mt-0.5 font-mono text-neutral-900">{inv.pa_number}</dd></div>}
          <div><dt className="text-neutral-400 text-xs">Uploaded</dt><dd className="mt-0.5 text-neutral-900">{formatDate(inv.created_at)}</dd></div>
        </dl>
      </div>

      {/* Line items */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <div className="px-4 py-2.5 border-b border-neutral-100 bg-neutral-50">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Line Items {inv.lines.length > 0 && `(${inv.lines.length})`}
          </h3>
        </div>
        <LinesTable rows={lines} currency={inv.currency} />
      </div>

      {/* Attachments */}
      <InvoiceAttachments invoiceId={id} fallbackName={inv.file_name} />
    </>
  )
}

// ── EPMS detail view ──────────────────────────────────────────────────────────

function EpmsDetailView({ id }: { id: string }) {
  const { data: inv, isLoading, error } = useQuery<EpmsInvoice>({
    queryKey: ['invoice-epms', id],
    queryFn: () => epmsApi.get<EpmsInvoice>(`/api/v1/invoices/${id}`),
  })

  if (isLoading) return <div className="flex items-center justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-neutral-400" /></div>
  if (error || !inv) return <div className="py-12 text-center text-sm text-red-500">Invoice not found</div>

  const lines = (inv.line_items ?? []).map(l => ({
    description: l.description, quantity: l.quantity,
    unit_price: l.unit_price, amount: l.total, budget: null,
  }))

  return (
    <>
      <div className="rounded-xl border border-neutral-200 bg-white p-6">
        <div className="flex items-start justify-between gap-4 mb-5">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-primary-50 text-primary-700">EPMS</span>
              <h1 className="text-xl font-bold text-neutral-900 font-mono">{inv.vendor_invoice_number}</h1>
            </div>
            <p className="text-sm text-neutral-400 font-mono">{inv.internal_ref}</p>
          </div>
          <StatusBadge status={inv.status} />
        </div>

        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
          <div><dt className="text-neutral-400 text-xs">Vendor</dt><dd className="mt-0.5 font-medium text-neutral-900">{inv.vendor_name}</dd></div>
          <div><dt className="text-neutral-400 text-xs">Invoice Date</dt><dd className="mt-0.5 text-neutral-900">{inv.invoice_date ? formatDate(inv.invoice_date) : '—'}</dd></div>
          <div><dt className="text-neutral-400 text-xs">Due Date</dt><dd className="mt-0.5 text-neutral-900">{inv.due_date ? formatDate(inv.due_date) : '—'}</dd></div>
          <div><dt className="text-neutral-400 text-xs">Currency</dt><dd className="mt-0.5 text-neutral-900">{inv.currency}</dd></div>
          {inv.po_number && <div><dt className="text-neutral-400 text-xs">PO Reference</dt><dd className="mt-0.5 font-mono text-neutral-900">{inv.po_number}</dd></div>}
          <div><dt className="text-neutral-400 text-xs">Total</dt><dd className="mt-0.5 font-mono font-semibold text-neutral-900">{formatAmount(inv.total_amount, inv.currency)}</dd></div>
        </dl>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <div className="px-4 py-2.5 border-b border-neutral-100 bg-neutral-50">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Line Items {lines.length > 0 && `(${lines.length})`}
          </h3>
        </div>
        <LinesTable rows={lines} currency={inv.currency} />
      </div>
    </>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function InvoiceDetailPage() {
  const { source, id } = useParams<{ source: string; id: string }>()
  const navigate = useNavigate()

  const isOa = source === 'oa'
  const isEpms = source === 'epms'

  return (
    <div className="flex flex-col gap-6 max-w-3xl">
      <button onClick={() => navigate('/invoices')}
        className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 self-start">
        <ArrowLeft className="h-4 w-4" />Back to Invoices
      </button>

      {isOa  && id && <OaDetailView  id={id} />}
      {isEpms && id && <EpmsDetailView id={id} />}
      {!isOa && !isEpms && (
        <div className="py-12 text-center text-sm text-red-500">Unknown invoice source: {source}</div>
      )}
    </div>
  )
}
