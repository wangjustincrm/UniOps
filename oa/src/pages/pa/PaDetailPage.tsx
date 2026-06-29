import { useState, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Loader2, CheckCircle2, RotateCcw, XCircle,
  AlertTriangle, Paperclip, Clock, Download, FileText, CreditCard, Pencil,
} from 'lucide-react'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { api, epmsApi } from '@/lib/api'
import ProcessPaymentModal from '@/components/ProcessPaymentModal'
import { StatusBadge } from '@/components/ui/badge'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Pa {
  id: string
  pa_number: string
  title: string
  pa_type: string
  status: string
  vendor_name: string
  vendor_id: string
  po_id: string | null
  po_number: string | null
  // Financial breakdown
  subtotal: number
  tax_amount: number
  shipping_amount: number
  other_charges: number
  other_charges_note: string | null
  payment_amount: number
  currency: string
  // Linked docs
  invoice_ids: string[]
  gr_ids: string[]
  budget_account_code: string | null
  // Meta
  notes: string | null
  status_label?: string
  submitted_at: string | null
  approval_step_idx: number
  created_by: string
  created_at: string
  updated_at: string
}

interface PaPermissions {
  is_owner: boolean
  can_approve: boolean
  can_pay: boolean
}

interface InvoiceAttachment {
  id: string
  file_name: string
  file_size_bytes: number
  mime_type: string | null
  invoice_source: string
}

interface ApprovalEvent {
  id: string
  step_idx: number
  action: string
  actor_id: string
  actor_role: string
  comment: string | null
  created_at: string
}

interface InvoiceLine {
  id: string
  line_number: number
  description: string
  quantity: number
  unit_price: number
  amount: number
  tax_amount: number
  budget_account_code: string | null
  budget_account_name: string | null
}

interface Invoice {
  id: string
  invoice_number: string | null
  vendor_name: string | null
  invoice_date: string | null
  due_date: string | null
  currency: string
  subtotal: number
  tax_amount: number
  total_amount: number
  file_name: string
  status: string
  lines: InvoiceLine[]
}


// ── Action area ───────────────────────────────────────────────────────────────

function ActionArea({
  pa, perms, onAction, onOpenModal, acting, error,
}: {
  pa: Pa
  perms: PaPermissions | undefined
  onAction: (a: string) => Promise<void>
  onOpenModal: (a: string) => void
  acting: boolean
  error: string
}) {
  const { status } = pa
  const isOwner = perms?.is_owner ?? false
  const canApprove = perms?.can_approve ?? false
  const canPay = perms?.can_pay ?? false

  if ((status === 'draft' || status === 'returned') && isOwner) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          {pa.pa_type === 'PA-DIR' && (
            <a href={`/pa/${pa.id}/edit`}
              className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-white px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors">
              <Pencil className="h-4 w-4" />Edit
            </a>
          )}
          <button onClick={() => onAction('submit')} disabled={acting}
            className="flex items-center gap-2 rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50 transition-colors">
            {acting ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
            {status === 'returned' ? 'Resubmit for Approval' : 'Submit for Approval'}
          </button>
          <button onClick={() => onAction('cancel')} disabled={acting}
            className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-white px-4 py-2 text-sm font-medium text-neutral-600 hover:bg-neutral-50 disabled:opacity-50 transition-colors">
            <XCircle className="h-4 w-4" />Cancel
          </button>
        </div>
        {status === 'returned' && (
          <p className="text-xs text-warning-600 bg-warning-50 border border-warning-200 rounded-lg px-3 py-2">
            This PA was returned for revision. Check the History tab for comments before resubmitting.
          </p>
        )}
        {error && <ErrorBanner message={error} />}
      </div>
    )
  }

  if (status === 'submitted' || status === 'in_review') {
    if (canApprove) {
      return (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <button onClick={() => onOpenModal('approve')} disabled={acting}
              className="flex items-center gap-2 rounded-lg bg-success-600 px-4 py-2 text-sm font-medium text-white hover:bg-success-700 disabled:opacity-50 transition-colors">
              <CheckCircle2 className="h-4 w-4" />Approve
            </button>
            <button onClick={() => onOpenModal('return')} disabled={acting}
              className="flex items-center gap-2 rounded-lg border border-warning-200 bg-white px-4 py-2 text-sm font-medium text-warning-700 hover:bg-warning-50 disabled:opacity-50 transition-colors">
              <RotateCcw className="h-4 w-4" />Return
            </button>
            <button onClick={() => onOpenModal('reject')} disabled={acting}
              className="flex items-center gap-2 rounded-lg border border-danger-200 bg-white px-4 py-2 text-sm font-medium text-danger-600 hover:bg-danger-50 disabled:opacity-50 transition-colors">
              <XCircle className="h-4 w-4" />Reject
            </button>
          </div>
          {error && <ErrorBanner message={error} />}
        </div>
      )
    }
    if (isOwner) {
      return (
        <div className="flex flex-col gap-3">
          <button onClick={() => onAction('recall')} disabled={acting}
            className="flex items-center gap-2 self-start rounded-lg border border-warning-200 bg-warning-50 px-4 py-2 text-sm font-medium text-warning-700 hover:bg-warning-100 disabled:opacity-50 transition-colors">
            {acting ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
            Recall to Draft
          </button>
          <p className="text-xs text-neutral-500">
            Recalling will reset this PA to Draft and cancel pending approval tasks.
          </p>
          {error && <ErrorBanner message={error} />}
        </div>
      )
    }
    return null
  }

  if (status === 'approved' && canPay) {
    return (
      <div className="flex flex-col gap-3">
        <button onClick={() => onOpenModal('pay')} disabled={acting}
          className="flex items-center gap-2 self-start rounded-lg bg-success-600 px-4 py-2 text-sm font-medium text-white hover:bg-success-700 disabled:opacity-50 transition-colors">
          <CreditCard className="h-4 w-4" />Mark as Processed
        </button>
        {error && <ErrorBanner message={error} />}
      </div>
    )
  }

  return null
}

// ── Action modal (approve / return / reject with comment) ────────────────────

function ActionModal({
  action, onConfirm, onClose, loading, error,
}: {
  action: string
  onConfirm: (comment: string) => void
  onClose: () => void
  loading: boolean
  error: string
}) {
  const [comment, setComment] = useState('')
  const labels: Record<string, { title: string; color: string }> = {
    approve: { title: 'Approve Payment', color: 'bg-success-600 text-white' },
    return:  { title: 'Return for Revision', color: 'bg-warning-500 text-white' },
    reject:  { title: 'Reject Payment', color: 'bg-danger-600 text-white' },
  }
  const cfg = labels[action] ?? { title: action, color: 'bg-neutral-800 text-white' }
  const commentRequired = action === 'return' || action === 'reject'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm rounded-xl border border-neutral-200 bg-white shadow-xl">
        <div className="p-5">
          <h3 className="text-base font-semibold text-neutral-900">{cfg.title}</h3>
          <div className="mt-3">
            <label className="mb-1 block text-xs font-medium text-neutral-600">
              Comment {commentRequired ? '(required)' : '(optional)'}
            </label>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={3}
              className="w-full rounded border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 resize-none"
              placeholder="Add a comment…"
            />
          </div>
          {error && <div className="mt-3"><ErrorBanner message={error} /></div>}
        </div>
        <div className="flex justify-end gap-2 border-t border-neutral-100 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-neutral-200 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={loading || (commentRequired && !comment.trim())}
            onClick={() => onConfirm(comment)}
            className={cn('rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:opacity-50', cfg.color)}
          >
            {loading ? 'Processing…' : cfg.title}
          </button>
        </div>
      </div>
    </div>
  )
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-danger-50 border border-danger-200 px-3 py-2 text-sm text-danger-700">
      <AlertTriangle className="h-4 w-4 shrink-0" />{message}
    </div>
  )
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

const TABS = ['Details', 'Attachments', 'History'] as const
type Tab = typeof TABS[number]

function TabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  return (
    <div className="flex gap-0 border-b border-neutral-200">
      {TABS.map(tab => (
        <button key={tab} onClick={() => onChange(tab)}
          className={cn(
            'px-5 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors',
            active === tab
              ? 'border-primary-700 text-primary-700'
              : 'border-transparent text-neutral-500 hover:text-neutral-700',
          )}>
          {tab}
        </button>
      ))}
    </div>
  )
}

// ── Invoice card (fetched inline) ─────────────────────────────────────────────

function InvoiceCard({ invoiceId, currency }: { invoiceId: string; currency: string }) {
  const { data: inv, isLoading } = useQuery<Invoice>({
    queryKey: ['invoice', invoiceId],
    queryFn: () => api.get<Invoice>(`/api/v1/invoices/${invoiceId}`),
  })

  if (isLoading) return (
    <div className="flex items-center gap-2 py-4 text-sm text-neutral-400">
      <Loader2 className="h-4 w-4 animate-spin" /> Loading invoice…
    </div>
  )
  if (!inv) return null

  const cur = inv.currency || currency

  return (
    <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
      {/* Invoice header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-neutral-100 bg-neutral-50">
        <FileText className="h-4 w-4 text-neutral-400 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-neutral-800 truncate">
            {inv.file_name}
          </p>
          <p className="text-xs text-neutral-400 mt-0.5">
            {inv.invoice_number && <span className="font-mono mr-2">#{inv.invoice_number}</span>}
            {inv.vendor_name && <span>{inv.vendor_name}</span>}
            {inv.invoice_date && <span className="ml-2 text-neutral-300">·</span>}
            {inv.invoice_date && <span className="ml-2">{formatDate(inv.invoice_date)}</span>}
          </p>
        </div>
        <div className="text-right shrink-0">
          <p className="text-sm font-mono font-semibold text-neutral-900">
            {formatAmount(inv.total_amount, cur)}
          </p>
          <p className="text-[10px] text-neutral-400 uppercase">Total</p>
        </div>
      </div>

      {/* Line items */}
      {inv.lines.length > 0 ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-100 bg-neutral-50/50">
              <th className="px-4 py-2 text-left text-[11px] font-semibold text-neutral-400 uppercase tracking-wide">Description</th>
              <th className="px-3 py-2 text-right text-[11px] font-semibold text-neutral-400 uppercase tracking-wide w-14">Qty</th>
              <th className="px-3 py-2 text-right text-[11px] font-semibold text-neutral-400 uppercase tracking-wide w-24">Unit Price</th>
              <th className="px-3 py-2 text-right text-[11px] font-semibold text-neutral-400 uppercase tracking-wide w-16">Tax</th>
              <th className="px-4 py-2 text-right text-[11px] font-semibold text-neutral-400 uppercase tracking-wide w-24">Amount</th>
            </tr>
          </thead>
          <tbody>
            {inv.lines.map((line) => (
              <tr key={line.id} className="border-b border-neutral-50 last:border-0">
                <td className="px-4 py-2.5">
                  <p className="text-neutral-800">{line.description}</p>
                  {line.budget_account_code && (
                    <p className="text-xs text-neutral-400 font-mono mt-0.5">
                      {line.budget_account_code}{line.budget_account_name ? ` — ${line.budget_account_name}` : ''}
                    </p>
                  )}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-600">{line.quantity}</td>
                <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-600">
                  {formatAmount(line.unit_price, cur)}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-500">
                  {formatAmount(line.tax_amount, cur)}
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-xs font-semibold text-neutral-900">
                  {formatAmount(line.amount, cur)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-neutral-200 bg-neutral-50">
              <td colSpan={4} className="px-4 py-2 text-xs text-neutral-400 text-right">Subtotal</td>
              <td className="px-4 py-2 text-right font-mono text-xs font-semibold text-neutral-700">
                {formatAmount(inv.subtotal, cur)}
              </td>
            </tr>
            {inv.tax_amount > 0 && (
              <tr className="bg-neutral-50">
                <td colSpan={4} className="px-4 py-1.5 text-xs text-neutral-400 text-right">Tax</td>
                <td className="px-4 py-1.5 text-right font-mono text-xs text-neutral-600">
                  {formatAmount(inv.tax_amount, cur)}
                </td>
              </tr>
            )}
            <tr className="bg-neutral-50 border-t border-neutral-100">
              <td colSpan={4} className="px-4 py-2 text-xs font-semibold text-neutral-700 text-right">Total</td>
              <td className="px-4 py-2 text-right font-mono text-sm font-bold text-primary-700">
                {formatAmount(inv.total_amount, cur)}
              </td>
            </tr>
          </tfoot>
        </table>
      ) : (
        <div className="px-4 py-4 text-xs text-neutral-400">No line items recorded for this invoice.</div>
      )}
    </div>
  )
}

// ── Details tab ───────────────────────────────────────────────────────────────

function DetailsTab({ pa }: { pa: Pa }) {
  const paTypeLabel = pa.pa_type === 'PA-PO' || pa.po_number ? 'PO-Linked Payment' : 'Direct Payment'

  return (
    <div className="flex flex-col gap-6 pt-5">
      {/* Meta grid */}
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-neutral-400 text-xs">Type</dt>
          <dd className="mt-0.5 font-medium text-neutral-900">{paTypeLabel}</dd>
        </div>
        {pa.po_number && (
          <div>
            <dt className="text-neutral-400 text-xs">PO Reference</dt>
            <dd className="mt-0.5 font-medium font-mono text-neutral-900">{pa.po_number}</dd>
          </div>
        )}
        <div>
          <dt className="text-neutral-400 text-xs">Vendor</dt>
          <dd className="mt-0.5 font-medium text-neutral-900">{pa.vendor_name}</dd>
        </div>
        {pa.budget_account_code && (
          <div>
            <dt className="text-neutral-400 text-xs">Budget Account</dt>
            <dd className="mt-0.5 font-mono text-neutral-900">{pa.budget_account_code}</dd>
          </div>
        )}
        <div>
          <dt className="text-neutral-400 text-xs">Submitted</dt>
          <dd className="mt-0.5 text-neutral-900">{pa.submitted_at ? formatDate(pa.submitted_at) : '—'}</dd>
        </div>
        <div>
          <dt className="text-neutral-400 text-xs">Created</dt>
          <dd className="mt-0.5 text-neutral-900">{formatDate(pa.created_at)}</dd>
        </div>
        {pa.notes && (
          <div className="col-span-2 sm:col-span-3">
            <dt className="text-neutral-400 text-xs">Description</dt>
            <dd className="mt-0.5 text-neutral-700">{pa.notes}</dd>
          </div>
        )}
      </dl>

      {/* Financial breakdown */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <div className="px-4 py-2.5 border-b border-neutral-100 bg-neutral-50">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Payment Breakdown
          </h3>
        </div>
        <div className="p-4 space-y-2 text-sm">
          <div className="flex justify-between text-neutral-600">
            <span>Subtotal</span>
            <span className="font-mono">{formatAmount(pa.subtotal, pa.currency)}</span>
          </div>
          <div className="flex justify-between text-neutral-600">
            <span>Tax</span>
            <span className="font-mono">{formatAmount(pa.tax_amount, pa.currency)}</span>
          </div>
          {pa.shipping_amount > 0 && (
            <div className="flex justify-between text-neutral-600">
              <span>Shipping</span>
              <span className="font-mono">{formatAmount(pa.shipping_amount, pa.currency)}</span>
            </div>
          )}
          {pa.other_charges > 0 && (
            <div className="flex justify-between text-neutral-600">
              <span>{pa.other_charges_note || 'Other Charges'}</span>
              <span className="font-mono">{formatAmount(pa.other_charges, pa.currency)}</span>
            </div>
          )}
          <div className="flex justify-between font-semibold border-t border-neutral-100 pt-2 mt-1 text-neutral-900">
            <span>Total Payment</span>
            <span className="font-mono text-primary-700">{formatAmount(pa.payment_amount, pa.currency)}</span>
          </div>
        </div>
      </div>

      {/* Linked invoices — fetched and expanded inline */}
      {pa.invoice_ids.length > 0 && (
        <div className="flex flex-col gap-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Invoice{pa.invoice_ids.length > 1 ? 's' : ''} ({pa.invoice_ids.length})
          </p>
          {pa.invoice_ids.map(invId => (
            <InvoiceCard key={invId} invoiceId={invId} currency={pa.currency} />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Attachments tab ───────────────────────────────────────────────────────────

function AttachmentsTab({ pa, perms }: { pa: Pa; perms: PaPermissions | undefined }) {
  const queryClient = useQueryClient()
  const invoiceId = pa.invoice_ids[0] ?? null
  const isOwner = perms?.is_owner ?? false
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { data: attachments = [], isLoading } = useQuery<InvoiceAttachment[]>({
    queryKey: ['pa-attachments', pa.id],
    queryFn: async () => {
      if (!invoiceId) return []
      return api.get<InvoiceAttachment[]>(`/api/v1/invoice-attachments?invoice_id=${invoiceId}`)
    },
    enabled: !!invoiceId,
  })

  const refetch = () => queryClient.invalidateQueries({ queryKey: ['pa-attachments', pa.id] })

  const upload = async (files: FileList) => {
    if (!invoiceId) { setError('No linked invoice to attach to.'); return }
    setBusy(true); setError('')
    try {
      for (const f of Array.from(files)) {
        const form = new FormData(); form.append('file', f)
        await api.postForm(`/api/v1/invoice-attachments?invoice_id=${invoiceId}&invoice_source=oa`, form)
      }
      await refetch()
    } catch (e: any) {
      setError(e.message || 'Upload failed')
    } finally { setBusy(false) }
  }

  const remove = async (attId: string) => {
    setBusy(true); setError('')
    try {
      await api.delete(`/api/v1/invoice-attachments/${attId}`)
      await refetch()
    } catch (e: any) {
      setError(e.message || 'Delete failed')
    } finally { setBusy(false) }
  }

  const download = async (att: InvoiceAttachment) => {
    try {
      const blob = await api.getBlob(`/api/v1/invoice-attachments/${att.id}/file`)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = att.file_name; a.click()
      URL.revokeObjectURL(url)
    } catch (e: any) {
      setError(e.message || 'Download failed')
    }
  }

  function formatSize(bytes: number) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  return (
    <div className="flex flex-col gap-3 pt-5">
      {isOwner && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-neutral-300 px-4 py-3">
          <p className="text-sm text-neutral-500">Add supporting documents (agreements, acceptance docs, etc.)</p>
          <button type="button" disabled={busy || !invoiceId} onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-1.5 rounded-lg bg-primary-700 px-3 py-2 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50 transition-colors">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}Upload
          </button>
          <input ref={fileInputRef} type="file" multiple className="hidden"
            onChange={e => { if (e.target.files?.length) upload(e.target.files); e.target.value = '' }} />
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-danger-50 border border-danger-200 px-3 py-2 text-sm text-danger-700">
          <AlertTriangle className="h-4 w-4 shrink-0" />{error}
        </div>
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-neutral-400" /></div>
      ) : attachments.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-neutral-100 mb-3">
            <Paperclip className="h-5 w-5 text-neutral-400" />
          </div>
          <p className="text-sm text-neutral-500">No attachments</p>
        </div>
      ) : (
        attachments.map(att => (
          <div key={att.id} className="flex items-center gap-3 rounded-lg border border-neutral-200 bg-white px-4 py-3">
            <Paperclip className="h-4 w-4 text-neutral-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-neutral-800 truncate">{att.file_name}</p>
              <p className="text-xs text-neutral-400">{formatSize(att.file_size_bytes)}</p>
            </div>
            <button type="button" onClick={() => download(att)}
              className="flex items-center gap-1 text-xs font-medium text-primary-600 hover:text-primary-800 shrink-0">
              <Download className="h-3.5 w-3.5" />Download
            </button>
            {isOwner && (
              <button type="button" disabled={busy} onClick={() => remove(att.id)}
                className="flex items-center gap-1 text-xs font-medium text-danger-500 hover:text-danger-700 disabled:opacity-50 shrink-0">
                <XCircle className="h-3.5 w-3.5" />Delete
              </button>
            )}
          </div>
        ))
      )}
    </div>
  )
}

// ── History tab ───────────────────────────────────────────────────────────────

const ACTION_COLORS: Record<string, string> = {
  submit:   'bg-info-100 text-info-700',
  approve:  'bg-success-100 text-success-700',
  return:   'bg-warning-100 text-warning-700',
  reject:   'bg-danger-100 text-danger-700',
  cancel:   'bg-danger-100 text-danger-500',
  recall:   'bg-warning-100 text-warning-700',
  process:  'bg-success-100 text-success-700',
}

function HistoryTab({ paId }: { paId: string }) {
  const { data: events = [], isLoading } = useQuery<ApprovalEvent[]>({
    queryKey: ['pa-history', paId],
    queryFn: () => api.get<ApprovalEvent[]>(`/api/v1/pa/${paId}/history`),
  })

  // Resolve actor user names via epms-api's user directory (auth-only endpoint).
  // Inactive/unknown ids (404) are skipped and fall back to the role label.
  const actorIds = [...new Set(events.map(e => e.actor_id))].sort()
  const { data: nameMap = {} } = useQuery<Record<string, string>>({
    queryKey: ['pa-history-actors', actorIds],
    queryFn: async () => {
      const map: Record<string, string> = {}
      await Promise.all(actorIds.map(async (id) => {
        try {
          const u = await epmsApi.get<{ id: string; full_name: string }>(`/api/v1/users/directory/${id}`)
          map[id] = u.full_name
        } catch {
          /* inactive or unknown user — leave unmapped, render falls back to role */
        }
      }))
      return map
    },
    enabled: actorIds.length > 0,
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-5 w-5 animate-spin text-neutral-400" />
      </div>
    )
  }

  if (events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-neutral-100 mb-3">
          <Clock className="h-5 w-5 text-neutral-400" />
        </div>
        <p className="text-sm text-neutral-500">No approval history yet</p>
        <p className="text-xs text-neutral-400 mt-1">Events will appear here after this PA is submitted</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-0 pt-5">
      {events.map((ev, i) => {
        const actionLabel = ev.action.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
        const roleLabel = ev.actor_role.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
        const isLast = i === events.length - 1

        return (
          <div key={ev.id} className="flex gap-4">
            {/* Timeline spine */}
            <div className="flex flex-col items-center">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 border-neutral-200 bg-white z-10">
                <span className="text-xs font-bold text-neutral-500">{ev.step_idx + 1}</span>
              </div>
              {!isLast && <div className="w-px flex-1 bg-neutral-200 my-1" />}
            </div>

            {/* Event card */}
            <div className={cn('flex-1 pb-5', isLast && 'pb-0')}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className={cn(
                  'inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold',
                  ACTION_COLORS[ev.action] ?? 'bg-neutral-100 text-neutral-600',
                )}>
                  {actionLabel}
                </span>
                <span className="text-sm font-medium text-neutral-800">{nameMap[ev.actor_id] ?? roleLabel}</span>
                <span className="text-xs text-neutral-400 ml-auto">
                  {formatDate(ev.created_at)}
                </span>
              </div>
              {ev.comment && (
                <p className="mt-1.5 text-sm text-neutral-600 bg-neutral-50 rounded-lg px-3 py-2 border border-neutral-100">
                  "{ev.comment}"
                </p>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function PaDetailPage() {
  const { id } = useParams<{ id: string }>()
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<Tab>('Details')
  const [acting, setActing] = useState(false)
  const [actError, setActError] = useState('')
  const [activeModal, setActiveModal] = useState<string | null>(null)

  const { data: pa, isLoading, error } = useQuery<Pa>({
    queryKey: ['pa', id],
    queryFn: () => api.get<Pa>(`/api/v1/pa/${id}`),
    enabled: !!id,
  })

  // Server-computed permissions — approval roles (Finance BP, etc.) are assignments,
  // not JWT roles, so authorization is resolved server-side via tasks + role_management.
  const { data: perms } = useQuery<PaPermissions>({
    queryKey: ['pa-permissions', id],
    queryFn: () => api.get<PaPermissions>(`/api/v1/pa/${id}/permissions`),
    enabled: !!id,
  })

  const invalidateAll = async () => {
    await queryClient.invalidateQueries({ queryKey: ['pa', id] })
    await queryClient.invalidateQueries({ queryKey: ['pa-permissions', id] })
    await queryClient.invalidateQueries({ queryKey: ['pa-list'] })
    await queryClient.invalidateQueries({ queryKey: ['pa-history', id] })
    await queryClient.invalidateQueries({ queryKey: ['oa-tasks'] })
  }

  const handleAction = async (action: string, comment?: string) => {
    if (!id) return
    setActing(true); setActError('')
    try {
      await api.post(`/api/v1/pa/${id}/action`, { action, comment: comment || null })
      setActiveModal(null)
      await invalidateAll()
    } catch (e: any) {
      setActError(e.message || `Failed to ${action} PA`)
    } finally {
      setActing(false)
    }
  }

  const handlePay = async (bankAccountId: string) => {
    if (!id) return
    setActing(true); setActError('')
    try {
      await api.post(`/api/v1/pa/${id}/pay`, { bank_account_id: bankAccountId })
      setActiveModal(null)
      await invalidateAll()
    } catch (e: any) {
      setActError(e.message || 'Failed to record payment')
    } finally {
      setActing(false)
    }
  }

  if (isLoading) return (
    <div className="flex items-center justify-center py-16">
      <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
    </div>
  )

  if (error || !pa) return (
    <div className="py-16 text-center text-sm text-danger-500">Payment application not found</div>
  )

  return (
    <div className="flex flex-col gap-6 max-w-3xl">
      {/* Back */}
      <a href="/pa" className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 self-start">
        <ArrowLeft className="h-4 w-4" />Back to PA List
      </a>

      {/* Header */}
      <div className="rounded-xl border border-neutral-200 bg-white p-6 flex flex-col gap-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold text-neutral-900 font-mono">{pa.pa_number}</h1>
            <p className="mt-0.5 text-sm text-neutral-500">{pa.title}</p>
          </div>
          <StatusBadge status={pa.status} />
        </div>

        {/* Action buttons — visibility resolved server-side via /permissions */}
        {(
          (['draft', 'returned'].includes(pa.status) && perms?.is_owner) ||
          (['submitted', 'in_review'].includes(pa.status) && (perms?.is_owner || perms?.can_approve)) ||
          (pa.status === 'approved' && perms?.can_pay)
        ) && (
          <div className="border-t border-neutral-100 pt-4">
            <ActionArea pa={pa} perms={perms} onAction={handleAction}
              onOpenModal={(a) => { setActError(''); setActiveModal(a) }}
              acting={acting} error={actError} />
          </div>
        )}
      </div>

      {/* Approve / Return / Reject modal */}
      {activeModal && activeModal !== 'pay' && (
        <ActionModal
          action={activeModal}
          loading={acting}
          error={actError}
          onClose={() => setActiveModal(null)}
          onConfirm={(comment) => handleAction(activeModal, comment)}
        />
      )}

      {/* Mark as Processed modal */}
      {activeModal === 'pay' && (
        <ProcessPaymentModal
          docNumber={pa.pa_number}
          currency={pa.currency}
          amount={Number(pa.payment_amount)}
          busy={acting}
          onConfirm={handlePay}
          onClose={() => setActiveModal(null)}
        />
      )}

      {/* Tabs */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <div className="px-6 pt-4">
          <TabBar active={activeTab} onChange={setActiveTab} />
        </div>
        <div className="px-6 pb-6">
          {activeTab === 'Details'     && <DetailsTab pa={pa} />}
          {activeTab === 'Attachments' && <AttachmentsTab pa={pa} perms={perms} />}
          {activeTab === 'History'     && <HistoryTab paId={pa.id} />}
        </div>
      </div>
    </div>
  )
}
