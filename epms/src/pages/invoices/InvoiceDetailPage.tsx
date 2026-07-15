import { useState, useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams, Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, FileText, ExternalLink, CheckCircle2,
  AlertTriangle, GitMerge, Paperclip, TrendingUp, Trash2, Pencil, X, Plus, Save, Upload, UserPlus,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn, formatAmount, formatDate, formatDateTime } from '@/lib/utils'
import { EXPENSE_BASE } from '@/lib/api'
import { computeSla } from '@/stores/invoice.store'
import type { InvoiceStatus, InvoiceLineItem } from '@/services/invoices'
import { useInvoice, useDeleteInvoice, useUpdateInvoice, useReviewMatch } from '@/hooks/useInvoices'
import { InvoiceTaxSection } from '@/components/invoices/InvoiceTaxSection'
import { useGr, useGrs } from '@/hooks/useGrs'
import { useAuthStore } from '@/stores/auth.store'
import { useRolePermissions } from '@/hooks/useConfig'
import { AssignMatchDialog } from './AssignMatchDialog'
import { MatchPanel } from './MatchPanel'

const MATCH_ROLES = new Set(['system_admin', 'ap_clerk', 'finance_manager', 'finance_bp'])

// ─── Status badge ─────────────────────────────────────────────────────────────

const STATUS_CFG: Record<InvoiceStatus, { label: string; variant: 'neutral' | 'warning' | 'info' | 'success' | 'danger'; dot: string }> = {
  unmatched:    { label: 'Unmatched',       variant: 'warning', dot: 'bg-warning-500'  },
  matched:      { label: 'Matched',         variant: 'success', dot: 'bg-success-600'  },
  exception:    { label: 'Exception',       variant: 'danger',  dot: 'bg-danger-600'   },
  match_review: { label: 'Pending Review',  variant: 'info',    dot: 'bg-primary-500'  },
  approved:     { label: 'Approved',        variant: 'success', dot: 'bg-success-600'  },
  paid:         { label: 'Paid',            variant: 'neutral', dot: 'bg-neutral-400'  },
}

function InvoiceStatusBadge({ status }: { status: InvoiceStatus }) {
  const c = STATUS_CFG[status]
  return (
    <Badge variant={c.variant}>
      <span className={cn('size-1.5 rounded-full', c.dot)} />
      {c.label}
    </Badge>
  )
}

// ─── 3-way match result row ───────────────────────────────────────────────────

function MatchRow({
  icon, label, ref: docRef, href, amount, currency, note, ok,
}: {
  icon: React.ReactNode
  label: string
  ref?: string
  href?: string
  amount?: number
  currency?: string
  note?: string
  ok?: boolean
}) {
  return (
    <tr className="border-b border-neutral-100">
      <td className="px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-medium text-neutral-900">
          {icon}{label}
        </div>
      </td>
      <td className="px-4 py-3">
        {docRef && href
          ? <Link to={href} className="font-mono text-xs text-primary-600 hover:underline inline-flex items-center gap-1">
              {docRef} <ExternalLink className="h-3 w-3" />
            </Link>
          : <span className="text-neutral-300 text-xs">—</span>
        }
      </td>
      <td className="px-4 py-3 text-right">
        {amount != null && currency
          ? <span className="font-mono text-sm font-semibold text-neutral-900">{formatAmount(amount, currency)}</span>
          : <span className="text-neutral-300 text-xs">—</span>
        }
      </td>
      <td className="px-4 py-3 text-xs text-neutral-500">{note}</td>
      <td className="px-4 py-3 text-center">
        {ok === true  && <CheckCircle2 className="h-4 w-4 text-success-600 mx-auto" />}
        {ok === false && <AlertTriangle className="h-4 w-4 text-danger-600 mx-auto" />}
        {ok == null   && <span className="text-neutral-300">—</span>}
      </td>
    </tr>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { user } = useAuthStore()
  const perms = useRolePermissions().data?.permissions
  const deleteInvoice = useDeleteInvoice()

  const { data: inv, isLoading } = useInvoice(id ?? '')
  const { data: gr } = useGr(inv?.gr_id ?? '')
  const updateInvoice = useUpdateInvoice()

  const [activeTab, setActiveTab] = useState<'details' | 'match' | 'history'>('details')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editing, setEditing] = useState(false)
  const [showAssignDialog, setShowAssignDialog] = useState(false)
  const [showMatchPanel, setShowMatchPanel] = useState(false)
  const [reviewNote, setReviewNote] = useState('')
  const reviewMutation = useReviewMatch()

  // Edit form state
  const [editVendorInvoiceNumber, setEditVendorInvoiceNumber] = useState('')
  const [editInvoiceDate, setEditInvoiceDate] = useState('')
  const [editDueDate, setEditDueDate] = useState('')
  const [editAmount, setEditAmount] = useState('')
  const [editTaxAmount, setEditTaxAmount] = useState('')
  const [editCurrency, setEditCurrency] = useState('CAD')
  const [editNotes, setEditNotes] = useState('')
  const [editLineItems, setEditLineItems] = useState<InvoiceLineItem[]>([])
  const [editGrIds, setEditGrIds] = useState<string[]>([])
  const [editError, setEditError] = useState<string | null>(null)

  // Fetch GRs for the linked PO when in edit mode (for GR selector)
  const { data: poGrsData } = useGrs(
    { po_id: inv?.po_id ?? '', page_size: 100 },
    editing && !!inv?.po_id,
  )
  const poGrs = poGrsData?.items ?? []

  const startEdit = () => {
    if (!inv) return
    setEditVendorInvoiceNumber(inv.vendor_invoice_number)
    setEditInvoiceDate(inv.invoice_date)
    setEditDueDate(inv.due_date)
    setEditAmount(String(inv.amount))
    setEditTaxAmount(String(inv.tax_amount))
    setEditCurrency(inv.currency)
    setEditNotes(inv.notes ?? '')
    setEditLineItems(inv.line_items ?? [])
    setEditGrIds(inv.gr_ids ?? (inv.gr_id ? [inv.gr_id] : []))
    setEditError(null)
    setEditing(true)
    setActiveTab('details')
  }

  const cancelEdit = () => {
    setEditing(false)
    setEditError(null)
  }

  const saveEdit = async () => {
    if (!inv) return
    setEditError(null)
    const amtNum = parseFloat(editAmount)
    const taxNum = parseFloat(editTaxAmount) || 0
    if (!editVendorInvoiceNumber.trim() || !editInvoiceDate || !editDueDate || isNaN(amtNum) || amtNum <= 0) {
      setEditError('Vendor Invoice #, Invoice Date, Due Date and Amount are required')
      return
    }
    try {
      await updateInvoice.mutateAsync({
        id: inv.id,
        vendor_invoice_number: editVendorInvoiceNumber.trim(),
        invoice_date: editInvoiceDate,
        due_date: editDueDate,
        amount: amtNum,
        tax_amount: taxNum,
        currency: editCurrency,
        notes: editNotes.trim() || undefined,
        line_items: editLineItems.length > 0 ? editLineItems : [],
        // Always send gr_ids when the invoice has a linked PO so backend uses
        // the user's selection rather than the previous value
        ...(inv.po_id ? { gr_ids: editGrIds } : {}),
      })
      setEditing(false)
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Save failed')
    }
  }

  const addEditLineItem = () =>
    setEditLineItems((prev) => [...prev, { description: '', quantity: 1, unit: null, unit_price: 0, line_total: 0 }])

  const updateEditLineItem = (idx: number, patch: Partial<InvoiceLineItem>) =>
    setEditLineItems((prev) => prev.map((item, i) => {
      if (i !== idx) return item
      const merged = { ...item, ...patch }
      merged.line_total = parseFloat(String(merged.quantity)) * parseFloat(String(merged.unit_price)) || 0
      return merged
    }))

  const removeEditLineItem = (idx: number) =>
    setEditLineItems((prev) => prev.filter((_, i) => i !== idx))

  const hasInvoiceUpload =
    user?.role === 'system_admin' || !!perms?.invoice_upload

  const isAp = !!user?.role && MATCH_ROLES.has(user.role)

  const canDelete =
    hasInvoiceUpload && !!inv && (inv.status === 'unmatched' || inv.status === 'exception')

  const canEdit =
    hasInvoiceUpload && !!inv && (inv.status === 'unmatched' || inv.status === 'exception' || inv.status === 'matched')

  const handleDelete = async () => {
    if (!inv) return
    try {
      await deleteInvoice.mutateAsync(inv.id)
      navigate('/invoices')
    } catch {
      // error displayed via deleteInvoice.isError below
    }
  }

  if (isLoading) return <div className="p-8 text-center text-neutral-400">Loading…</div>
  if (!inv) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-lg font-semibold text-neutral-500">Invoice not found</p>
        <Link to="/invoices" className="mt-4 text-sm text-primary-600 hover:underline">← Back to Invoices</Link>
      </div>
    )
  }

  const sla = inv.status === 'unmatched' ? computeSla(inv.uploaded_at) : null

  const varianceAbs = Math.abs(Number(inv.variance ?? 0))
  const variancePctAbs = Math.abs(Number(inv.variance_pct ?? 0))
  const hasException = inv.status === 'exception'

  const tabs = [
    { key: 'details' as const, label: 'Invoice Details' },
    { key: 'match'   as const, label: '3-Way Match' },
    { key: 'history' as const, label: 'History' },
  ]

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <Link to="/invoices" className="mt-1 text-neutral-400 hover:text-neutral-600 transition-colors">
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-2xl font-bold text-neutral-900 font-mono">{inv.internal_ref}</h1>
              <InvoiceStatusBadge status={inv.status} />
              {sla && (
                <span className={cn('text-xs font-medium rounded-full px-2 py-0.5',
                  sla.status === 'overdue'  ? 'bg-danger-50 text-danger-700'   :
                  sla.status === 'warning'  ? 'bg-warning-50 text-warning-700' :
                  'bg-success-50 text-success-700')}>
                  {sla.status === 'overdue' ? `⚠ SLA overdue (${sla.days}d)` : sla.status === 'warning' ? '⚠ Due today' : '✓ On time'}
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-neutral-500">
              {inv.vendor_name} · Vendor ref: {inv.vendor_invoice_number}{inv.uploaded_at ? ` · Uploaded ${formatDate(inv.uploaded_at)}` : ''}
            </p>
          </div>
        </div>

        {/* Header actions */}
        <div className="flex items-center gap-2">
          {!!inv && (inv.status === 'unmatched' || inv.status === 'exception') && !editing &&
            (isAp || (inv.match_assignee_id != null && inv.match_assignee_id === user?.id)) && (
            <Button size="sm" className="gap-1.5" onClick={() => setShowMatchPanel((v) => !v)}>
              <GitMerge className="h-3.5 w-3.5" />
              Match to PO
            </Button>
          )}
          {isAp && !!inv && (inv.status === 'unmatched' || inv.status === 'exception') && !editing && (
            <Button variant="secondary" size="sm" className="gap-1.5" onClick={() => setShowAssignDialog(true)}>
              <UserPlus className="h-3.5 w-3.5" />
              {inv.match_assignee_name ? `Reassign (${inv.match_assignee_name})` : 'Assign'}
            </Button>
          )}
          {canEdit && !editing && (
            <Button variant="secondary" size="sm" className="gap-1.5" onClick={startEdit}>
              <Pencil className="h-3.5 w-3.5" />
              Edit
            </Button>
          )}
          {canDelete && !editing && (
            <>
              {deleteInvoice.isError && (
                <span className="text-xs text-danger-600">
                  {deleteInvoice.error instanceof Error ? deleteInvoice.error.message : 'Delete failed'}
                </span>
              )}
              {confirmDelete ? (
                <>
                  <span className="text-xs text-danger-600 font-medium">Delete this invoice?</span>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setConfirmDelete(false)}
                    disabled={deleteInvoice.isPending}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    className="bg-danger-600 hover:bg-danger-700 text-white gap-1.5"
                    onClick={handleDelete}
                    disabled={deleteInvoice.isPending}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {deleteInvoice.isPending ? 'Deleting…' : 'Confirm Delete'}
                  </Button>
                </>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  className="gap-1.5 text-danger-600 border-danger-200 hover:bg-danger-50"
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Delete
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Inline match panel — Task Inbox / email links land on this page */}
      {showMatchPanel && !!inv && (inv.status === 'unmatched' || inv.status === 'exception') && (
        <MatchPanel inv={inv} onClose={() => setShowMatchPanel(false)} />
      )}

      {/* Exception banner */}
      {hasException && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 p-4 flex gap-3">
          <AlertTriangle className="h-5 w-5 text-danger-600 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-danger-700">
              Invoice variance: {inv.currency} {formatAmount(varianceAbs, inv.currency)} ({variancePctAbs.toFixed(1)}% over PO)
            </p>
            <p className="text-sm text-danger-600 mt-0.5">{inv.exception_reason}</p>
            {inv.exception_resolved_at && (
              <p className="text-xs text-neutral-500 mt-1">
                Resolved by {inv.exception_resolved_by_name ?? inv.exception_resolved_by} on {formatDateTime(inv.exception_resolved_at)} —{' '}
                {inv.exception_resolution === 'accepted' ? 'Accepted with justification' : 'Credit note requested'}
              </p>
            )}
          </div>
        </div>
      )}

      <div className="flex gap-6 items-start">
        {/* Main content */}
        <div className="flex-1 min-w-0 flex flex-col gap-4">
          {/* Tabs */}
          <div className="flex gap-1 border-b border-neutral-200">
            {tabs.map((t) => (
              <button key={t.key} onClick={() => setActiveTab(t.key)}
                className={cn('px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors',
                  activeTab === t.key ? 'border-primary-600 text-primary-600' : 'border-transparent text-neutral-500 hover:text-neutral-700')}>
                {t.label}
              </button>
            ))}
          </div>

          {/* ── Details tab ───────────────────────────────────────────────────── */}
          {activeTab === 'details' && !editing && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {/* Match review panel — shown to AP roles when invoice is pending review */}
              {inv.status === 'match_review' && isAp && (
                <div className="sm:col-span-2 rounded-xl border border-primary-200 bg-primary-50 p-4 flex flex-col gap-3">
                  <p className="text-sm font-semibold text-primary-800">Match pending review</p>
                  <p className="text-xs text-neutral-600">
                    This invoice was matched with a variance of {formatAmount(Math.abs(Number(inv.variance ?? 0)), inv.currency)}
                    {' '}against PO reference {formatAmount(Number(inv.po_total ?? 0), inv.currency)}. Approve to finalize the match,
                    or reject to send it back to {inv.match_assignee_name ?? 'the assignee'}.
                  </p>
                  <textarea rows={2} value={reviewNote} onChange={(e) => setReviewNote(e.target.value)}
                    placeholder="Review note (required to reject)..."
                    className="px-3 py-2 rounded-lg border border-neutral-300 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-primary-600" />
                  {reviewMutation.isError && (
                    <p className="text-xs text-danger-600">
                      {reviewMutation.error instanceof Error ? reviewMutation.error.message : 'Review action failed'}
                    </p>
                  )}
                  <div className="flex gap-2">
                    <Button size="sm" disabled={reviewMutation.isPending}
                      onClick={() => reviewMutation.mutate(
                        { id: inv.id, action: 'approve', note: reviewNote || undefined },
                        { onSuccess: () => setReviewNote('') },
                      )}>
                      Approve Match
                    </Button>
                    <Button size="sm" variant="secondary" disabled={reviewMutation.isPending || !reviewNote.trim()}
                      onClick={() => reviewMutation.mutate(
                        { id: inv.id, action: 'reject', note: reviewNote },
                        { onSuccess: () => setReviewNote('') },
                      )}>
                      Reject — send back
                    </Button>
                  </div>
                </div>
              )}

              {/* Invoice fields */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">Invoice Information</h3>
                <div className="flex flex-col gap-2.5">
                  <MetaRow label="Internal Ref"     value={inv.internal_ref}          mono />
                  <MetaRow label="Vendor Invoice #" value={inv.vendor_invoice_number}  mono />
                  <MetaRow label="Vendor"           value={inv.vendor_name} />
                  <MetaRow label="Invoice Date"     value={formatDate(inv.invoice_date)} />
                  <MetaRow label="Due Date"         value={formatDate(inv.due_date)} />
                  <MetaRow label="Uploaded"         value={formatDateTime(inv.uploaded_at)} />
                  <MetaRow label="Uploaded By"      value={inv.uploaded_by_name ?? '—'} />
                </div>
              </div>

              {/* Amounts */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">Amounts</h3>
                <div className="flex flex-col gap-2.5">
                  <MetaRow label="Pre-tax Amount"  value={formatAmount(inv.amount, inv.currency)}    mono />
                  <MetaRow label="Tax Amount"      value={formatAmount(inv.tax_amount, inv.currency)} mono />
                  <div className="border-t border-neutral-100 pt-2.5">
                    <MetaRow label="Total Amount"  value={formatAmount(inv.total_amount, inv.currency)} mono />
                  </div>
                  <MetaRow label="Currency"        value={inv.currency} />
                  {inv.matched_at && (
                    <>
                      <div className="border-t border-neutral-100 pt-2.5">
                        <MetaRow label="Matched On" value={formatDateTime(inv.matched_at)} />
                        <MetaRow label="Matched By" value={inv.matched_by_name ?? inv.matched_by ?? '—'} />
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Linked documents */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5 sm:col-span-2">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">Linked Documents</h3>
                <div className="grid grid-cols-2 gap-3">
                  {(() => {
                    const linkedPos = Array.from(
                      new Map(
                        (inv.allocations ?? []).map((a) => [a.po_id, a.po_number ?? a.po_id.slice(0, 8)]),
                      ).entries(),
                    )
                    if (linkedPos.length === 0 && inv.po_id) {
                      linkedPos.push([inv.po_id, inv.po_number ?? inv.po_id.slice(0, 8)])
                    }
                    return linkedPos.length > 0 ? (
                      <div className="rounded-lg border border-neutral-200 px-3 py-2.5">
                        <p className="text-xs text-neutral-400 mb-1">
                          Purchase Order{linkedPos.length > 1 ? `s (${linkedPos.length})` : ''}
                        </p>
                        <div className="flex flex-col gap-1">
                          {linkedPos.map(([pid, label]) => (
                            <Link
                              key={pid}
                              to={`/po/${pid}`}
                              className="text-sm font-mono font-medium text-primary-600 hover:underline inline-flex items-center gap-1"
                            >
                              {label} <ExternalLink className="h-3 w-3" />
                            </Link>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-center justify-center rounded-lg border border-dashed border-neutral-200 px-3 py-4">
                        <p className="text-xs text-neutral-400">Not yet matched to a PO</p>
                      </div>
                    )
                  })()}
                  {gr ? (
                    <div className="flex items-center justify-between rounded-lg border border-neutral-200 px-3 py-2.5">
                      <div>
                        <p className="text-xs text-neutral-400">Goods Receipt</p>
                        <Link to={`/gr/${inv.gr_id}`} className="text-sm font-mono font-medium text-primary-600 hover:underline">
                          {inv.gr_number}
                        </Link>
                      </div>
                      <Link to={`/gr/${inv.gr_id}`}><ExternalLink className="h-4 w-4 text-neutral-400 hover:text-primary-600" /></Link>
                    </div>
                  ) : (
                    <div className="flex items-center justify-center rounded-lg border border-dashed border-neutral-200 px-3 py-4">
                      <p className="text-xs text-neutral-400">No GR linked</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Line Items */}
              {inv.line_items && inv.line_items.length > 0 && (
                <div className="rounded-xl border border-neutral-200 bg-white p-5 sm:col-span-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">
                    Line Items <span className="ml-1 normal-case font-normal text-neutral-300">({inv.line_items.length})</span>
                  </h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-neutral-100">
                          <th className="pb-2 text-left text-xs font-semibold text-neutral-400 uppercase tracking-wide">Description</th>
                          <th className="pb-2 text-right text-xs font-semibold text-neutral-400 uppercase tracking-wide w-16">Qty</th>
                          <th className="pb-2 text-left text-xs font-semibold text-neutral-400 uppercase tracking-wide w-16">Unit</th>
                          <th className="pb-2 text-right text-xs font-semibold text-neutral-400 uppercase tracking-wide w-28">Unit Price</th>
                          <th className="pb-2 text-right text-xs font-semibold text-neutral-400 uppercase tracking-wide w-28">Line Total</th>
                        </tr>
                      </thead>
                      <tbody>
                        {inv.line_items.map((item, idx) => (
                          <tr key={idx} className="border-b border-neutral-50 last:border-0">
                            <td className="py-2.5 pr-4 text-neutral-800">{item.description}</td>
                            <td className="py-2.5 text-right font-mono text-xs text-neutral-600">{Number(item.quantity)}</td>
                            <td className="py-2.5 pl-3 text-xs text-neutral-400">{item.unit ?? '—'}</td>
                            <td className="py-2.5 text-right font-mono text-xs text-neutral-600">{formatAmount(Number(item.unit_price), inv.currency)}</td>
                            <td className="py-2.5 text-right font-mono text-xs font-semibold text-neutral-900">{formatAmount(Number(item.line_total), inv.currency)}</td>
                          </tr>
                        ))}
                      </tbody>
                      <tfoot>
                        <tr className="border-t border-neutral-200">
                          <td colSpan={4} className="pt-2.5 text-xs text-neutral-400 text-right pr-4">Subtotal</td>
                          <td className="pt-2.5 text-right font-mono text-xs font-semibold text-neutral-900">
                            {formatAmount(inv.line_items.reduce((s, i) => s + Number(i.line_total), 0), inv.currency)}
                          </td>
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                </div>
              )}

              {/* Tax lines — line-level GST/HST/PST/QST split with ITC (A2) */}
              <div className="sm:col-span-2">
                <InvoiceTaxSection
                  invoiceId={inv.id}
                  currency={inv.currency}
                  pretaxAmount={Number(inv.amount)}
                  headerTax={Number(inv.tax_amount)}
                  editable={hasInvoiceUpload && inv.status !== 'paid'}
                />
              </div>

              {/* Attachments */}
              <div className="sm:col-span-2">
                <InvoiceAttachments invoiceId={inv.id} fileName={inv.file_name} fileSize={inv.file_size} canUpload={hasInvoiceUpload} />
              </div>

              {/* File / notes */}
              {(inv.file_name || inv.notes) && (
                <div className="rounded-xl border border-neutral-200 bg-white p-5 sm:col-span-2">
                  {inv.file_name && (
                    <div className="flex items-center gap-3 mb-3">
                      <Paperclip className="h-4 w-4 text-neutral-400" />
                      <span className="text-sm text-neutral-700">{inv.file_name}</span>
                      {inv.file_size && <span className="text-xs text-neutral-400">{inv.file_size}</span>}
                      <Button variant="secondary" size="sm" className="ml-auto">Download</Button>
                    </div>
                  )}
                  {inv.notes && (
                    <p className="text-sm text-neutral-600 border-t border-neutral-100 pt-3 mt-3">{inv.notes}</p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* ── Edit form ─────────────────────────────────────────────────────── */}
          {editing && (
            <div className="flex flex-col gap-4">
              {/* Basic info */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-4">Invoice Information</h3>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <label className="block text-xs font-medium text-neutral-500 mb-1">Vendor Invoice #</label>
                    <input
                      value={editVendorInvoiceNumber}
                      onChange={(e) => setEditVendorInvoiceNumber(e.target.value)}
                      className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-neutral-500 mb-1">Vendor</label>
                    <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-500">
                      {inv.vendor_name}
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-neutral-500 mb-1">Invoice Date</label>
                    <input
                      type="date"
                      value={editInvoiceDate}
                      onChange={(e) => setEditInvoiceDate(e.target.value)}
                      className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-neutral-500 mb-1">Due Date</label>
                    <input
                      type="date"
                      value={editDueDate}
                      onChange={(e) => setEditDueDate(e.target.value)}
                      className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
                    />
                  </div>
                  {inv.po_id && (
                    <div className="sm:col-span-2">
                      <label className="block text-xs font-medium text-neutral-500 mb-2">
                        Goods Receipts
                        <span className="ml-1 font-normal text-neutral-400">(PO: {inv.po_number})</span>
                        {editGrIds.length > 0 && (
                          <span className="ml-2 rounded-full bg-primary-100 px-1.5 py-0.5 text-[10px] font-semibold text-primary-700">
                            {editGrIds.length} selected
                          </span>
                        )}
                      </label>
                      {poGrs.filter((g) => g.status !== 'cancelled').length === 0 ? (
                        <p className="text-xs text-neutral-400 italic">No GRs found for this PO</p>
                      ) : (
                        <div className="flex flex-col gap-1.5 rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                          {poGrs
                            .filter((g) => g.status !== 'cancelled')
                            .map((g) => {
                              const checked = editGrIds.includes(g.id)
                              return (
                                <label
                                  key={g.id}
                                  className={cn(
                                    'flex items-center gap-3 rounded-md px-2.5 py-2 cursor-pointer transition-colors',
                                    checked ? 'bg-primary-50 border border-primary-200' : 'border border-transparent hover:bg-white',
                                  )}
                                >
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={(e) =>
                                      setEditGrIds((prev) =>
                                        e.target.checked
                                          ? [...prev, g.id]
                                          : prev.filter((id) => id !== g.id),
                                      )
                                    }
                                    className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-500"
                                  />
                                  <span className="font-mono text-sm font-medium text-neutral-900">{g.number}</span>
                                  <span className="text-xs text-neutral-500 flex-1 truncate">{g.title}</span>
                                  <span className="text-xs text-neutral-400 shrink-0">
                                    {g.status.replace(/_/g, ' ')}
                                  </span>
                                </label>
                              )
                            })}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* Amounts */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-4">Amounts</h3>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div>
                    <label className="block text-xs font-medium text-neutral-500 mb-1">Pre-tax Amount</label>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={editAmount}
                      onChange={(e) => setEditAmount(e.target.value)}
                      className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-neutral-500 mb-1">Tax Amount</label>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={editTaxAmount}
                      onChange={(e) => setEditTaxAmount(e.target.value)}
                      className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-neutral-500 mb-1">Currency</label>
                    <select
                      value={editCurrency}
                      onChange={(e) => setEditCurrency(e.target.value)}
                      className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
                    >
                      {['CAD', 'USD', 'EUR', 'RMB'].map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="mt-3 flex items-center justify-between rounded-lg bg-neutral-50 border border-neutral-200 px-4 py-2.5">
                  <span className="text-xs text-neutral-500">Total Amount</span>
                  <span className="font-mono text-sm font-semibold text-neutral-900">
                    {editCurrency} {((parseFloat(editAmount) || 0) + (parseFloat(editTaxAmount) || 0)).toLocaleString('en-CA', { minimumFractionDigits: 2 })}
                  </span>
                </div>
              </div>

              {/* Notes */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-4">Notes</h3>
                <textarea
                  value={editNotes}
                  onChange={(e) => setEditNotes(e.target.value)}
                  rows={3}
                  placeholder="Optional notes…"
                  className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 resize-none"
                />
              </div>

              {/* Line Items */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400">Line Items</h3>
                  <button
                    onClick={addEditLineItem}
                    className="flex items-center gap-1 text-xs font-medium text-primary-600 hover:text-primary-700"
                  >
                    <Plus className="h-3.5 w-3.5" /> Add Row
                  </button>
                </div>
                {editLineItems.length === 0 ? (
                  <p className="text-xs text-neutral-400 text-center py-4">No line items — click Add Row to add one</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-neutral-100">
                          <th className="pb-2 text-left text-xs font-semibold text-neutral-400 uppercase tracking-wide">Description</th>
                          <th className="pb-2 text-right text-xs font-semibold text-neutral-400 uppercase tracking-wide w-20">Qty</th>
                          <th className="pb-2 text-left text-xs font-semibold text-neutral-400 uppercase tracking-wide w-20">Unit</th>
                          <th className="pb-2 text-right text-xs font-semibold text-neutral-400 uppercase tracking-wide w-28">Unit Price</th>
                          <th className="pb-2 text-right text-xs font-semibold text-neutral-400 uppercase tracking-wide w-28">Total</th>
                          <th className="pb-2 w-8" />
                        </tr>
                      </thead>
                      <tbody>
                        {editLineItems.map((item, idx) => (
                          <tr key={idx} className="border-b border-neutral-50 last:border-0">
                            <td className="py-1.5 pr-2">
                              <input
                                value={item.description}
                                onChange={(e) => updateEditLineItem(idx, { description: e.target.value })}
                                placeholder="Description"
                                className="w-full rounded border border-neutral-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary-400"
                              />
                            </td>
                            <td className="py-1.5 px-1">
                              <input
                                type="number"
                                min="0"
                                step="1"
                                value={item.quantity}
                                onChange={(e) => updateEditLineItem(idx, { quantity: parseFloat(e.target.value) || 0 })}
                                className="w-full rounded border border-neutral-200 px-2 py-1 text-xs text-right font-mono focus:outline-none focus:ring-1 focus:ring-primary-400"
                              />
                            </td>
                            <td className="py-1.5 px-1">
                              <input
                                value={item.unit ?? ''}
                                onChange={(e) => updateEditLineItem(idx, { unit: e.target.value || null })}
                                placeholder="unit"
                                className="w-full rounded border border-neutral-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary-400"
                              />
                            </td>
                            <td className="py-1.5 px-1">
                              <input
                                type="number"
                                min="0"
                                step="0.01"
                                value={item.unit_price}
                                onChange={(e) => updateEditLineItem(idx, { unit_price: parseFloat(e.target.value) || 0 })}
                                className="w-full rounded border border-neutral-200 px-2 py-1 text-xs text-right font-mono focus:outline-none focus:ring-1 focus:ring-primary-400"
                              />
                            </td>
                            <td className="py-1.5 px-1 text-right font-mono text-xs text-neutral-700">
                              {(Number(item.quantity) * Number(item.unit_price)).toLocaleString('en-CA', { minimumFractionDigits: 2 })}
                            </td>
                            <td className="py-1.5 pl-1">
                              <button
                                onClick={() => removeEditLineItem(idx)}
                                className="rounded p-1 text-neutral-400 hover:text-danger-600 hover:bg-danger-50"
                              >
                                <X className="h-3.5 w-3.5" />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Save / Cancel */}
              {editError && (
                <div className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700">
                  {editError}
                </div>
              )}
              <div className="flex justify-end gap-3">
                <Button variant="secondary" size="sm" onClick={cancelEdit} disabled={updateInvoice.isPending}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  className="gap-1.5 bg-primary-600 hover:bg-primary-700 text-white"
                  onClick={saveEdit}
                  disabled={updateInvoice.isPending}
                >
                  <Save className="h-3.5 w-3.5" />
                  {updateInvoice.isPending ? 'Saving…' : 'Save Changes'}
                </Button>
              </div>
            </div>
          )}

          {/* ── 3-Way Match tab ───────────────────────────────────────────────── */}
          {activeTab === 'match' && (
            <div className="flex flex-col gap-4">
              {inv.status === 'unmatched' ? (
                <div className="rounded-xl border border-neutral-200 bg-white p-8 flex flex-col items-center text-center gap-3">
                  <FileText className="h-10 w-10 text-neutral-300" />
                  <p className="text-sm font-medium text-neutral-500">Not yet matched to a PO</p>
                  <p className="text-xs text-neutral-400">Go to the Unmatched Queue to link this invoice to a PO</p>
                  <Link to="/invoices"><Button variant="secondary" size="sm">Go to Unmatched Queue</Button></Link>
                </div>
              ) : (
                <>
                  {/* Summary row */}
                  <div className={cn('rounded-xl border p-5 flex items-center gap-4',
                    hasException ? 'border-danger-200 bg-danger-50' : 'border-success-200 bg-success-50')}>
                    {hasException
                      ? <AlertTriangle className="h-8 w-8 text-danger-600 flex-shrink-0" />
                      : <CheckCircle2 className="h-8 w-8 text-success-600 flex-shrink-0" />
                    }
                    <div>
                      <p className={cn('text-sm font-semibold', hasException ? 'text-danger-700' : 'text-success-700')}>
                        {hasException
                          ? `3-Way Match: Exception — Variance ${Number(inv.variance_pct ?? 0) > 0 ? '+' : ''}${Number(inv.variance_pct ?? 0).toFixed(1)}% (${inv.currency} ${formatAmount(Number(inv.variance ?? 0), inv.currency)})`
                          : '3-Way Match: ✓ Passed — Invoice within 5% tolerance of PO'
                        }
                      </p>
                      {inv.matched_at && (
                        <p className="text-xs text-neutral-500 mt-0.5">
                          Matched by {inv.matched_by_name ?? inv.matched_by} on {formatDateTime(inv.matched_at)}
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Match table */}
                  <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-neutral-200 bg-neutral-50">
                          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Document</th>
                          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Reference</th>
                          <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Amount</th>
                          <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Note</th>
                          <th className="px-4 py-3 text-center text-xs font-semibold uppercase tracking-wide text-neutral-500">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        <MatchRow
                          icon={<FileText className="h-4 w-4 text-neutral-400" />}
                          label="Purchase Order"
                          ref={inv.po_number}
                          href={`/po/${inv.po_id}`}
                          amount={inv.po_total}
                          currency={inv.currency}
                          note="Authorised PO total"
                          ok={true}
                        />
                        <MatchRow
                          icon={<TrendingUp className="h-4 w-4 text-neutral-400" />}
                          label="Goods / Service Receipt"
                          ref={inv.gr_number}
                          href={`/gr/${inv.gr_id}`}
                          amount={inv.gr_value}
                          currency={inv.currency}
                          note={inv.gr_value != null && inv.po_total != null && Number(inv.gr_value) < Number(inv.po_total)
                            ? 'Partial delivery' : 'Fully delivered'}
                          ok={inv.gr_value != null}
                        />
                        <MatchRow
                          icon={<FileText className="h-4 w-4 text-primary-500" />}
                          label="Invoice"
                          ref={inv.internal_ref}
                          href={`/invoices/${inv.id}`}
                          amount={inv.total_amount}
                          currency={inv.currency}
                          note={hasException
                            ? `${Number(inv.variance_pct ?? 0) > 0 ? '+' : ''}${Number(inv.variance_pct ?? 0).toFixed(1)}% vs PO`
                            : 'Within tolerance'}
                          ok={!hasException}
                        />
                      </tbody>
                    </table>
                    {/* Variance footer */}
                    <div className="px-4 py-3 border-t border-neutral-200 bg-neutral-50 flex justify-end">
                      <div className="flex items-center gap-6 text-sm">
                        <span className="text-neutral-500">Variance</span>
                        <span className={cn('font-mono font-semibold',
                          variancePctAbs > 5 ? 'text-danger-600' : variancePctAbs > 0 ? 'text-warning-600' : 'text-success-600')}>
                          {Number(inv.variance ?? 0) >= 0 ? '+' : ''}{formatAmount(Number(inv.variance ?? 0), inv.currency)}{' '}
                          ({Number(inv.variance_pct ?? 0) >= 0 ? '+' : ''}{Number(inv.variance_pct ?? 0).toFixed(2)}%)
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* PO allocations breakdown (multi-PO) */}
                  {inv.allocations && inv.allocations.length > 0 && (
                    <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
                      <div className="px-4 py-3 border-b border-neutral-200 bg-neutral-50">
                        <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-500">PO Allocations</h3>
                      </div>
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-neutral-200 bg-neutral-50">
                            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">PO</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">PO Line</th>
                            <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Allocated</th>
                            <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Variance</th>
                          </tr>
                        </thead>
                        <tbody>
                          {inv.allocations.map((a) => (
                            <tr key={a.id} className="border-b border-neutral-100 last:border-0">
                              <td className="px-4 py-3">
                                <Link to={`/po/${a.po_id}`} className="font-mono text-xs text-primary-600 hover:underline">
                                  {a.po_number ?? a.po_id.slice(0, 8)}
                                </Link>
                              </td>
                              <td className="px-4 py-3 text-xs text-neutral-500">{a.po_line_description ?? (a.po_line_id ? a.po_line_id.slice(0, 8) : 'PO header')}</td>
                              <td className="px-4 py-3 text-right font-mono text-xs">{formatAmount(Number(a.allocated_total), inv.currency)}</td>
                              <td className={cn('px-4 py-3 text-right font-mono text-xs',
                                Number(a.variance ?? 0) === 0 ? 'text-success-600' : 'text-danger-600')}>
                                {a.variance == null ? '-' : formatAmount(Number(a.variance), inv.currency)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {/* Exception resolution detail */}
                  {hasException && inv.exception_resolved_at && (
                    <div className="rounded-xl border border-neutral-200 bg-white p-5">
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">Exception Resolution</h3>
                      <div className="flex flex-col gap-2">
                        <MetaRow label="Resolution" value={inv.exception_resolution === 'accepted' ? 'Accepted with justification' : 'Credit note requested'} />
                        <MetaRow label="Resolved By" value={inv.exception_resolved_by_name ?? inv.exception_resolved_by ?? '—'} />
                        <MetaRow label="Resolved On" value={formatDateTime(inv.exception_resolved_at)} />
                        {inv.notes && <MetaRow label="Notes" value={inv.notes} />}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* ── History tab ───────────────────────────────────────────────────── */}
          {activeTab === 'history' && (
            <div className="rounded-xl border border-neutral-200 bg-white p-5">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-4">Activity Log</h3>
              <div className="flex flex-col gap-0">
                {[
                  inv.matched_at && inv.exception_resolved_at && {
                    date: inv.exception_resolved_at,
                    actor: inv.exception_resolved_by_name ?? inv.exception_resolved_by ?? 'System',
                    action: inv.exception_resolution === 'accepted' ? 'Exception accepted with justification' : 'Credit note requested',
                    color: 'bg-warning-500',
                  },
                  inv.matched_at && {
                    date: inv.matched_at,
                    actor: inv.matched_by_name ?? 'System',
                    action: `Matched to ${inv.po_number}${hasException ? ' — Exception raised' : ' — 3-way match passed'}`,
                    color: hasException ? 'bg-danger-600' : 'bg-success-600',
                  },
                  {
                    date: inv.uploaded_at,
                    actor: inv.uploaded_by_name ?? 'System',
                    action: `Invoice uploaded (${inv.file_name ?? 'manual entry'})`,
                    color: 'bg-primary-600',
                  },
                ]
                  .filter(Boolean)
                  .sort((a, b) => new Date((b as {date:string}).date).getTime() - new Date((a as {date:string}).date).getTime())
                  .map((entry, i, arr) => {
                    const e = entry as { date: string; actor: string; action: string; color: string }
                    return (
                      <div key={i} className="flex gap-3">
                        <div className="flex flex-col items-center">
                          <div className={cn('h-2.5 w-2.5 rounded-full mt-1 flex-shrink-0', e.color)} />
                          {i < arr.length - 1 && <div className="w-0.5 flex-1 bg-neutral-200 my-1" style={{ minHeight: 20 }} />}
                        </div>
                        <div className="pb-4">
                          <p className="text-sm text-neutral-900">{e.action}</p>
                          <p className="text-xs text-neutral-400 mt-0.5">{e.actor} · {formatDateTime(e.date)}</p>
                        </div>
                      </div>
                    )
                  })
                }
              </div>
            </div>
          )}
        </div>

        {/* Right sidebar */}
        <div className="w-56 flex-shrink-0 flex flex-col gap-4">
          <div className="rounded-xl border border-neutral-200 bg-white p-4">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">Quick Info</h3>
            <div className="flex flex-col gap-2 text-xs">
              <div className="flex justify-between">
                <span className="text-neutral-400">Total Due</span>
                <span className="font-mono font-semibold text-neutral-900">{formatAmount(inv.total_amount, inv.currency)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-neutral-400">Due Date</span>
                <span className="text-neutral-700">{formatDate(inv.due_date)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-neutral-400">Status</span>
                <InvoiceStatusBadge status={inv.status} />
              </div>
            </div>
          </div>

        </div>
      </div>

      {showAssignDialog && (
        <AssignMatchDialog
          invoiceId={inv.id}
          currentAssigneeName={inv.match_assignee_name}
          onClose={() => setShowAssignDialog(false)}
          onAssigned={() => setShowAssignDialog(false)}
        />
      )}
    </div>
  )
}

// ─── Invoice Attachments ──────────────────────────────────────────────────────

interface AttachmentMeta {
  id: string; file_name: string; content_type: string
  file_size_bytes: number; uploaded_at: string
}

function fmtBytes(b: number) {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / (1024 * 1024)).toFixed(1)} MB`
}

/** Fetches a single attachment file with auth → returns a revocable blob URL. */
function useAttachmentBlob(attachmentId: string, token: string | null) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!token) { setLoading(false); return }
    let url = ''
    setLoading(true)
    fetch(`${EXPENSE_BASE}/api/v1/invoice-attachments/${attachmentId}/file`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.ok ? r.blob() : Promise.reject(r.status))
      .then((blob) => { url = URL.createObjectURL(blob); setBlobUrl(url) })
      .catch(() => setBlobUrl(null))
      .finally(() => setLoading(false))

    return () => { if (url) URL.revokeObjectURL(url) }
  }, [attachmentId, token])

  return { blobUrl, loading }
}

function AttachmentItem({ att, token }: { att: AttachmentMeta; token: string | null }) {
  const { blobUrl } = useAttachmentBlob(att.id, token)

  const handleDownload = () => {
    if (!blobUrl) return
    const a = document.createElement('a')
    a.href = blobUrl; a.download = att.file_name; a.click()
  }

  const handleOpen = () => {
    if (blobUrl) window.open(blobUrl, '_blank')
  }

  return (
    <div className="flex flex-col gap-2">
      {/* File info + actions */}
      <div className="flex items-center gap-2 text-xs">
        <FileText className="h-4 w-4 text-neutral-400 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-neutral-700">{att.file_name}</p>
          <p className="text-neutral-400">{fmtBytes(att.file_size_bytes)}</p>
        </div>
        <button onClick={handleOpen} disabled={!blobUrl}
          className="flex items-center gap-1 rounded-lg border border-neutral-200 px-2 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50 disabled:opacity-40 transition-colors shrink-0">
          <ExternalLink className="h-3 w-3" /> Open
        </button>
        <button onClick={handleDownload} disabled={!blobUrl}
          className="flex items-center gap-1 rounded-lg bg-primary-50 border border-primary-200 px-2 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-100 disabled:opacity-40 transition-colors shrink-0">
          Download
        </button>
      </div>

    </div>
  )
}

function InvoiceAttachments({
  invoiceId, fileName, fileSize, canUpload = false,
}: {
  invoiceId: string
  fileName: string | null | undefined
  fileSize: string | null | undefined
  canUpload?: boolean
}) {
  const { token } = useAuthStore()
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const { data: attachments = [], isLoading } = useQuery<AttachmentMeta[]>({
    queryKey: ['invoice-attachments', invoiceId],
    queryFn: async () => {
      const res = await fetch(
        `${EXPENSE_BASE}/api/v1/invoice-attachments?invoice_id=${invoiceId}&invoice_source=epms`,
        { headers: { Authorization: `Bearer ${token}` } }
      )
      if (!res.ok) return []
      return res.json()
    },
    enabled: !!invoiceId,
  })

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setUploading(true)
    setUploadError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(
        `${EXPENSE_BASE}/api/v1/invoice-attachments?invoice_id=${invoiceId}&invoice_source=epms`,
        { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: form }
      )
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail ?? `Upload failed (${res.status})`)
      }
      await queryClient.invalidateQueries({ queryKey: ['invoice-attachments', invoiceId] })
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  const UploadButton = ({ label = 'Upload file' }: { label?: string }) => (
    canUpload ? (
      <>
        <input ref={fileInputRef} type="file" className="sr-only" onChange={handleFileChange} />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-200 px-2.5 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50 disabled:opacity-50 transition-colors"
        >
          <Upload className="h-3 w-3" />
          {uploading ? 'Uploading…' : label}
        </button>
      </>
    ) : null
  )

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400">
          Attachments {attachments.length > 0 && `(${attachments.length})`}
        </h3>
        {attachments.length > 0 && <UploadButton label="Add" />}
      </div>

      {uploadError && (
        <p className="mb-2 text-xs text-danger-600 flex items-center gap-1">
          <AlertTriangle className="h-3 w-3 shrink-0" />{uploadError}
        </p>
      )}

      {isLoading ? (
        <p className="text-xs text-neutral-400">Loading…</p>
      ) : attachments.length === 0 ? (
        <div className="flex flex-col gap-3">
          {fileName && (
            <div className="flex items-center gap-2 text-xs text-neutral-500">
              <FileText className="h-4 w-4 text-neutral-400 shrink-0" />
              <div className="min-w-0">
                <p className="truncate text-neutral-700">{fileName}</p>
                {fileSize && <p className="text-neutral-400">{fileSize}</p>}
              </div>
            </div>
          )}
          <UploadButton label={fileName ? 'Upload to file server' : 'Upload file'} />
          {!canUpload && (
            <p className="text-xs text-neutral-400">{fileName ? 'No file preview available' : 'No attachment'}</p>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {attachments.map((att) => (
            <AttachmentItem key={att.id} att={att} token={token} />
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Helper ───────────────────────────────────────────────────────────────────

function MetaRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="text-neutral-400 min-w-36 flex-shrink-0">{label}</span>
      <span className={cn('text-neutral-800 font-medium', mono && 'font-mono text-xs')}>{value}</span>
    </div>
  )
}
