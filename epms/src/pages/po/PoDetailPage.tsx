import { useState, useRef } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, CheckCircle2, XCircle, RotateCcw, Pencil, ChevronDown,
  MessageSquare, X, Send, ExternalLink, FileText, Mail, ShoppingCart, Warehouse, Globe, Loader2,
  CreditCard,
} from 'lucide-react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Badge, StatusBadge } from '@/components/ui/badge'
import { ApprovalTimeline } from '@/components/pr/ApprovalTimeline'
import { formatAmount, formatDate, cn } from '@/lib/utils'
import type { ApprovalStep, DocumentStatus, WorkflowNodeDef } from '@/types'
import { useAuthStore } from '@/stores/auth.store'
import { DocumentChainTree } from '@/components/shared/DocumentChainTree'
import { generatePoHtml } from '@/lib/po-document'
import { buildEmailVars, renderTemplate } from '@/lib/email-template'
import { useConfig, useRolePermissions } from '@/hooks/useConfig'
import { downloadPdf } from '@/lib/pdf-utils'
import { usePo, usePoAction, usePoAttachments, useUploadPoAttachment, useDeletePoAttachment, usePoEvents, usePlaceOrder, usePoWorkflowSteps, useRegeneratePoPdf } from '@/hooks/usePos'
import { AttachmentsEditor } from '@/components/shared/AttachmentsEditor'
import { useGrs } from '@/hooks/useGrs'
import { useInvoices } from '@/hooks/useInvoices'
import { useTasks } from '@/hooks/useTasks'
import type { ApiPo, ApiPoLineItem } from '@/services/po'
import type { ApiEvent } from '@/services/pr'
import type { GrStatus } from '@/services/gr'
import { vendorService } from '@/services/vendors'

const TYPE_LABELS: Record<number, string> = {
  1: 'Raw Mat./Pack.',
  2: 'Consumables',
  3: 'Spare Parts',
  4: 'Service',
  5: 'Fixed Asset',
  6: 'Project',
}

const TAX_LABELS: Record<number, string> = {
  0: '0% — No Tax / Exempt',
  0.05: '5% — GST',
  0.13: '13% — HST',
  0.15: '15% — HST',
}

const APPROVABLE_STATUSES = ['submitted', 'in_review']

const GR_STATUS_LABELS: Record<GrStatus, string> = {
  pending_ack:        'Pending Ack.',
  collection_pending: 'Collection Pending',
  collected:          'Collected',
  confirmed:          'Confirmed',
  discrepancy:        'Discrepancy',
  rejected:           'Rejected',
  cancelled:          'Cancelled',
}

const GR_STATUS_DOC: Record<GrStatus, DocumentStatus> = {
  pending_ack:        'submitted',
  collection_pending: 'submitted',
  collected:          'collected',
  confirmed:          'confirmed',
  discrepancy:        'returned',
  rejected:           'returned',
  cancelled:          'cancelled',
}

// ─── Approval Timeline ─────────────────────────────────────────────────────────

function buildWorkflowSteps(nodes: WorkflowNodeDef[], status: string, stepIdx: number, events: ApiEvent[] = []): ApprovalStep[] {
  // Index approve events by step_idx so each workflow node shows who acted.
  const approveEventByStep = events
    .filter((e) => e.action === 'approve')
    .reduce<Record<number, ApiEvent>>((acc, e) => {
      if (!(e.step_idx in acc)) acc[e.step_idx] = e
      return acc
    }, {})

  const created: ApprovalStep = {
    id: 'created',
    role: 'Procurement Officer',
    // Per product decision the Procurement Officer creation step shows the role
    // only, no name (imported POs carry no reliable creator). Later workflow
    // steps still show their actor.
    actorName: undefined,
    status: 'completed',
    action: 'Created',
    channel: 'Web',
  }
  const approvalNodes: ApprovalStep[] = nodes.map((node, i) => {
    let s: ApprovalStep['status']
    if (['approved', 'issued', 'partially_received', 'fully_received', 'closed', 'nc_milk'].includes(status)) {
      s = 'completed'
    } else if (status === 'cancelled') {
      s = i < stepIdx ? 'completed' : i === stepIdx ? 'skipped' : 'pending'
    } else if (status === 'submitted') {
      s = i < stepIdx ? 'completed' : 'pending'
    } else {
      s = i < stepIdx ? 'completed' : i === stepIdx ? 'current' : 'pending'
    }
    const evt = approveEventByStep[i]
    if (evt?.comment?.includes('Auto-skipped')) {
      s = 'skipped'
    }
    return { id: node.id, role: node.label, actorName: evt?.comment?.includes('Auto-skipped') ? undefined : evt?.actor_name ?? undefined, status: s }
  })
  return [created, ...approvalNodes]
}

// ─── Place Order Modal ─────────────────────────────────────────────────────────

type PlaceOrderStep = 'choose' | 'email_compose' | 'online_confirm' | 'done'
type PlaceOrderDoneMethod = 'email' | 'online'

interface PlaceOrderModalProps {
  po: ApiPo
  onClose: () => void
}

function PlaceOrderModal({ po, onClose }: PlaceOrderModalProps) {
  const { data: config } = useConfig()
  const { user } = useAuthStore()
  const placeOrder = usePlaceOrder(po.id)
  const [step, setStep] = useState<PlaceOrderStep>('choose')
  const [doneMethod, setDoneMethod] = useState<PlaceOrderDoneMethod>('online')
  const [reference, setReference] = useState('')
  const [error, setError] = useState('')

  // Fetch vendor contact email
  const { data: vendor } = useQuery({
    queryKey: ['vendors', po.vendor_id],
    queryFn: () => vendorService.get(po.vendor_id),
    enabled: Boolean(po.vendor_id),
  })
  const vendorEmail = vendor?.contact_email ?? ''

  // Email composer state — pre-filled from template (config may still be loading)
  const emailVars = config
    ? buildEmailVars(po, config, user?.name ?? 'Procurement Officer', vendorEmail)
    : null
  const [emailTo, setEmailTo] = useState('')
  const [emailCc, setEmailCc] = useState('')
  const [emailSubject, setEmailSubject] = useState('')
  const [emailBody, setEmailBody] = useState('')

  // Pre-fill email fields when vendor loads or user opens email step
  const initEmail = () => {
    setEmailTo(vendorEmail)
    setEmailSubject(renderTemplate(config?.po_email_subject ?? '', emailVars ?? {} as any))
    setEmailBody(renderTemplate(config?.po_email_body ?? '', emailVars ?? {} as any))
    setStep('email_compose')
  }

  const handleOnlineConfirm = () => {
    setError('')
    placeOrder.mutate(
      { method: 'online', reference: reference || undefined },
      {
        onSuccess: () => { setDoneMethod('online'); setStep('done') },
        onError: (e) => setError(e instanceof Error ? e.message : 'Failed to place order'),
      }
    )
  }

  const handleSendEmail = () => {
    if (!emailTo.trim()) { setError('Recipient email is required'); return }
    setError('')
    placeOrder.mutate(
      { method: 'email', to: emailTo, cc: emailCc.trim() || undefined, subject: emailSubject, body: emailBody },
      {
        onSuccess: () => { setDoneMethod('email'); setStep('done') },
        onError: (e) => setError(e instanceof Error ? e.message : 'Failed to place order'),
      }
    )
  }

  const inputCls = 'w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-2xl rounded-2xl bg-white shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4 shrink-0">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-50">
              <ShoppingCart className="h-5 w-5 text-primary-600" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">Place Order</h2>
              <p className="text-xs text-neutral-500">{po.number} · {po.vendor_name}</p>
            </div>
          </div>
          {step !== 'done' && (
            <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100">
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 px-6 py-5">

          {/* Step: Choose method */}
          {step === 'choose' && (
            <div className="flex flex-col gap-4">
              <p className="text-sm text-neutral-600">How would you like to place this order?</p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {/* Option A — Email Supplier */}
                <button
                  onClick={initEmail}
                  className="flex flex-col items-start gap-3 rounded-xl border-2 border-neutral-200 bg-white p-5 text-left transition-colors hover:border-primary-400 hover:bg-primary-50 group"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary-50 group-hover:bg-primary-100">
                    <Mail className="h-5 w-5 text-primary-600" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-neutral-900">Place Order via Email</p>
                    <p className="mt-1 text-xs text-neutral-500">
                      Send a PO email to the vendor using the configured template. PO PDF is included.
                    </p>
                    {vendorEmail && (
                      <p className="mt-1.5 text-xs font-mono text-neutral-400 truncate">{vendorEmail}</p>
                    )}
                    {!vendorEmail && (
                      <p className="mt-1.5 text-xs text-warning-600">No vendor email on file — add one in Vendor Master first.</p>
                    )}
                  </div>
                </button>

                {/* Option B — Place Order Online */}
                <button
                  onClick={() => setStep('online_confirm')}
                  className="flex flex-col items-start gap-3 rounded-xl border-2 border-neutral-200 bg-white p-5 text-left transition-colors hover:border-primary-400 hover:bg-primary-50 group"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-success-50 group-hover:bg-success-100">
                    <Globe className="h-5 w-5 text-success-600" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-neutral-900">Place Order Online</p>
                    <p className="mt-1 text-xs text-neutral-500">
                      You placed the order via the vendor's online portal. Confirm here to mark the PO as Issued.
                    </p>
                  </div>
                </button>
              </div>
            </div>
          )}

          {/* Step: Email Compose */}
          {step === 'email_compose' && (
            <div className="flex flex-col gap-4">
              <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4 flex flex-col gap-3">
                <div className="flex items-start gap-3 text-sm">
                  <span className="text-neutral-500 w-16 shrink-0 pt-2">To:</span>
                  <input
                    value={emailTo}
                    onChange={(e) => setEmailTo(e.target.value)}
                    className={inputCls}
                    placeholder="vendor@example.com"
                    type="email"
                  />
                </div>
                <div className="flex items-start gap-3 text-sm border-t border-neutral-200 pt-3">
                  <span className="text-neutral-500 w-16 shrink-0 pt-2">CC:</span>
                  <input
                    value={emailCc}
                    onChange={(e) => setEmailCc(e.target.value)}
                    className={inputCls}
                    placeholder="cc@example.com"
                    type="email"
                  />
                </div>
                <div className="flex items-start gap-3 text-sm border-t border-neutral-200 pt-3">
                  <span className="text-neutral-500 w-16 shrink-0 pt-2">Subject:</span>
                  <input
                    value={emailSubject}
                    onChange={(e) => setEmailSubject(e.target.value)}
                    className={inputCls}
                    placeholder="Purchase Order…"
                  />
                </div>
              </div>
              <div className="rounded-lg border border-neutral-200 bg-white">
                <div className="px-4 py-2.5 border-b border-neutral-100 flex items-center justify-between">
                  <span className="text-xs font-medium text-neutral-500 uppercase tracking-wide">Email Body</span>
                  <span className="text-xs text-neutral-400">Editable · plain text</span>
                </div>
                <textarea
                  rows={10}
                  value={emailBody}
                  onChange={(e) => setEmailBody(e.target.value)}
                  className="w-full resize-none px-4 py-4 text-sm text-neutral-700 font-mono leading-relaxed focus:outline-none"
                />
              </div>
              {/* PDF attachment indicator */}
              <div className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
                <FileText className="h-4 w-4 shrink-0 text-primary-500" />
                <span className="text-sm text-neutral-700 flex-1 font-mono">{po.number}.pdf</span>
                <span className="text-xs text-neutral-400">Auto-attached · cannot remove</span>
              </div>
              <p className="text-xs text-neutral-400">
                Template customisable in Admin Panel → Email Templates. PO will be marked <strong>Issued</strong> on send.
              </p>
            </div>
          )}

          {/* Step: Online confirm */}
          {step === 'online_confirm' && (
            <div className="flex flex-col gap-5">
              <div className="flex items-start gap-3 rounded-lg bg-neutral-50 border border-neutral-200 px-4 py-4">
                <Globe className="h-5 w-5 text-neutral-400 shrink-0 mt-0.5" />
                <p className="text-sm text-neutral-700">
                  Confirm that you have placed this order with <strong>{po.vendor_name}</strong> via their online portal or procurement platform. The PO will be marked as <strong>Issued</strong>.
                </p>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-neutral-700">Portal Reference / Order ID <span className="font-normal text-neutral-400">(optional)</span></label>
                <input
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                  className={inputCls}
                  placeholder="e.g. ORD-2026-00123"
                />
              </div>
            </div>
          )}

          {/* Step: Done */}
          {step === 'done' && (
            <div className="flex flex-col items-center gap-4 py-8 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-success-50">
                <CheckCircle2 className="h-7 w-7 text-success-600" />
              </div>
              <div>
                <p className="text-base font-semibold text-neutral-900">
                  {doneMethod === 'email' ? 'Email Sent — Order Placed' : 'Order Confirmed'}
                </p>
                {doneMethod === 'email' && (
                  <p className="mt-1 text-sm text-neutral-500">PO email sent to <span className="font-mono">{emailTo}</span>.</p>
                )}
                <p className="mt-0.5 text-sm text-neutral-500">PO status updated to <strong>Issued</strong>.</p>
              </div>
            </div>
          )}

          {error && (
            <p className="mt-3 rounded-lg bg-danger-50 border border-danger-200 px-3 py-2 text-xs text-danger-700">{error}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-neutral-100 px-6 py-4 shrink-0">
          {step === 'choose' && (
            <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          )}
          {step === 'email_compose' && (
            <>
              <Button variant="secondary" size="sm" onClick={() => setStep('choose')}>Back</Button>
              <button
                onClick={handleSendEmail}
                disabled={placeOrder.isPending}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
              >
                {placeOrder.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                Send &amp; Issue PO
              </button>
            </>
          )}
          {step === 'online_confirm' && (
            <>
              <Button variant="secondary" size="sm" onClick={() => setStep('choose')}>Back</Button>
              <button
                onClick={handleOnlineConfirm}
                disabled={placeOrder.isPending}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-success-600 px-3 text-sm font-medium text-white hover:bg-success-700 disabled:opacity-50"
              >
                {placeOrder.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                Confirm Order Placed
              </button>
            </>
          )}
          {step === 'done' && (
            <Button size="sm" onClick={onClose}>Done</Button>
          )}
        </div>
      </div>
    </div>,
    document.body
  )
}

// ─── Approval Action Modal ─────────────────────────────────────────────────────

type ApprovalAction = 'approve' | 'reject' | 'return'

interface ApprovalModalProps {
  action: ApprovalAction
  poNumber: string
  onConfirm: (comment: string) => void
  onClose: () => void
  // The modal stays mounted until the action resolves, so without this the
  // confirm button is live for the whole request. A second click re-posts the
  // same action: usually a 409 the user reads as a failure, but for 'approve'
  // it can silently consume the NEXT step's task when the same person approves
  // two consecutive steps — two levels passed on one intended click.
  isPending: boolean
}

function ApprovalModal({ action, poNumber, onConfirm, onClose, isPending }: ApprovalModalProps) {
  const [comment, setComment] = useState('')
  const needsComment = action !== 'approve'
  const canSubmit = !needsComment || comment.trim().length > 0

  const config = {
    approve: {
      title: 'Approve Purchase Order',
      label: 'Approve',
      icon: <CheckCircle2 className="h-5 w-5 text-success-600" />,
      bgIcon: 'bg-success-50',
      btn: 'bg-success-600 hover:bg-success-700 text-white',
      commentLabel: 'Comment (optional)',
      placeholder: 'Add an optional comment…',
    },
    reject: {
      title: 'Reject Purchase Order',
      label: 'Reject',
      icon: <XCircle className="h-5 w-5 text-danger-600" />,
      bgIcon: 'bg-danger-50',
      btn: 'bg-danger-600 hover:bg-danger-700 text-white',
      commentLabel: 'Reason for rejection *',
      placeholder: 'Explain why this PO is being rejected…',
    },
    return: {
      title: 'Return for Revision',
      label: 'Return',
      icon: <RotateCcw className="h-5 w-5 text-warning-600" />,
      bgIcon: 'bg-warning-50',
      btn: 'bg-warning-500 hover:bg-warning-600 text-white',
      commentLabel: 'Revision instructions *',
      placeholder: 'Describe what needs to be revised…',
    },
  }[action]

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className={cn('flex h-9 w-9 items-center justify-center rounded-full', config.bgIcon)}>
              {config.icon}
            </div>
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">{config.title}</h2>
              <p className="text-xs text-neutral-500">{poNumber}</p>
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-600">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">{config.commentLabel}</label>
            <div className="relative">
              <MessageSquare className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-neutral-400" />
              <textarea
                rows={3}
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder={config.placeholder}
                autoFocus
                className="w-full resize-none rounded-lg border border-neutral-300 bg-white pl-8 pr-3 pt-2 pb-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
              />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
            <button
              disabled={!canSubmit || isPending}
              onClick={() => onConfirm(comment)}
              className={cn(
                'inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed',
                config.btn
              )}
            >
              {config.icon}
              {isPending ? 'Working…' : config.label}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}

// ─── Page ──────────────────────────────────────────────────────────────────────

const TABS = ['Details', 'Linked GRs', 'Attachments', 'History'] as const
type Tab = typeof TABS[number]

export default function PoDetailPage() {
  const { id } = useParams()
  const { data: po, isLoading } = usePo(id ?? '')
  const { data: events } = usePoEvents(id ?? '')
  const { data: poAttachments = [] } = usePoAttachments(id ?? '')
  const uploadAttachment = useUploadPoAttachment(id ?? '')
  const deleteAttachment = useDeletePoAttachment(id ?? '')
  const regeneratePdf = useRegeneratePoPdf(id ?? '')
  const { data: grsData, isLoading: grsLoading } = useGrs({ po_id: id ?? '' }, Boolean(id))
  // Drives the Create PA gate below — see the comment there for why the PO's
  // own has_unpaid_invoice flag cannot be used on this page.
  const { data: poInvoices } = useInvoices({ po_id: id ?? '' }, Boolean(id))
  const linkedGrs = (grsData?.items ?? []).filter((g) => g.status !== 'cancelled')
  const poAction = usePoAction(id ?? '')
  const { user } = useAuthStore()
  const { data: config } = useConfig()
  const { data: workflowSteps } = usePoWorkflowSteps(id ?? '')
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<Tab>('Details')
  const [pendingAction, setPendingAction] = useState<ApprovalAction | null>(null)
  const [showPlaceOrder, setShowPlaceOrder] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const moreRef = useRef<HTMLDivElement>(null)

  const isProcurementOfficer = user?.role === 'procurement_officer' || user?.role === 'procurement_manager' || user?.role === 'system_admin'

  const downloadPoAttachment = (attId: string, filename: string) => {
    const base = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'
    const token = useAuthStore.getState().token
    fetch(`${base}/po/${id}/attachments/${attId}/download`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).then(async (res) => {
      if (!res.ok) return
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    })
  }

  // Approval gating is driven solely by the approval engine's task routing — no
  // client-side workflow/role fallback. The engine assigns each step to exactly the
  // resolved approver (e.g. gm_or_opm → GM or OPM by the creator's department), and
  // grants system_admin every task. Showing the Approve button only when the current
  // user holds an active approve_po task for THIS PO keeps the button consistent with
  // the inbox and follows whatever workflow is configured in the engine.
  const paPerms = useRolePermissions().data?.permissions
  const { data: myTasks } = useTasks({ is_completed: false })
  const hasApproveTask = !!po && !!(myTasks?.items ?? []).some(
    (t) => t.document_id === po.id && t.type === 'approve_po'
  )
  const canApprove =
    !!user &&
    !!po &&
    APPROVABLE_STATUSES.includes(po.status) &&
    hasApproveTask
  const canPlaceOrder = isProcurementOfficer && po?.status === 'approved'
  const canEdit = isProcurementOfficer && po && ['draft', 'returned'].includes(po.status)
  // NC-imported POs never reach draft/returned, so canEdit above can never fire
  // for them. Buyer detail (supplier item IDs, samples, Incoterms, delivery,
  // notes) is filled in through a separate, deliberately narrow endpoint —
  // gated by the Access Control Matrix, not a hardcoded role list, so the
  // button and PATCH /po/{id}/imported-details cannot disagree.
  const perms = useRolePermissions().data?.permissions
  const canEditImported =
    !!po &&
    po.source === 'nc' &&
    po.status === 'issued' &&
    (user?.role === 'system_admin' || !!perms?.['epms.po.edit_imported'])
  const canWithdraw = isProcurementOfficer && po && ['draft', 'submitted'].includes(po.status)
  // PA creation is permission-driven, exactly like the PA list's Create button
  // (PaListPage): the Access Control matrix decides, not a hard-coded role list.
  // A hard-coded list also read only the JWT's primary role, so an ADDITIONAL
  // role granted through user_roles (e.g. a Procurement Officer allowed to raise
  // PAs on someone's behalf) never saw the button even with the matrix ticked.
  // ...and on the PO actually having something to pay. The status alone is not
  // that: an 'issued' PO with no invoice yet offers nothing a PA could be raised
  // against, and PaCreatePage's own PO picker rejects it
  // (`is_prepaid || has_unpaid_invoice`, PaCreatePage.tsx) — so the button was
  // an invitation to a dead end. Mirror that same rule here so the entry point
  // and the page it opens agree. `po.has_unpaid_invoice` cannot be used: only
  // GET /po computes it, GET /po/{id} leaves the schema default false — hence
  // the invoice fetch above, which uses the same header-OR-allocation rule the
  // backend flag does (crud/invoice.py get_all).
  const hasUnpaidInvoice = (poInvoices?.items ?? []).some((inv) => inv.status !== 'paid')
  const canCreatePa =
    po &&
    ['issued', 'partially_received', 'fully_received'].includes(po.status) &&
    (po.is_prepaid || hasUnpaidInvoice) &&
    (user?.role === 'system_admin' || !!paPerms?.['epms.pa.write'])
  const isServicePo = po?.type === 4
  // Physical PO: warehouse/procurement roles, PO must be issued or partially received
  // Service PO: the *requester of the linked PR* (not the PO creator, not a generic
  // 'requester' role) can confirm delivery / create the GR, from approved onwards.
  const isPrRequester = !!po?.pr_requester_id && po.pr_requester_id === user?.id
  const canCreateGr =
    (['warehouse_staff', 'procurement_officer', 'procurement_manager', 'system_admin'].includes(user?.role ?? '') &&
      ['issued', 'partially_received'].includes(po?.status ?? '')) ||
    (isServicePo && isPrRequester &&
      ['approved', 'issued', 'partially_received'].includes(po?.status ?? ''))

  const handleDownloadPdf = () => {
    if (!po || !config) return
    downloadPdf(generatePoHtml(po, config), `${po.number}_Purchase_Order.pdf`)
  }

  const handleConfirm = (action: ApprovalAction, comment: string) => {
    if (!po) return
    const apiAction = action === 'approve' ? 'approve' : action === 'return' ? 'return' : 'reject'
    poAction.mutate(
      { action: apiAction, comment: comment || undefined },
      { onSuccess: () => setPendingAction(null) }
    )
  }

  if (isLoading) return <div className="p-8 text-center text-neutral-400">Loading…</div>

  if (!po) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <div className="text-5xl mb-4">🔍</div>
        <h2 className="text-xl font-semibold text-neutral-700">PO Not Found</h2>
        <p className="mt-2 text-sm text-neutral-400">The purchase order you're looking for doesn't exist.</p>
        <Link to="/po" className="mt-4">
          <Button variant="secondary">Back to PO List</Button>
        </Link>
      </div>
    )
  }

  const approvalSteps = buildWorkflowSteps(workflowSteps ?? [], po.status, po.approval_step_idx ?? 0, events ?? [])
  // Mirrors the PDF: the Sample column only appears when some line carries one,
  // so POs without samples keep their existing layout.
  const showSample = po.line_items.some((li) => li.sample)
  const hasMaterial = po.type === 1 || po.type === 3

  return (
    <div className={cn('flex flex-col gap-6', canApprove && 'pb-16')}>
      {/* Page header */}
      <div className="rounded-lg border border-neutral-200 bg-white px-6 py-4">
        <div className="flex items-center gap-3 mb-3">
          <Link to="/po">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4" />
              Back to PO List
            </Button>
          </Link>
        </div>
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-bold text-neutral-900">{po.number}</h1>
              <StatusBadge status={po.status as DocumentStatus} />
              {po.source === 'nc' && (
                <span title="This PO was mirrored from NC ERP — read-only origin context">
                  <Badge variant="neutral">Synced from NC ERP</Badge>
                </span>
              )}
              {po.is_prepaid && (
                <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800">
                  Prepayment
                </span>
              )}
            </div>
            <p className="mt-1 text-neutral-600">{po.title} — {po.vendor_name}</p>
            {po.pr_number && (
              <Link
                to={`/pr/${po.pr_id}`}
                className="mt-0.5 inline-flex items-center gap-1 text-xs text-neutral-400 hover:text-primary-600"
              >
                From PR: {po.pr_number}
                <ExternalLink className="h-3 w-3" />
              </Link>
            )}
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            {canEdit && (
              <Button variant="secondary" size="sm" onClick={() => navigate(`/po/${po.id}/edit`)}>
                <Pencil className="h-3.5 w-3.5" />
                Edit
              </Button>
            )}
            {canEditImported && (
              <Button variant="secondary" size="sm" onClick={() => navigate(`/po/${po.id}/edit-imported`)}>
                <Pencil className="h-3.5 w-3.5" />
                Edit Details
              </Button>
            )}
            {canWithdraw && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => poAction.mutate({ action: 'cancel' })}
                disabled={poAction.isPending}
              >
                Withdraw
              </Button>
            )}
            {canPlaceOrder && (
              <button
                onClick={() => setShowPlaceOrder(true)}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-sm font-medium text-white transition-colors hover:bg-primary-700"
              >
                <ShoppingCart className="h-3.5 w-3.5" />
                Place Order
              </button>
            )}
            {canCreateGr && (
              <button
                onClick={() => navigate(`/gr/new?poId=${po.id}`)}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-success-600 px-3 text-sm font-medium text-white transition-colors hover:bg-success-700"
              >
                <Warehouse className="h-3.5 w-3.5" />
                {isServicePo ? 'Confirm Service' : 'Create GR'}
              </button>
            )}
            {canCreatePa && (
              <button
                onClick={() => navigate(`/pa/new?poId=${po.id}`)}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-primary-600 px-3 text-sm font-medium text-primary-600 transition-colors hover:bg-primary-50"
              >
                <CreditCard className="h-3.5 w-3.5" />
                Create PA
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Main content + timeline */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left: tabs */}
        <div className="lg:col-span-8 flex flex-col gap-4">
          {/* Tab bar */}
          <div className="flex border-b border-neutral-200 overflow-x-auto">
            {TABS.map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={[
                  'px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors whitespace-nowrap',
                  activeTab === tab
                    ? 'border-primary-600 text-primary-700'
                    : 'border-transparent text-neutral-500 hover:text-neutral-700',
                ].join(' ')}
              >
                {tab === 'Attachments' ? 'Attachments' : tab}
              </button>
            ))}
          </div>

          {/* Details Tab */}
          {activeTab === 'Details' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-6">
              <section>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-3">Details</h2>
                <dl className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2 text-sm">
                  {([
                    ['Procurement Type', `Type ${po.type} — ${TYPE_LABELS[po.type]}`],
                    ['Vendor', po.vendor_name],
                    ['Currency', po.currency],
                    ['Budget Code', po.budget_code ?? '—'],
                    ['Created', formatDate(po.created_at)],
                    ['Expected Delivery', po.expected_delivery ? formatDate(po.expected_delivery) : '—'],
                    ['Delivery Address', po.delivery_address || '—'],
                    ['PR Reference', po.pr_number || '—'],
                  ] as [string, string][]).map(([label, value]) => (
                    <div key={label} className="flex flex-col gap-0.5">
                      <dt className="text-xs font-medium text-neutral-500">{label}</dt>
                      <dd className="text-neutral-900">{value}</dd>
                    </div>
                  ))}
                  {po.incoterms && (
                    <div className="flex flex-col gap-0.5">
                      <dt className="text-xs font-medium text-neutral-500">Incoterms</dt>
                      <dd className="text-neutral-900">{po.incoterms}</dd>
                    </div>
                  )}
                  {/* purchase_orders.notes means two different things depending on origin:
                      buyer text (from the Create PO page's "Buyer Notes / Terms &
                      Conditions" box) on native POs, but ERP-owned sync text (rewritten
                      every NC sync with [NC Paid] / [NC Closed <date>] markers for
                      finance) on NC POs. The rows below are gated accordingly — Buyer
                      Notes only falls back to notes on non-NC POs, and NC Sync Notes is a
                      separate, NC-only row so the markers stay visible to finance here on
                      the internal detail page without leaking into the vendor-facing PDF,
                      which mirrors this same split (pdf_po.py) and prints neither NC row. */}
                  {(po.buyer_notes || (po.source !== 'nc' ? po.notes : undefined)) && (
                    <div className="flex flex-col gap-0.5 sm:col-span-2">
                      <dt className="text-xs font-medium text-neutral-500">Buyer Notes</dt>
                      <dd className="whitespace-pre-wrap text-neutral-900">
                        {po.buyer_notes || po.notes}
                      </dd>
                    </div>
                  )}
                  {po.source === 'nc' && po.notes && (
                    <div className="flex flex-col gap-0.5 sm:col-span-2">
                      <dt className="text-xs font-medium text-neutral-500">NC Sync Notes</dt>
                      <dd className="whitespace-pre-wrap text-neutral-900">{po.notes}</dd>
                    </div>
                  )}
                  <div className="flex flex-col gap-0.5">
                    <dt className="text-xs font-medium text-neutral-500">Prepayment PO</dt>
                    <dd>
                      {po.is_prepaid ? (
                        <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800">
                          Yes — Prepayment required
                        </span>
                      ) : (
                        <span className="text-sm text-neutral-900">No</span>
                      )}
                    </dd>
                  </div>
                </dl>
              </section>

              {/* Financial summary */}
              <section>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-3">Financial Summary</h2>
                <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-5 py-4 flex flex-col gap-2 max-w-sm">
                  <div className="flex justify-between text-sm text-neutral-700">
                    <span>Subtotal</span>
                    <span className="amount">{formatAmount(po.subtotal, po.currency)}</span>
                  </div>
                  <div className="flex justify-between text-sm text-neutral-700">
                    <span>{TAX_LABELS[Number(po.tax_rate)] ?? `${Math.round(Number(po.tax_rate) * 100)}% Tax`}</span>
                    <span className="amount">{formatAmount(po.tax_amount, po.currency)}</span>
                  </div>
                  <div className="border-t border-neutral-300 mt-1 pt-2 flex justify-between">
                    <span className="text-sm font-bold text-neutral-900">Total ({po.currency})</span>
                    <span className="amount text-base font-bold text-neutral-900">{formatAmount(po.total, po.currency)}</span>
                  </div>
                </div>
              </section>

              {/* Line Items */}
              <section>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-3">
                  Line Items <span className="normal-case font-normal text-neutral-400">({po.line_items.length})</span>
                </h2>
                <div className="rounded-lg border border-neutral-200 overflow-hidden">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-neutral-200 bg-neutral-50">
                          <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 w-10">#</th>
                          <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500">Description</th>
                          {hasMaterial && (
                            <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 w-32">Material ID</th>
                          )}
                          {/* Always shown for consistency with the Create PO page, which always exposes this input. */}
                          <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 w-36">Supplier Item ID</th>
                          <th className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 w-20">Qty</th>
                          <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 w-20">Unit</th>
                          {showSample && (
                            <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 w-24">Sample</th>
                          )}
                          <th className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 w-32">Unit Price</th>
                          <th className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 w-32">Line Total</th>
                          <th className="px-4 py-3 text-center text-xs font-semibold text-neutral-500 w-28">Received</th>
                        </tr>
                      </thead>
                      <tbody>
                        {po.line_items.map((item, i) => {
                          const pct = item.qty > 0 ? Math.min((item.received_qty / item.qty) * 100, 100) : 0
                          return (
                            <tr
                              key={item.id}
                              className={`border-b border-neutral-100 last:border-0 ${i % 2 === 1 ? 'bg-neutral-50/50' : 'bg-white'}`}
                            >
                              <td className="px-4 py-2.5 text-xs text-neutral-400">{i + 1}</td>
                              <td className="px-4 py-2.5 text-neutral-900">{item.description}</td>
                              {hasMaterial && (
                                <td className="px-4 py-2.5 font-mono text-xs text-neutral-600">{item.material_id || '—'}</td>
                              )}
                              <td className="px-4 py-2.5 font-mono text-xs text-neutral-600">{item.supplier_item_id || '—'}</td>
                              <td className="px-4 py-2.5 text-right font-mono text-neutral-900">{item.qty}</td>
                              <td className="px-4 py-2.5 text-neutral-500">{item.unit}</td>
                              {showSample && (
                                <td className="px-4 py-2.5 text-neutral-500">{item.sample || '—'}</td>
                              )}
                              <td className="px-4 py-2.5 amount text-right text-neutral-900">{formatAmount(item.unit_price, po.currency)}</td>
                              <td className="px-4 py-2.5 amount text-right font-semibold text-neutral-900">{formatAmount(item.line_total, po.currency)}</td>
                              <td className="px-4 py-2.5">
                                <div className="flex flex-col items-center gap-1">
                                  <span className="text-xs text-neutral-600">{item.received_qty} / {item.qty}</span>
                                  <div className="h-1.5 w-full rounded-full bg-neutral-200">
                                    <div
                                      style={{ width: `${pct}%` }}
                                      className={cn(
                                        'h-full rounded-full transition-all',
                                        pct >= 100 ? 'bg-success-500' : pct > 0 ? 'bg-primary-500' : 'bg-neutral-300'
                                      )}
                                    />
                                  </div>
                                </div>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                      <tfoot>
                        <tr className="border-t-2 border-neutral-200 bg-neutral-50">
                          <td colSpan={hasMaterial ? 7 : 6} className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">
                            Subtotal
                          </td>
                          <td className="px-4 py-3 amount text-right text-base font-bold text-neutral-900">
                            {formatAmount(po.subtotal, po.currency)}
                          </td>
                          <td />
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                  <div className="border-t border-neutral-200 px-4 py-3 flex justify-end gap-8 text-sm bg-neutral-50">
                    <span className="text-neutral-500">{TAX_LABELS[Number(po.tax_rate)] ?? 'Tax'}: <span className="amount text-neutral-700">{formatAmount(po.tax_amount, po.currency)}</span></span>
                    <span className="font-bold text-neutral-900">Total: <span className="amount">{formatAmount(po.total, po.currency)}</span></span>
                  </div>
                </div>
              </section>
            </div>
          )}

          {/* Linked GRs Tab */}
          {activeTab === 'Linked GRs' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-4">Linked Goods Receipts</h2>
              {grsLoading ? (
                <div className="flex items-center justify-center py-12 text-neutral-400">
                  <Loader2 className="h-5 w-5 animate-spin" />
                </div>
              ) : linkedGrs.length > 0 ? (
                <ul className="flex flex-col gap-2">
                  {linkedGrs.map((gr) => (
                    <li key={gr.id}>
                      <Link
                        to={`/gr/${gr.id}`}
                        className="flex w-full items-center gap-3 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2.5 text-left text-sm transition-colors hover:border-primary-300 hover:bg-primary-50 group"
                      >
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-100 group-hover:bg-primary-200 shrink-0">
                          <Warehouse className="h-4 w-4 text-primary-600" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-sm font-semibold text-neutral-700 truncate">{gr.number}</span>
                            <StatusBadge status={GR_STATUS_DOC[gr.status]} label={GR_STATUS_LABELS[gr.status]} />
                          </div>
                          <p className="text-xs text-neutral-400 mt-0.5 truncate">
                            {gr.gr_type === 'physical' ? 'Physical' : 'Service'} · Received {formatDate(gr.received_at)}
                          </p>
                        </div>
                        <ExternalLink className="h-4 w-4 shrink-0 text-neutral-300 group-hover:text-primary-400" />
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="flex flex-col items-center justify-center py-12 text-center">
                  <span className="text-3xl mb-3">🏭</span>
                  <p className="text-sm text-neutral-400">No goods receipts linked to this PO yet.</p>
                  <p className="text-xs text-neutral-300 mt-1">They will appear here once received.</p>
                </div>
              )}
            </div>
          )}

          {/* Attachments Tab */}
          {activeTab === 'Attachments' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">Attachments</h2>
                {['approved', 'issued', 'partially_received', 'fully_received', 'closed'].includes(po.status) && (
                  <button
                    type="button"
                    onClick={() => regeneratePdf.mutate()}
                    disabled={regeneratePdf.isPending}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-100 disabled:opacity-50"
                    title="Generate the approved-PO PDF and attach it (replaces the existing one)"
                  >
                    <RotateCcw className={`h-3.5 w-3.5 ${regeneratePdf.isPending ? 'animate-spin' : ''}`} />
                    {regeneratePdf.isPending ? 'Generating…' : 'Regenerate PDF'}
                  </button>
                )}
              </div>
              <AttachmentsEditor
                inputId="po-detail-file-upload"
                attachments={poAttachments}
                isUploading={uploadAttachment.isPending}
                isDeleting={deleteAttachment.isPending}
                onUpload={(file) => uploadAttachment.mutateAsync(file)}
                onDelete={(attId) => deleteAttachment.mutate(attId)}
                onDownload={(att) => downloadPoAttachment(att.id, att.filename)}
              />
              {poAttachments.length === 0 && (
                <div className="mt-3 flex flex-col items-center justify-center text-center">
                  <p className="text-sm text-neutral-400">
                    {po.status === 'approved' ? 'PDF is being generated…' : 'No attachments yet'}
                  </p>
                  {po.status !== 'approved' && (
                    <button onClick={handleDownloadPdf} className="mt-1 text-xs text-primary-600 hover:underline">
                      Generate preview PDF
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          {/* History Tab */}
          {activeTab === 'History' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-4">Audit History</h2>
              {events && events.length > 0 ? (
                <ul className="flex flex-col gap-3">
                  {events.map((e) => (
                    <li key={e.id} className="flex gap-3 text-sm">
                      <span className="text-xs text-neutral-400 whitespace-nowrap pt-0.5">{formatDate(e.created_at)}</span>
                      <div>
                        <span className="font-medium text-neutral-800 capitalize">{e.action.replace(/_/g, ' ')}</span>
                        <span className="text-neutral-500 ml-1">by {e.actor_role.replace(/_/g, ' ')}</span>
                        {e.actor_name && <span className="text-neutral-400 ml-1">— {e.actor_name}</span>}
                        {e.comment && <p className="text-neutral-400 text-xs mt-0.5 italic">{e.comment}</p>}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-sm text-neutral-400 text-center py-8">No history yet</div>
              )}
            </div>
          )}
        </div>

        {/* Right: approval timeline */}
        <div className="lg:col-span-4">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5 sticky top-6">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-4">Approval Timeline</h2>
            <ApprovalTimeline
              steps={approvalSteps}
              onSendReminder={(id) => console.log('remind', id)}
            />

            {/* Place Order panel for procurement officer */}
            {canPlaceOrder && (
              <div className="mt-5 border-t border-neutral-100 pt-4 flex flex-col gap-2">
                <p className="text-xs font-medium text-neutral-500 mb-1">Ready to Place Order</p>
                <button
                  onClick={() => setShowPlaceOrder(true)}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary-600 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-700"
                >
                  <ShoppingCart className="h-4 w-4" />
                  Place Order
                </button>
              </div>
            )}

            {/* Document Chain */}
            <DocumentChainTree currentType="po" id={po.id} />
          </div>
        </div>
      </div>

      {/* Fixed bottom approver action bar */}
      {canApprove && (
        <div className="fixed bottom-0 left-60 right-0 z-10 bg-white border-t border-neutral-200 px-6 py-3 flex items-center justify-between shadow-[0_-1px_8px_rgba(0,0,0,0.06)] max-md:left-0">
          <p className="text-sm text-neutral-500">
            Reviewing <span className="font-medium text-neutral-900">{po.number}</span>
          </p>
          <div className="flex items-center gap-2">
            <div className="relative" ref={moreRef}>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setMoreOpen((v) => !v)}
                className="gap-1"
              >
                More <ChevronDown className="h-3.5 w-3.5" />
              </Button>
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
          poNumber={po.number}
          onConfirm={(comment) => handleConfirm(pendingAction, comment)}
          onClose={() => setPendingAction(null)}
          isPending={poAction.isPending}
        />
      )}

      {/* Place Order modal */}
      {showPlaceOrder && (
        <PlaceOrderModal
          po={po}
          onClose={() => setShowPlaceOrder(false)}
        />
      )}
    </div>
  )
}
