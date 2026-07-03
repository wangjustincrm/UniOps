import { useState, useRef } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { financeApi } from '@/lib/api'
import {
  ArrowLeft, CheckCircle2, Clock, AlertTriangle, ChevronDown,
  CreditCard, FileText, ExternalLink, Landmark, Paperclip, X,
  RotateCcw, XCircle, Pencil, MessageSquare,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn, formatAmount, formatDate, formatDateTime } from '@/lib/utils'
import { usePa, usePaAction, usePaEvents, useConfirmSettlement, usePaWorkflowSteps } from '@/hooks/usePas'
import { usePo } from '@/hooks/usePos'
import { useInvoices } from '@/hooks/useInvoices'
import { useTasks } from '@/hooks/useTasks'
import { useAuthStore } from '@/stores/auth.store'
import { usePaAttachments, useDeletePaAttachment } from '@/hooks/usePaAttachments'
import { paAttachmentService } from '@/services/paAttachments'
import { PA_TYPE_LABEL, type PaStatus } from '@/services/pa'
import { DocumentChainTree } from '@/components/shared/DocumentChainTree'

// ─── Status config ────────────────────────────────────────────────────────────

const STATUS_CFG: Record<PaStatus, { label: string; variant: 'neutral' | 'warning' | 'info' | 'success' | 'danger' }> = {
  draft:     { label: 'Draft',     variant: 'neutral'  },
  submitted: { label: 'Submitted', variant: 'warning'  },
  in_review: { label: 'In Review', variant: 'info'     },
  approved:  { label: 'Approved',  variant: 'success'  },
  processed: { label: 'Processed', variant: 'neutral'  },
  returned:  { label: 'Returned',  variant: 'warning'  },
  cancelled: { label: 'Cancelled', variant: 'danger'   },
}

function PaStatusBadge({ status }: { status: PaStatus }) {
  // Tolerate unexpected/legacy status values so the page never crashes on a bad status.
  const c = STATUS_CFG[status] ?? { label: String(status ?? '—'), variant: 'neutral' as const }
  return <Badge variant={c.variant}>{c.label}</Badge>
}

// ─── Approval timeline ────────────────────────────────────────────────────────

function TimelineStep({
  label, actor, date, status, note,
}: {
  label: string; actor?: string; date?: string;
  status: 'done' | 'active' | 'pending';
  note?: string;
}) {
  const isAutoApproved = note?.includes('Auto-approved')
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <div className={cn('h-7 w-7 rounded-full flex items-center justify-center shrink-0', {
          'bg-success-600':  status === 'done' && !isAutoApproved,
          'bg-success-400':  status === 'done' && isAutoApproved,
          'bg-primary-600 ring-4 ring-primary-100': status === 'active',
          'bg-neutral-200':  status === 'pending',
        })}>
          {status === 'done'
            ? <CheckCircle2 className="h-4 w-4 text-white" />
            : status === 'active'
              ? <Clock className="h-3.5 w-3.5 text-white animate-pulse" />
              : <div className="h-2 w-2 rounded-full bg-neutral-400" />}
        </div>
        <div className="w-px flex-1 bg-neutral-200 mt-1" />
      </div>
      <div className="pb-5">
        <p className="text-sm font-semibold text-neutral-800">{actor ? `${label} — ${actor}` : label}</p>
        {date && <p className="text-xs text-neutral-400 mt-0.5">{formatDateTime(date)}</p>}
        {isAutoApproved && (
          <span className="inline-flex items-center mt-1 rounded-full bg-neutral-100 border border-neutral-200 px-2 py-0.5 text-[10px] font-medium text-neutral-500">
            Auto-approved
          </span>
        )}
        {note && !isAutoApproved && <p className="text-xs text-neutral-500 mt-1 italic">"{note}"</p>}
        {status === 'active' && !date && (
          <p className="text-xs text-primary-600 mt-0.5 font-medium">Awaiting action</p>
        )}
      </div>
    </div>
  )
}

// ─── Approval Action Modal ────────────────────────────────────────────────────

type ApprovalAction = 'approve' | 'return' | 'reject'

function ApprovalModal({ action, paNumber, onConfirm, onClose }: {
  action: ApprovalAction; paNumber: string
  onConfirm: (comment: string) => void; onClose: () => void
}) {
  const [comment, setComment] = useState('')
  const needsComment = action !== 'approve'
  const cfg = {
    approve: { title: 'Approve Payment Application', label: 'Approve', icon: <CheckCircle2 className="h-5 w-5 text-success-600" />, bg: 'bg-success-50', btn: 'bg-success-600 hover:bg-success-700 text-white', placeholder: 'Add an optional comment…', commentLabel: 'Comment (optional)' },
    reject:  { title: 'Reject Payment Application',  label: 'Reject',  icon: <XCircle className="h-5 w-5 text-danger-600" />,  bg: 'bg-danger-50',  btn: 'bg-danger-600 hover:bg-danger-700 text-white',   placeholder: 'Explain why this PA is being rejected…', commentLabel: 'Reason for rejection *' },
    return:  { title: 'Return for Revision',          label: 'Return',  icon: <RotateCcw className="h-5 w-5 text-warning-600" />, bg: 'bg-warning-50', btn: 'bg-warning-500 hover:bg-warning-600 text-white', placeholder: 'Describe what needs to be revised…', commentLabel: 'Revision instructions *' },
  }[action]
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className={cn('flex h-9 w-9 items-center justify-center rounded-full', cfg.bg)}>{cfg.icon}</div>
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">{cfg.title}</h2>
              <p className="text-xs text-neutral-500">{paNumber}</p>
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100"><X className="h-4 w-4" /></button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">{cfg.commentLabel}</label>
            <div className="relative">
              <MessageSquare className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-neutral-400" />
              <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)}
                placeholder={cfg.placeholder} autoFocus
                className="w-full resize-none rounded-lg border border-neutral-300 bg-white pl-8 pr-3 pt-2 pb-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-neutral-200 px-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50">Cancel</button>
            <button disabled={needsComment && !comment.trim()} onClick={() => onConfirm(comment)}
              className={cn('inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed', cfg.btn)}>
              {cfg.icon}{cfg.label}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}

// ─── Process (record payment) modal ───────────────────────────────────────────

interface FundingAccount {
  id: string; name: string; bank_name: string; kind: string
  account_masked: string | null; currency: string; is_active: boolean
}

function ProcessModal({ paNumber, currency, amount, busy, onConfirm, onClose }: {
  paNumber: string; currency: string; amount: number; busy: boolean
  onConfirm: (bankAccountId: string) => void; onClose: () => void
}) {
  const [bankId, setBankId] = useState('')
  const { data: accounts = [], isLoading } = useQuery({
    queryKey: ['finance-bank-accounts'],
    queryFn: () => financeApi.get<FundingAccount[]>('/bank/accounts'),
  })
  const options = accounts.filter((a) => a.is_active && a.currency === currency)

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-50">
              <CreditCard className="h-5 w-5 text-primary-600" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">Mark as Processed</h2>
              <p className="text-xs text-neutral-500">{paNumber} · {formatAmount(amount)} {currency}</p>
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100"><X className="h-4 w-4" /></button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Pay from (bank account or credit card) *</label>
            <select value={bankId} onChange={(e) => setBankId(e.target.value)}
              className="w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600">
              <option value="">Select a {currency} payment source…</option>
              {options.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.kind === 'credit_card' ? '💳' : '🏦'} {a.bank_name} — {a.name}{a.account_masked ? ` · …${a.account_masked}` : ''}
                </option>
              ))}
            </select>
            {!isLoading && options.length === 0 && (
              <p className="text-xs text-amber-600">No active {currency} accounts. Add one under Finance → Bank &amp; Cards.</p>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-neutral-200 px-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50">Cancel</button>
            <button disabled={!bankId || busy} onClick={() => onConfirm(bankId)}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-40 disabled:cursor-not-allowed">
              <CheckCircle2 className="h-4 w-4" />{busy ? 'Processing…' : 'Confirm Payment'}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function PaDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { data: pa, isLoading } = usePa(id ?? '')
  const { data: events } = usePaEvents(id ?? '')
  const paAction = usePaAction(id ?? '')
  const confirmSettlement = useConfirmSettlement(id ?? '')
  const { data: po } = usePo(pa?.po_id ?? '')
  const { data: invoicesData } = useInvoices(pa?.invoice_ids.length ? { po_id: pa.po_id } : undefined)
  // The PO can carry multiple PAs/invoices, so narrow to the invoices actually
  // linked to THIS PA (pa.invoice_ids) instead of showing every PO invoice.
  const linkedInvoices = (invoicesData?.items ?? []).filter((inv) => pa?.invoice_ids.includes(inv.id))
  const { user } = useAuthStore()

  const [activeTab, setActiveTab] = useState<'details' | 'attachments' | 'history'>('details')
  const [pendingAction, setPendingAction] = useState<ApprovalAction | null>(null)
  const [processOpen, setProcessOpen] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const moreRef = useRef<HTMLDivElement>(null)
  const { data: attachments = [] } = usePaAttachments(id ?? '')
  const deleteAttachment = useDeletePaAttachment(id ?? '')

  const { data: workflowSteps } = usePaWorkflowSteps(id ?? '')
  // Must stay above the early returns below — calling it later would make the
  // hook count differ between the loading and loaded renders (Rules of Hooks).
  const { data: myTasks } = useTasks({ is_completed: false })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <p className="text-neutral-400 text-sm">Loading…</p>
      </div>
    )
  }

  if (!pa) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-neutral-500">Payment Application not found.</p>
        <Button variant="secondary" className="mt-4" onClick={() => navigate('/pa')}>Back to PA List</Button>
      </div>
    )
  }

  const wfNodes = workflowSteps ?? []
  const stepIdx = pa.approval_step_idx ?? 0

  // Approval gating is driven solely by the approval engine's task routing — no
  // client-side workflow/role fallback. The engine assigns each step to exactly the
  // resolved approver (e.g. gm_or_opm → GM or OPM by the creator's department), and
  // grants system_admin every task. Showing the Approve button only when the current
  // user holds an active approve_pa task for THIS PA keeps the button consistent with
  // the inbox and follows whatever workflow is configured in the engine.
  const hasApproveTask = !!(myTasks?.items ?? []).some(
    (t) => t.document_id === pa.id && t.type === 'approve_pa'
  )
  const canApprove  =
    (['submitted', 'in_review'].includes(pa.status)) &&
    hasApproveTask
  const canProcess  = user?.role === 'ap_clerk'
  const canSettle   = user?.role === 'requester' || user?.role === 'system_admin'

  const handleConfirm = (action: ApprovalAction, comment: string) => {
    paAction.mutate(
      { action, comment: comment || undefined },
      { onSuccess: () => setPendingAction(null) }
    )
  }
  const handleProcessConfirm = (bankAccountId: string) =>
    paAction.mutate(
      { action: 'process', bank_account_id: bankAccountId },
      { onSuccess: () => setProcessOpen(false) },
    )

  // Build timeline steps
  // Index approval events by step_idx for quick lookup
  const approveEventByStep = (events ?? [])
    .filter((e) => e.action === 'approve')
    .reduce<Record<number, typeof events[0]>>((acc, e) => {
      if (!(e.step_idx in acc)) acc[e.step_idx] = e
      return acc
    }, {})
  const submitEvent = (events ?? []).find((e) => e.action === 'submit')

  const steps = [
    {
      label: 'PA Created & Submitted',
      // Prefer the document's creator (always set, incl. imported data); fall back
      // to the submit event's actor for records created before created_by tracking.
      actor: pa.created_by_name ?? submitEvent?.actor_name ?? undefined,
      date: pa.created_at,
      status: 'done' as const,
      note: undefined as string | undefined,
    },
    ...wfNodes.map((node, i) => {
      const isDone = ['approved', 'processed'].includes(pa.status) || i < stepIdx
      const isActive = !isDone && i === stepIdx && ['submitted', 'in_review'].includes(pa.status)
      const evt = approveEventByStep[i]
      return {
        label: node.label,
        actor: evt?.actor_name ?? undefined,
        date: evt?.created_at ?? undefined,
        note: evt?.comment?.includes('Auto-approved') ? 'Auto-approved (same approver)' : undefined,
        status: isDone ? 'done' as const : isActive ? 'active' as const : 'pending' as const,
      }
    }),
    {
      label: 'Payment Processed',
      actor: undefined as string | undefined,
      date: pa.status === 'processed' ? pa.updated_at : undefined,
      note: undefined as string | undefined,
      status: pa.status === 'processed' ? 'done' as const : pa.status === 'approved' ? 'active' as const : 'pending' as const,
    },
  ]

  if (pa.pa_type === 'prepayment') {
    steps.push({
      label: 'Prepayment Settlement',
      actor: pa.settled_by_name ?? pa.settled_by,
      date: pa.settled_at,
      note: undefined,
      status: pa.settled_at ? 'done' as const : pa.status === 'processed' ? 'active' as const : 'pending' as const,
    })
  }

  return (
    <div className={cn('flex flex-col gap-6 p-6', canApprove && 'pb-16')}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/pa')} className="p-2 rounded-lg hover:bg-neutral-100 text-neutral-500">
            <ArrowLeft className="h-5 w-5" />
          </button>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-bold text-neutral-900">{pa.pa_number}</h1>
              <PaStatusBadge status={pa.status} />
              {pa.pa_type === 'prepayment' && (
                <span className="inline-flex items-center rounded-full bg-info-50 border border-info-200 px-2 py-0.5 text-[10px] font-semibold text-info-700 uppercase">Prepayment</span>
              )}
            </div>
            <p className="text-sm text-neutral-500 mt-0.5">
              {pa.title}
              <span className="ml-2 text-neutral-400">·</span>
              <span className="ml-2 text-neutral-400">{linkedInvoices.length} invoice{linkedInvoices.length !== 1 ? 's' : ''}</span>
            </p>
          </div>
        </div>

        <div className="flex gap-2">
          {(['draft', 'returned'] as PaStatus[]).includes(pa.status) && (
            <Button variant="secondary" onClick={() => navigate(`/pa/${pa.id}/edit`)} className="gap-2">
              <Pencil className="h-4 w-4" />
              Edit
            </Button>
          )}
          {/* Approval actions moved to fixed bottom bar */}
          {pa.status === 'approved' && canProcess && (
            <Button onClick={() => setProcessOpen(true)} disabled={paAction.isPending} className="gap-2">
              <Landmark className="h-4 w-4" />
              {paAction.isPending ? 'Processing…' : 'Mark as Processed'}
            </Button>
          )}
          {pa.status === 'processed' && pa.pa_type === 'prepayment' && pa.settlement_status === 'pending' && canSettle && (
            <Button onClick={() => navigate(`/pa/new?settleFrom=${pa.id}`)} variant="secondary" className="gap-2">
              <FileText className="h-4 w-4" />
              Settle Prepayment
            </Button>
          )}
          {pa.pa_type === 'settlement' && pa.status === 'submitted' && Number(pa.payment_amount) === 0 && (canProcess || user?.role === 'system_admin') && (
            <Button
              onClick={() => confirmSettlement.mutate()}
              disabled={confirmSettlement.isPending}
              className="gap-2"
            >
              <CheckCircle2 className="h-4 w-4" />
              {confirmSettlement.isPending ? 'Confirming…' : 'Confirm Settlement'}
            </Button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Main content */}
        <div className="col-span-2 flex flex-col gap-5">
          {/* Tabs */}
          <div className="flex gap-1 border-b border-neutral-200">
            {(['details', 'attachments', 'history'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setActiveTab(t)}
                className={cn(
                  'px-4 py-2 text-sm font-medium capitalize transition-colors',
                  activeTab === t
                    ? 'border-b-2 border-primary-600 text-primary-700'
                    : 'text-neutral-500 hover:text-neutral-700'
                )}
              >
                {t}
              </button>
            ))}
          </div>

          {activeTab === 'details' && (
            <>
              {/* PA Details */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
                <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-4">Payment Details</h3>
                <div className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm">
                  {[
                    { label: 'PA Number',    value: pa.pa_number },
                    { label: 'Vendor',       value: pa.vendor_name },
                    { label: 'PA Type',      value: PA_TYPE_LABEL[pa.pa_type] ?? 'Regular Payment' },
                    { label: 'Created',      value: formatDate(pa.created_at) },
                    ...(pa.pa_type === 'prepayment' && pa.prepayment_pct ? [
                      { label: 'Prepayment %', value: `${pa.prepayment_pct}%` },
                      { label: 'Expected Settlement', value: pa.expected_settlement_date ? formatDate(pa.expected_settlement_date) : '—' },
                    ] : []),
                  ].map(({ label, value }) => (
                    <div key={label}>
                      <p className="text-xs text-neutral-500">{label}</p>
                      <p className="font-medium text-neutral-900 mt-0.5">{value}</p>
                    </div>
                  ))}
                </div>

                {pa.notes && (
                  <div className="mt-4 pt-4 border-t border-neutral-100">
                    <p className="text-xs text-neutral-500 mb-1">Notes</p>
                    <p className="text-sm text-neutral-700">{pa.notes}</p>
                  </div>
                )}
              </div>

              {/* Charge Breakdown */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
                <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-4">Charge Breakdown</h3>
                <div className="flex flex-col gap-2 text-sm">
                  {[
                    { label: 'Pre-tax Amount',    value: formatAmount(pa.subtotal, pa.currency) },
                    { label: 'Tax',               value: formatAmount(pa.tax_amount, pa.currency) },
                  ].map(({ label, value }) => (
                    <div key={label} className="flex items-center justify-between py-1 border-b border-neutral-100 last:border-0">
                      <span className="text-neutral-600">{label}</span>
                      <span className="font-mono text-neutral-800">{value}</span>
                    </div>
                  ))}
                  <div className="flex items-center justify-between pt-2 mt-1 border-t-2 border-neutral-200">
                    <span className="font-semibold text-neutral-800">Total Payment</span>
                    <span className="font-mono font-bold text-lg text-neutral-900">{formatAmount(pa.payment_amount, pa.currency)}</span>
                  </div>
                </div>
              </div>

              {/* Payment amount box */}
              <div className="rounded-xl border-2 border-primary-200 bg-primary-50 p-5 flex items-center justify-between">
                <div>
                  <p className="text-xs text-primary-600 font-semibold uppercase tracking-wide">Payment Amount</p>
                  <p className="text-3xl font-bold text-primary-800 font-mono mt-1">{formatAmount(pa.payment_amount, pa.currency)}</p>
                </div>
                <CreditCard className="h-10 w-10 text-primary-300" />
              </div>

              {/* Prepayment settlement status */}
              {pa.pa_type === 'prepayment' && (
                <div className={cn(
                  'rounded-xl border p-5 shadow-sm',
                  pa.settlement_status === 'settled'  ? 'border-success-200 bg-success-50' :
                  pa.settlement_status === 'disputed' ? 'border-warning-200 bg-warning-50' :
                  'border-neutral-200 bg-white'
                )}>
                  <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-3">Settlement Status</h3>
                  {pa.settlement_status === 'pending' && pa.status !== 'processed' && (
                    <p className="text-sm text-neutral-500">Settlement required after payment is processed and final invoice received.</p>
                  )}
                  {pa.settlement_status === 'pending' && pa.status === 'processed' && (
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 text-warning-700">
                        <AlertTriangle className="h-4 w-4" />
                        <span className="text-sm font-medium">Settlement pending</span>
                      </div>
                      {canSettle && (
                        <Button variant="secondary" onClick={() => navigate(`/pa/new?settleFrom=${pa.id}`)}>
                          Settle Now →
                        </Button>
                      )}
                    </div>
                  )}
                  {(pa.settlement_status === 'settled' || pa.settlement_status === 'disputed') && (
                    <div className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm">
                      {[
                        { label: 'Status',    value: pa.settlement_status === 'settled' ? '✅ Settled' : '⚠ Disputed' },
                        { label: 'Settled By', value: pa.settled_by_name ?? pa.settled_by ?? '—' },
                        { label: 'Settled At', value: pa.settled_at ? formatDateTime(pa.settled_at) : '—' },
                        { label: 'Variance',   value: pa.settlement_variance !== undefined ? formatAmount(pa.settlement_variance, pa.currency) : '—' },
                      ].map(({ label, value }) => (
                        <div key={label}>
                          <p className="text-xs text-neutral-500">{label}</p>
                          <p className="font-medium text-neutral-900 mt-0.5">{value}</p>
                        </div>
                      ))}
                      {pa.settlement_note && (
                        <div className="col-span-2">
                          <p className="text-xs text-neutral-500">Settlement Note</p>
                          <p className="text-sm text-neutral-700 mt-0.5">{pa.settlement_note}</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* PA Line Items */}
              <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
                <div className="px-5 py-3 border-b border-neutral-200 bg-neutral-50">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400">
                    PA Line Items ({pa.line_items.length})
                  </h3>
                </div>
                {pa.line_items.length === 0 ? (
                  <div className="px-5 py-6 text-center text-xs text-neutral-400 italic">
                    No line items recorded for this PA
                  </div>
                ) : (
                  <>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-neutral-200 bg-neutral-50">
                            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 w-8">#</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Description</th>
                            <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500 w-20">Qty</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 w-16">Unit</th>
                            <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500 w-28">Unit Price</th>
                            <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500 w-28">Line Total</th>
                          </tr>
                        </thead>
                        <tbody>
                          {pa.line_items.map((line, idx) => (
                            <tr key={line.id} className={cn('border-b border-neutral-100 last:border-0', idx % 2 === 1 && 'bg-neutral-50/50')}>
                              <td className="px-4 py-3 text-center text-neutral-400 text-xs">{idx + 1}</td>
                              <td className="px-4 py-3">
                                <div className="text-neutral-800">{line.description}</div>
                                {line.notes && <div className="text-xs text-neutral-400 mt-0.5">{line.notes}</div>}
                              </td>
                              <td className="px-4 py-3 text-right font-mono text-xs text-neutral-700">{line.qty}</td>
                              <td className="px-4 py-3 text-xs text-neutral-500">{line.unit}</td>
                              <td className="px-4 py-3 text-right font-mono text-xs text-neutral-700">{formatAmount(line.unit_price, pa.currency)}</td>
                              <td className="px-4 py-3 text-right font-mono text-xs font-semibold text-neutral-900">{formatAmount(line.line_total, pa.currency)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="px-4 py-3 border-t border-neutral-200 flex justify-end">
                      <div className="flex items-center gap-4 text-sm">
                        <span className="text-neutral-500">Lines Total</span>
                        <span className="font-mono font-semibold text-neutral-900">
                          {formatAmount(pa.line_items.reduce((s, l) => s + Number(l.line_total), 0), pa.currency)}
                        </span>
                      </div>
                    </div>
                  </>
                )}
              </div>

              {/* Linked documents */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
                <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-4">Linked Documents</h3>
                <div className="flex flex-col gap-2">
                  {/* PO */}
                  <div className="flex items-center justify-between rounded-lg border border-neutral-100 bg-neutral-50 px-4 py-2.5">
                    <span className="text-xs font-medium text-neutral-600">PO</span>
                    {po ? (
                      <Link to={`/po/${pa.po_id}`} className="flex items-center gap-1 font-mono text-xs text-primary-700 hover:underline">
                        {pa.po_number} <ExternalLink className="h-3 w-3" />
                      </Link>
                    ) : (
                      <span className="font-mono text-xs text-neutral-400">{pa.po_number}</span>
                    )}
                  </div>

                  {/* Invoices */}
                  {linkedInvoices.length > 0 ? linkedInvoices.map((inv) => (
                    <div key={inv.id} className="flex items-center justify-between rounded-lg border border-neutral-100 bg-neutral-50 px-4 py-2.5">
                      <span className="text-xs font-medium text-neutral-600">Invoice</span>
                      <Link to={`/invoices/${inv.id}`} className="flex items-center gap-1 font-mono text-xs text-primary-700 hover:underline">
                        {inv.internal_ref} · {formatAmount(inv.total_amount, inv.currency)} <ExternalLink className="h-3 w-3" />
                      </Link>
                    </div>
                  )) : (
                    <div className="flex items-center justify-between rounded-lg border border-neutral-100 bg-neutral-50 px-4 py-2.5">
                      <span className="text-xs font-medium text-neutral-600">Invoice</span>
                      <span className="text-xs text-neutral-400 italic">No invoices linked</span>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}

          {activeTab === 'attachments' && (
            <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
              <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-4">Attachments</h3>
              {attachments.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-center">
                  <Paperclip className="h-8 w-8 text-neutral-300 mb-3" />
                  <p className="text-sm text-neutral-400">No attachments uploaded</p>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  {attachments.map((att) => (
                    <div key={att.id} className="flex items-center gap-3 rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3">
                      <Paperclip className="h-4 w-4 shrink-0 text-neutral-400" />
                      <span className="flex-1 truncate text-sm text-neutral-700">{att.filename}</span>
                      <span className="text-xs text-neutral-400">{(att.file_size / 1024 / 1024).toFixed(1)} MB</span>
                      <button
                        type="button"
                        onClick={() => paAttachmentService.download(id!, att.id, att.filename)}
                        className="text-xs text-primary-600 hover:underline"
                      >
                        Download
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteAttachment.mutate(att.id)}
                        className="text-neutral-300 hover:text-danger-500"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {activeTab === 'history' && (
            <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
              <h3 className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-4">Activity History</h3>
              <div className="flex flex-col text-sm">
                {events && events.length > 0 ? (
                  [...events].reverse().map((evt, i) => (
                    <div key={i} className="flex gap-4 pb-4 border-b border-neutral-100 last:border-0 last:pb-0">
                      <div className="text-xs text-neutral-400 w-32 shrink-0 mt-0.5">{formatDateTime(evt.created_at)}</div>
                      <div>
                        <p className="text-sm font-medium text-neutral-800 capitalize">{evt.action.replace(/_/g, ' ')}</p>
                        <p className="text-xs text-neutral-500 mt-0.5">
                          by {evt.actor_role.replace(/_/g, ' ')}
                          {evt.actor_name && <span className="text-neutral-400"> — {evt.actor_name}</span>}
                        </p>
                        {evt.comment && <p className="text-xs text-neutral-400 mt-0.5 italic">"{evt.comment}"</p>}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="flex gap-4 pb-4">
                    <div className="text-xs text-neutral-400 w-32 shrink-0 mt-0.5">{formatDateTime(pa.created_at)}</div>
                    <div>
                      <p className="text-sm font-medium text-neutral-800">PA created</p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Right sidebar — timeline + document chain */}
        <div className="flex flex-col gap-4">
          <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm sticky top-6">
            <p className="text-xs font-semibold text-neutral-500 uppercase tracking-wide mb-4">Approval Progress</p>
            {steps.map((step, i) => (
              <TimelineStep key={i} {...step} />
            ))}
            <DocumentChainTree currentType="pa" id={pa.id} />
          </div>
        </div>
      </div>

      {/* Fixed bottom approver action bar */}
      {canApprove && (
        <div className="fixed bottom-0 left-60 right-0 z-10 bg-white border-t border-neutral-200 px-6 py-3 flex items-center justify-between shadow-[0_-1px_8px_rgba(0,0,0,0.06)] max-md:left-0">
          <p className="text-sm text-neutral-500">
            Reviewing <span className="font-medium text-neutral-900">{pa.pa_number}</span>
          </p>
          <div className="flex items-center gap-2">
            <div className="relative" ref={moreRef}>
              <button
                onClick={() => setMoreOpen((v) => !v)}
                className="inline-flex h-9 items-center gap-1 rounded-lg border border-neutral-200 bg-white px-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors"
              >
                More <ChevronDown className="h-3.5 w-3.5" />
              </button>
              {moreOpen && (
                <div className="absolute right-0 bottom-full mb-1 w-44 rounded-lg border border-neutral-200 bg-white py-1 shadow-lg z-20">
                  <button
                    onClick={() => { setPendingAction('return'); setMoreOpen(false) }}
                    className="flex w-full items-center gap-2 px-3 py-2 text-sm text-warning-700 hover:bg-warning-50"
                  >
                    <RotateCcw className="h-4 w-4" /> Return for Revision
                  </button>
                  <button
                    onClick={() => { setPendingAction('reject'); setMoreOpen(false) }}
                    className="flex w-full items-center gap-2 px-3 py-2 text-sm text-danger-600 hover:bg-danger-50"
                  >
                    <XCircle className="h-4 w-4" /> Reject
                  </button>
                </div>
              )}
            </div>
            <button
              onClick={() => setPendingAction('approve')}
              className="inline-flex items-center gap-2 h-9 px-5 rounded-lg bg-success-600 text-white text-sm font-semibold hover:bg-success-700 transition-colors"
            >
              <CheckCircle2 className="h-4 w-4" /> Approve
            </button>
          </div>
        </div>
      )}

      {/* Approval modal */}
      {pendingAction && (
        <ApprovalModal
          action={pendingAction}
          paNumber={pa.pa_number}
          onConfirm={(comment) => handleConfirm(pendingAction, comment)}
          onClose={() => setPendingAction(null)}
        />
      )}
      {processOpen && (
        <ProcessModal
          paNumber={pa.pa_number}
          currency={pa.currency}
          amount={Number(pa.payment_amount)}
          busy={paAction.isPending}
          onConfirm={handleProcessConfirm}
          onClose={() => setProcessOpen(false)}
        />
      )}
    </div>
  )
}
