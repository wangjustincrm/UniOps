import { useState, useRef } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import {
  ArrowLeft, CheckCircle2, XCircle, RotateCcw, Pencil, ChevronDown, MessageSquare, Send, ExternalLink, FileText, Mail, ShoppingCart, Warehouse, Globe, Loader2, CreditCard, SearchX, Factory,
} from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Badge, StatusBadge } from '@/components/ui/badge'
import { ApprovalTimeline } from '@/components/pr/ApprovalTimeline'
import { formatAmount, formatDate, formatQty, formatUnitPrice, cn } from '@/lib/utils'
import type { ApprovalStep, CurrentStep, DocumentStatus, WorkflowNodeDef } from '@/types'
import { useAuthStore } from '@/stores/auth.store'
import { DocumentChainTree } from '@/components/shared/DocumentChainTree'
import { PoSignoffPanel } from '@/components/po/PoSignoffPanel'
import { PoFeePanel } from '@/components/po/PoFeePanel'
import { generatePoHtml } from '@/lib/po-document'
import { buildEmailVars, renderTemplate } from '@/lib/email-template'
import { useConfig, useRolePermissions } from '@/hooks/useConfig'
import { downloadPdf } from '@/lib/pdf-utils'
import { usePo, usePoAction, usePoAttachments, useUploadPoAttachment, useDeletePoAttachment, usePoEvents, usePlaceOrder, usePoWorkflowSteps, useRegeneratePoPdf } from '@/hooks/usePos'
import { AttachmentsEditor } from '@/components/shared/AttachmentsEditor'
import { useGrs } from '@/hooks/useGrs'

import { useTasks } from '@/hooks/useTasks'
import { useCloseOnEscape } from '@/hooks/useCloseOnEscape'
import { useConfirmDialog } from '@/components/ui/ConfirmDialog'
import { errorMessage } from '@/lib/submitFailure'
import { poService } from '@/services/po'
import type { ApiPo, ApiPoLineItem } from '@/services/po'
import { useApprovalReminder } from '@/hooks/useApprovalReminder'
import type { ApiEvent } from '@/services/pr'
import { historyLine } from '@/lib/approvalHistory'
import { buildApprovalNodes } from '@/lib/approvalTimeline'
import type { GrStatus } from '@/services/gr'
import { vendorService } from '@/services/vendors'
import { isImportedEditablePo } from '@/lib/importedPoEdit'
import { canWithdrawDocument } from '@/lib/documentActions'
import { formatPaymentTerms } from '@/lib/paymentTerms'

import { Alert, ChoiceCard, FormField, Input, MenuItem, Modal, TBody, TFoot, THead, Table, Tabs, Td, Textarea, Th, Tr } from '@uniops/shell'
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

function buildWorkflowSteps(
  nodes: WorkflowNodeDef[], status: string, stepIdx: number, events: ApiEvent[] = [],
  currentStep?: CurrentStep | null,
): ApprovalStep[] {
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
  // Shared with the PR / Agreement / PA timelines. A `submitted` PO sits on its
  // first approver's open task like any other document and shows that step as
  // current (ISS-0088); it used to be lumped with nc_pending and showed none.
  // 'nc_pending' = awaiting approval in NC, where the real signatories are: it
  // is not an awaiting status there, so every UniOps step renders as pending
  // rather than 'current' — no task was ever raised here.
  const approvalNodes = buildApprovalNodes({
    nodes, status, stepIdx, events, currentStep,
    doneStatuses: ['approved', 'issued', 'partially_received', 'fully_received', 'closed', 'nc_milk'],
    ownerLabel: 'Procurement Officer',
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

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={step !== 'done'}
      busy={placeOrder.isPending}
      // Closed only by its buttons, as before: Escape must not drop what was typed.
      closeOnEscape={false}
      size="lg"
      icon={<ShoppingCart />}
      title="Place Order"
      description={`${po.number} · ${po.vendor_name}`}
      footer={<>
          {step === 'choose' && (
            <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          )}
          {step === 'email_compose' && (
            <>
              <Button variant="secondary" size="sm" onClick={() => setStep('choose')}>Back</Button>
              <Button size="sm"
                onClick={handleSendEmail}
                disabled={placeOrder.isPending}
               
              >
                {placeOrder.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                Send &amp; Issue PO
              </Button>
            </>
          )}
          {step === 'online_confirm' && (
            <>
              <Button variant="secondary" size="sm" onClick={() => setStep('choose')}>Back</Button>
              <Button variant="success" size="sm"
                onClick={handleOnlineConfirm}
                disabled={placeOrder.isPending}
               
              >
                {placeOrder.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                Confirm Order Placed
              </Button>
            </>
          )}
          {step === 'done' && (
            <Button size="sm" onClick={onClose}>Done</Button>
          )}
      </>}
    >

          {/* Step: Choose method */}
          {step === 'choose' && (
            <div className="flex flex-col gap-4">
              <p className="text-sm text-fg-secondary">How would you like to place this order?</p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {/* Option A — Email Supplier */}
                <ChoiceCard
                  onClick={initEmail}
                  className="group gap-3 p-5"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary-50 ">
                    <Mail className="h-5 w-5 text-primary-600" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-fg">Place Order via Email</p>
                    <p className="mt-1 text-xs text-fg-muted">
                      Send a PO email to the vendor using the configured template. PO PDF is included.
                    </p>
                    {vendorEmail && (
                      <p className="mt-1.5 text-xs font-mono text-fg-muted truncate">{vendorEmail}</p>
                    )}
                    {!vendorEmail && (
                      <p className="mt-1.5 text-xs text-warning-fg">No vendor email on file — add one in Vendor Master first.</p>
                    )}
                  </div>
                </ChoiceCard>

                {/* Option B — Place Order Online */}
                <ChoiceCard
                  onClick={() => setStep('online_confirm')}
                  className="group gap-3 p-5"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-success-soft ">
                    <Globe className="h-5 w-5 text-success-fg" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-fg">Place Order Online</p>
                    <p className="mt-1 text-xs text-fg-muted">
                      You placed the order via the vendor's online portal. Confirm here to mark the PO as Issued.
                    </p>
                  </div>
                </ChoiceCard>
              </div>
            </div>
          )}

          {/* Step: Email Compose */}
          {step === 'email_compose' && (
            <div className="flex flex-col gap-4">
              <div className="rounded-lg border border-line bg-surface-subtle p-4 flex flex-col gap-3">
                <div className="flex items-start gap-3 text-sm">
                  <span className="text-fg-muted w-16 shrink-0 pt-2">To:</span>
                  <Input
                    value={emailTo}
                    onChange={(e) => setEmailTo(e.target.value)}
                   
                    placeholder="vendor@example.com"
                    type="email"
                  />
                </div>
                <div className="flex items-start gap-3 text-sm border-t border-line pt-3">
                  <span className="text-fg-muted w-16 shrink-0 pt-2">CC:</span>
                  <Input
                    value={emailCc}
                    onChange={(e) => setEmailCc(e.target.value)}
                   
                    placeholder="cc@example.com"
                    type="email"
                  />
                </div>
                <div className="flex items-start gap-3 text-sm border-t border-line pt-3">
                  <span className="text-fg-muted w-16 shrink-0 pt-2">Subject:</span>
                  <Input
                    value={emailSubject}
                    onChange={(e) => setEmailSubject(e.target.value)}
                   
                    placeholder="Purchase Order…"
                  />
                </div>
              </div>
              <div className="rounded-lg border border-line bg-surface">
                <div className="px-4 py-2.5 border-b border-line-subtle flex items-center justify-between">
                  <span className="text-xs font-medium text-fg-muted">Email Body</span>
                  <span className="text-xs text-fg-muted">Editable · plain text</span>
                </div>
                <Textarea
                  rows={10}
                  value={emailBody}
                  onChange={(e) => setEmailBody(e.target.value)}
                  className="w-full resize-none font-mono leading-relaxed"
                />
              </div>
              {/* PDF attachment indicator */}
              <div className="flex items-center gap-2 rounded-lg border border-line bg-surface-subtle px-3 py-2">
                <FileText className="h-4 w-4 shrink-0 text-primary-500" />
                <span className="text-sm text-fg-secondary flex-1 font-mono">{po.number}.pdf</span>
                <span className="text-xs text-fg-muted">Auto-attached · cannot remove</span>
              </div>
              <p className="text-xs text-fg-muted">
                Template customisable in Admin Panel → Email Templates. PO will be marked <strong>Issued</strong> on send.
              </p>
            </div>
          )}

          {/* Step: Online confirm */}
          {step === 'online_confirm' && (
            <div className="flex flex-col gap-5">
              <div className="flex items-start gap-3 rounded-lg bg-surface-subtle border border-line px-4 py-4">
                <Globe className="h-5 w-5 text-fg-muted shrink-0 mt-0.5" />
                <p className="text-sm text-fg-secondary">
                  Confirm that you have placed this order with <strong>{po.vendor_name}</strong> via their online portal or procurement platform. The PO will be marked as <strong>Issued</strong>.
                </p>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-fg-secondary">Portal Reference / Order ID <span className="font-normal text-fg-muted">(optional)</span></label>
                <Input
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                 
                  placeholder="e.g. ORD-2026-00123"
                />
              </div>
            </div>
          )}

          {/* Step: Done */}
          {step === 'done' && (
            <div className="flex flex-col items-center gap-4 py-8 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-success-soft">
                <CheckCircle2 className="h-7 w-7 text-success-fg" />
              </div>
              <div>
                <p className="text-base font-semibold text-fg">
                  {doneMethod === 'email' ? 'Email Sent — Order Placed' : 'Order Confirmed'}
                </p>
                {doneMethod === 'email' && (
                  <p className="mt-1 text-sm text-fg-muted">PO email sent to <span className="font-mono">{emailTo}</span>.</p>
                )}
                <p className="mt-0.5 text-sm text-fg-muted">PO status updated to <strong>Issued</strong>.</p>
              </div>
            </div>
          )}

          {error && <Alert tone="danger" className="mt-3">{error}</Alert>}
    </Modal>
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
  /** The server's refusal, shown in the modal (was a browser alert). */
  error?: string | null
}

function ApprovalModal({ action, poNumber, onConfirm, onClose, isPending, error }: ApprovalModalProps) {
  const [comment, setComment] = useState('')
  const needsComment = action !== 'approve'
  const canSubmit = !needsComment || comment.trim().length > 0

  const config = {
    approve: {
      title: 'Approve Purchase Order',
      label: 'Approve',
      icon: <CheckCircle2 className="h-5 w-5 text-success-fg" />,
      bgIcon: 'bg-success-soft',
      variant: 'success' as const,
      commentLabel: 'Comment (optional)',
      placeholder: 'Add an optional comment…',
    },
    reject: {
      title: 'Reject Purchase Order',
      label: 'Reject',
      icon: <XCircle className="h-5 w-5 text-danger-fg" />,
      bgIcon: 'bg-danger-soft',
      variant: 'danger' as const,
      commentLabel: 'Reason for rejection *',
      placeholder: 'Explain why this PO is being rejected…',
    },
    return: {
      title: 'Return for Revision',
      label: 'Return',
      icon: <RotateCcw className="h-5 w-5 text-warning-fg" />,
      bgIcon: 'bg-warning-soft',
      variant: 'warning' as const,
      commentLabel: 'Revision instructions *',
      placeholder: 'Describe what needs to be revised…',
    },
  }[action]

  return (
    <Modal
      open
      onClose={onClose}
      busy={isPending}
      // Closed only by its buttons, as before: Escape must not drop what was typed.
      closeOnEscape={false}
      size="sm"
      icon={config.icon}
      title={config.title}
      description={poNumber}
      bodyClassName="flex flex-col gap-4"
      footer={<>
        <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
        <Button variant={config.variant} size="sm" disabled={!canSubmit || isPending} onClick={() => onConfirm(comment)}>
          {config.icon}
          {isPending ? 'Working…' : config.label}
        </Button>
      </>}
    >
      <FormField label={config.commentLabel} htmlFor="po-action-comment">
        <div className="relative">
          <MessageSquare className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-fg-muted" />
          <Textarea
            id="po-action-comment"
            rows={3}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder={config.placeholder}
            autoFocus
            className="resize-none pl-8"
          />
        </div>
      </FormField>
      {error && <Alert tone="danger">{error}</Alert>}
    </Modal>
  )
}

// ─── Page ──────────────────────────────────────────────────────────────────────

const TABS = ['Details', 'Linked GRs', 'Attachments', 'History'] as const
type Tab = typeof TABS[number]

export default function PoDetailPage() {
  const { id } = useParams()
  const { data: po, isLoading } = usePo(id ?? '')
  useDocTabTitle(po?.number)
  const { data: events } = usePoEvents(id ?? '')
  const { data: poAttachments = [] } = usePoAttachments(id ?? '')
  const uploadAttachment = useUploadPoAttachment(id ?? '')
  const deleteAttachment = useDeletePoAttachment(id ?? '')
  const regeneratePdf = useRegeneratePoPdf(id ?? '')
  const { data: grsData, isLoading: grsLoading } = useGrs({ po_id: id ?? '' }, Boolean(id))
  const linkedGrs = (grsData?.items ?? []).filter((g) => g.status !== 'cancelled')
  const poAction = usePoAction(id ?? '')
  // Withdraw shows its refusal in the header rather than in an alert().
  const poWithdraw = usePoAction(id ?? '')
  const confirmDlg = useConfirmDialog()
  const { reminder, sendReminder } = useApprovalReminder(() => poService.remind(id ?? ''))
  const { user } = useAuthStore()
  const { data: config } = useConfig()
  const { data: workflowSteps } = usePoWorkflowSteps(id ?? '')
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<Tab>('Details')
  const [pendingAction, setPendingAction] = useState<ApprovalAction | null>(null)
  const [showPlaceOrder, setShowPlaceOrder] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const moreRef = useRef<HTMLDivElement>(null)
  useCloseOnEscape(moreOpen, () => setMoreOpen(false))

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
    isImportedEditablePo(po) &&
    (user?.role === 'system_admin' || !!perms?.['epms.po.edit_imported'])
  // Same parties and statuses as the engine's cancel (lib/documentActions.ts):
  // draft / returned / submitted, by the creator, a System Admin or the
  // approver holding it — not "any procurement role" (ISS-0082).
  const canWithdraw = !!po && canWithdrawDocument('po', po, user ?? {}, hasApproveTask)
  // PA creation is permission-driven, exactly like the PA list's Create button
  // (PaListPage): the Access Control matrix decides, not a hard-coded role list.
  // A hard-coded list also read only the JWT's primary role, so an ADDITIONAL
  // role granted through user_roles (e.g. a Procurement Officer allowed to raise
  // PAs on someone's behalf) never saw the button even with the matrix ticked.
  // ...and on the PO actually having something to pay. The status alone is not
  // that: an 'issued' PO with no invoice yet — or one whose only invoice is
  // already on somebody's payment — offers nothing a PA could be raised
  // against, and PaCreatePage's own PO picker rejects it
  // (`is_prepaid || has_unpaid_invoice`, PaCreatePage.tsx), so the button would
  // be an invitation to a dead end.
  //
  // This used to re-derive the rule client-side from the PO's invoices, because
  // GET /po/{id} left has_unpaid_invoice at its schema default. It now computes
  // it (api/v1/po.py, off crud.po::payable_invoice_po_ids), so the button and
  // the picker read the SAME flag and cannot drift apart — which they did the
  // moment "unpaid" stopped being sufficient.
  const canCreatePa =
    po &&
    ['issued', 'partially_received', 'fully_received', 'nc_milk'].includes(po.status) &&
    (po.is_prepaid || po.has_unpaid_invoice) &&
    (user?.role === 'system_admin' || !!paPerms?.['epms.pa.write'])
  // Types 4 (Service) and 6 (Project-Related) both run the service GR flow —
  // api/v1/gr.py has always admitted the pair. Checking only type 4 here left
  // project POs reachable from GR List but not from their own PO page.
  const isServicePo = po?.type === 4 || po?.type === 6
  // Physical PO: warehouse/procurement roles, PO must be issued or partially received
  // Service PO: the *owner or requester of the linked PR* (not the PO creator,
  // not a generic 'requester' role) can confirm delivery / create the GR, from
  // approved onwards.
  //
  // The UNION of pr_owner_id and pr_requester_id, matching POST /gr's own gate
  // (epms-api api/v1/gr.py). The two differ whenever a service/project PR named
  // someone else as its owner: the owner is who receives the confirm-service
  // task, so gating on the requester alone would show the reminder's recipient
  // no button — and dropping the requester arm would take the button away from
  // someone the API still admits.
  const canConfirmService =
    (!!po?.pr_owner_id && po.pr_owner_id === user?.id) ||
    (!!po?.pr_requester_id && po.pr_requester_id === user?.id)
  // A service PO confirmed while still approved keeps that status (PT-008),
  // so the status alone cannot tell that nothing is left to confirm (ISS-0108).
  const hasOpenQty = (po?.line_items ?? []).some((li) => Number(li.received_qty ?? 0) < Number(li.qty))
  const canCreateGr = (!isServicePo || hasOpenQty) && (
    (['warehouse_staff', 'procurement_officer', 'procurement_manager', 'system_admin'].includes(user?.role ?? '') &&
      ['issued', 'partially_received'].includes(po?.status ?? '')) ||
    (isServicePo && canConfirmService &&
      ['approved', 'issued', 'partially_received'].includes(po?.status ?? '')))

  const handleDownloadPdf = () => {
    if (!po || !config) return
    downloadPdf(generatePoHtml(po, config), `${po.number}_Purchase_Order.pdf`)
  }

  const handleConfirm = (action: ApprovalAction, comment: string) => {
    if (!po) return
    const apiAction = action === 'approve' ? 'approve' : action === 'return' ? 'return' : 'reject'
    poAction.reset()
    poAction.mutate(
      { action: apiAction, comment: comment || undefined },
      { onSuccess: () => setPendingAction(null) }
    )
  }

  if (isLoading) return <div className="p-8 text-center text-fg-muted">Loading…</div>

  if (!po) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <SearchX aria-hidden className="mb-4 h-10 w-10 stroke-[1.5] text-fg-faint" />
        <h2 className="text-xl font-semibold text-fg-secondary">PO Not Found</h2>
        <p className="mt-2 text-sm text-fg-muted">The purchase order you're looking for doesn't exist.</p>
        <BackLink to="/po" className="mt-4">
          <Button variant="secondary">Back to PO List</Button>
        </BackLink>
      </div>
    )
  }

  const approvalSteps = buildWorkflowSteps(workflowSteps ?? [], po.status, po.approval_step_idx ?? 0, events ?? [], po.current_step)
  // Mirrors the PDF: the Sample column only appears when some line carries one,
  // so POs without samples keep their existing layout.
  const showSample = po.line_items.some((li) => li.sample)
  // Delivery Date mirrors the same conditional-column pattern: it only appears
  // when a line actually carries an ERP-synced planned_arrival_date, so POs
  // without one (UniOps-native POs) keep their existing layout.
  const showDeliveryDate = po.line_items.some((li) => li.planned_arrival_date)
  const hasMaterial = po.type === 1 || po.type === 3

  // expected_delivery is a human-entered header override; NC-synced POs never
  // populate it. When absent, fall back to the earliest ERP-synced line date
  // (string comparison is safe/UTC-agnostic for YYYY-MM-DD) — display-only,
  // no internal provenance marker (this page is also what buyers screenshot
  // for vendors, so keep it consistent with the PDF's plain date).
  const erpDeliveryDate = po.line_items.reduce<string | undefined>((earliest, li) => {
    if (!li.planned_arrival_date) return earliest
    return !earliest || li.planned_arrival_date < earliest ? li.planned_arrival_date : earliest
  }, undefined)
  const expectedDeliveryDisplay = po.expected_delivery
    ? formatDate(po.expected_delivery)
    : erpDeliveryDate
      ? formatDate(erpDeliveryDate)
      : '—'

  return (
    <div className={cn('flex flex-col gap-6', canApprove && 'pb-16')}>
      {/* Page header */}
      <div className="rounded-lg border border-line bg-surface px-6 py-4">
        <div className="flex items-center gap-3 mb-3">
          <BackLink to="/po">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4" />
              Back to PO List
            </Button>
          </BackLink>
        </div>
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-semibold text-fg">{po.number}</h1>
              <StatusBadge status={po.status as DocumentStatus} />
              {po.source === 'nc' && (
                <span title="This PO was mirrored from NC ERP — read-only origin context">
                  <Badge variant="neutral">Synced from NC ERP</Badge>
                </span>
              )}
              {po.is_prepaid && (
                <Badge variant="warning">
                  Prepayment
                </Badge>
              )}
            </div>
            <p className="mt-1 text-fg-secondary">{po.title} — {po.vendor_name}</p>
            {po.pr_number && (
              <Link
                to={`/pr/${po.pr_id}`}
                className="mt-0.5 inline-flex items-center gap-1 text-xs text-fg-muted hover:text-primary-600"
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
                className="border-danger-line text-danger-fg hover:bg-danger-soft"
                onClick={() => confirmDlg.ask({
                  title: 'Withdraw PO',
                  subtitle: po.number,
                  message: `Withdraw ${po.number}? This will cancel the PO.`,
                  confirmLabel: 'Withdraw',
                  // Closes at once; a refusal shows beside the button.
                  onConfirm: () => poWithdraw.mutate({ action: 'cancel' }),
                })}
                disabled={poWithdraw.isPending}
              >
                Withdraw
              </Button>
            )}
            {poWithdraw.isError && (
              <span className="text-xs text-danger-fg">
                {poWithdraw.error instanceof Error ? poWithdraw.error.message : 'Could not withdraw the PO'}
              </span>
            )}
            {canPlaceOrder && (
              <Button size="sm"
                onClick={() => setShowPlaceOrder(true)}
               
              >
                <ShoppingCart className="h-3.5 w-3.5" />
                Place Order
              </Button>
            )}
            {canCreateGr && (
              <Button variant="success" size="sm"
                onClick={() => navigate(`/gr/new?poId=${po.id}`)}
               
              >
                <Warehouse className="h-3.5 w-3.5" />
                {po.type === 4 ? 'Confirm Service' : po.type === 6 ? 'Confirm Completion' : 'Create GR'}
              </Button>
            )}
            {canCreatePa && (
              <Button variant="outline" size="sm"
                onClick={() => navigate(`/pa/new?poId=${po.id}`)}
               
              >
                <CreditCard className="h-3.5 w-3.5" />
                Create PA
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Main content + timeline */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left: tabs */}
        <div className="lg:col-span-8 flex flex-col gap-4">
          {/* Tab bar */}
          <Tabs items={TABS.map((t) => ({ key: t, label: t }))} value={activeTab} onChange={setActiveTab} />

          {/* Sign-off — NC imports only; the panel renders nothing otherwise.
              Kept above the tabs rather than inside Details: while a sign-off
              is open it is the only thing anyone opening this PO is here to
              do. */}
          {activeTab === 'Details' && (
            <PoSignoffPanel poId={po.id} source={po.source ?? null} />
          )}

          {/* Freight & Customs — every PO (the panel hides itself on a milk
              order with no Incoterms and nothing linked, and on an EPMS PO with
              nothing linked). Right under the sign-off, above the order
              details: "are this order's fee invoices in?" is what a
              raw-material PO is opened to find out. */}
          {activeTab === 'Details' && (
            <PoFeePanel poId={po.id} canEditDetails={canEditImported} />
          )}

          {/* Details Tab */}
          {activeTab === 'Details' && (
            <div className="rounded-xl bg-surface shadow-sm p-6 flex flex-col gap-6">
              <section>
                <h2 className="text-sm font-semibold text-fg-muted mb-3">Details</h2>
                <dl className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2 text-sm">
                  {([
                    ['Procurement Type', `Type ${po.type} — ${TYPE_LABELS[po.type]}`],
                    ['Vendor', po.vendor_name],
                    ['Currency', po.currency],
                    // ISS-0087: the PO's own terms, or the vendor's on a PO that has none.
                    ['Payment Terms', po.payment_terms
                      ? formatPaymentTerms(po.payment_terms)
                      : po.vendor_payment_terms ? `${formatPaymentTerms(po.vendor_payment_terms)} (vendor's terms)` : '—'],
                    ['Budget Code', po.budget_code ?? '—'],
                    ['Created', formatDate(po.created_at)],
                    ['Expected Delivery', expectedDeliveryDisplay],
                    // PO-IMP-002: an NC PO's Delivery Address defaults to the company
                    // delivery address until the buyer edits it — Edit Details
                    // pre-fills it, so the detail shows it too (ISS-0183). Display
                    // only; nothing is written to the PO.
                    ['Delivery Address', po.delivery_address
                      || (po.source === 'nc' && config?.delivery_address
                        ? `${config.delivery_address} (company default)` : '—')],
                    ['PR Reference', po.pr_number || '—'],
                  ] as [string, string][]).map(([label, value]) => (
                    <div key={label} className="flex flex-col gap-0.5">
                      <dt className="text-xs font-medium text-fg-muted">{label}</dt>
                      <dd className="text-fg">{value}</dd>
                    </div>
                  ))}
                  {(po.incoterms_code || po.incoterms) && (
                    <div className="flex flex-col gap-0.5">
                      <dt className="text-xs font-medium text-fg-muted">Incoterms</dt>
                      <dd className="text-fg">
                        {[po.incoterms_code, po.incoterms].filter(Boolean).join(' ')}
                      </dd>
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
                      <dt className="text-xs font-medium text-fg-muted">Buyer Notes</dt>
                      <dd className="whitespace-pre-wrap text-fg">
                        {po.buyer_notes || po.notes}
                      </dd>
                    </div>
                  )}
                  {po.source === 'nc' && po.notes && (
                    <div className="flex flex-col gap-0.5 sm:col-span-2">
                      <dt className="text-xs font-medium text-fg-muted">NC Sync Notes</dt>
                      <dd className="whitespace-pre-wrap text-fg">{po.notes}</dd>
                    </div>
                  )}
                  <div className="flex flex-col gap-0.5">
                    <dt className="text-xs font-medium text-fg-muted">Prepayment PO</dt>
                    <dd>
                      {po.is_prepaid ? (
                        <Badge variant="warning">
                          Yes — Prepayment required
                        </Badge>
                      ) : (
                        <span className="text-sm text-fg">No</span>
                      )}
                    </dd>
                  </div>
                </dl>
              </section>

              {/* Financial summary */}
              <section>
                <h2 className="text-sm font-semibold text-fg-muted mb-3">Financial Summary</h2>
                <div className="rounded-lg border border-line bg-surface-subtle px-5 py-4 flex flex-col gap-2 max-w-sm">
                  <div className="flex justify-between text-sm text-fg-secondary">
                    <span>Subtotal</span>
                    <span className="amount">{formatAmount(po.subtotal, po.currency)}</span>
                  </div>
                  <div className="flex justify-between text-sm text-fg-secondary">
                    <span>{TAX_LABELS[Number(po.tax_rate)] ?? `${Math.round(Number(po.tax_rate) * 100)}% Tax`}</span>
                    <span className="amount">{formatAmount(po.tax_amount, po.currency)}</span>
                  </div>
                  <div className="border-t border-line-strong mt-1 pt-2 flex justify-between">
                    <span className="text-sm font-semibold text-fg">Total ({po.currency})</span>
                    <span className="amount text-base font-semibold text-fg">{formatAmount(po.total, po.currency)}</span>
                  </div>
                </div>
              </section>

              {/* Line Items */}
              <section>
                <h2 className="text-sm font-semibold text-fg-muted mb-3">
                  Line Items <span className="normal-case font-normal text-fg-muted">({po.line_items.length})</span>
                </h2>
                <div className="rounded-lg border border-line overflow-hidden">
                  <div className="overflow-x-auto">
                    <Table>
                      <THead>
                        <Tr className="bg-surface-subtle">
                          <Th className="w-10">#</Th>
                          <Th>Description</Th>
                          {hasMaterial && (
                            <Th className="w-32">Material ID</Th>
                          )}
                          {/* Always shown for consistency with the Create PO page, which always exposes this input. */}
                          <Th className="w-36">Supplier Item ID</Th>
                          <Th align="right" className="w-20">Qty</Th>
                          <Th className="w-20">Unit</Th>
                          {showSample && (
                            <Th className="w-24">Sample</Th>
                          )}
                          {showDeliveryDate && (
                            <Th className="w-28">Delivery Date</Th>
                          )}
                          <Th align="right" className="w-32">Unit Price</Th>
                          <Th align="right" className="w-32">Line Total</Th>
                          <Th align="center" className="w-28">Received</Th>
                        </Tr>
                      </THead>
                      <TBody>
                        {po.line_items.map((item, i) => {
                          const pct = item.qty > 0 ? Math.min((item.received_qty / item.qty) * 100, 100) : 0
                          return (
                            <Tr
                              key={item.id}
                              className={`border-b border-line-subtle last:border-0 ${i % 2 === 1 ? 'bg-surface-subtle/50' : 'bg-surface'}`}
                            >
                              <Td className="text-xs text-fg-muted">{i + 1}</Td>
                              <Td className="text-fg">{item.description}</Td>
                              {hasMaterial && (
                                <Td className="font-mono text-xs text-fg-secondary">{item.material_id || '—'}</Td>
                              )}
                              <Td className="font-mono text-xs text-fg-secondary">{item.supplier_item_id || '—'}</Td>
                              <Td align="right" className="font-mono text-fg">{formatQty(item.qty)}</Td>
                              <Td className="text-fg-muted">{item.unit}</Td>
                              {showSample && (
                                <Td className="text-fg-muted">{item.sample || '—'}</Td>
                              )}
                              {showDeliveryDate && (
                                <Td className="text-fg-muted">
                                  {item.planned_arrival_date ? formatDate(item.planned_arrival_date) : '—'}
                                </Td>
                              )}
                              <Td align="right" className="amount text-fg">{formatUnitPrice(item.unit_price, po.currency)}</Td>
                              <Td align="right" className="amount font-semibold text-fg">{formatAmount(item.line_total, po.currency)}</Td>
                              <Td>
                                <div className="flex flex-col items-center gap-1">
                                  <span className="text-xs text-fg-secondary">{formatQty(item.received_qty)} / {formatQty(item.qty)}</span>
                                  <div className="h-1.5 w-full rounded-full bg-surface-strong">
                                    <div
                                      style={{ width: `${pct}%` }}
                                      className={cn(
                                        'h-full rounded-full transition-all',
                                        pct >= 100 ? 'bg-success-solid' : pct > 0 ? 'bg-primary-500' : 'bg-line-strong'
                                      )}
                                    />
                                  </div>
                                </div>
                              </Td>
                            </Tr>
                          )
                        })}
                      </TBody>
                      <TFoot>
                        <Tr className="border-t-2 bg-surface-subtle">
                          <Td align="right" colSpan={6 + (hasMaterial ? 1 : 0) + (showSample ? 1 : 0) + (showDeliveryDate ? 1 : 0)} className="text-xs font-semibold text-fg-muted">
                            Subtotal
                          </Td>
                          <Td align="right" className="amount text-base font-semibold text-fg">
                            {formatAmount(po.subtotal, po.currency)}
                          </Td>
                          <Td />
                        </Tr>
                      </TFoot>
                    </Table>
                  </div>
                  <div className="border-t border-line px-4 py-3 flex justify-end gap-8 text-sm bg-surface-subtle">
                    <span className="text-fg-muted">{TAX_LABELS[Number(po.tax_rate)] ?? 'Tax'}: <span className="amount text-fg-secondary">{formatAmount(po.tax_amount, po.currency)}</span></span>
                    <span className="font-semibold text-fg">Total: <span className="amount">{formatAmount(po.total, po.currency)}</span></span>
                  </div>
                </div>
              </section>
            </div>
          )}

          {/* Linked GRs Tab */}
          {activeTab === 'Linked GRs' && (
            <div className="rounded-xl bg-surface shadow-sm p-6">
              <h2 className="text-sm font-semibold text-fg-muted mb-4">Linked Goods Receipts</h2>
              {grsLoading ? (
                <div className="flex items-center justify-center py-12 text-fg-muted">
                  <Loader2 className="h-5 w-5 animate-spin" />
                </div>
              ) : linkedGrs.length > 0 ? (
                <ul className="flex flex-col gap-2">
                  {linkedGrs.map((gr) => (
                    <li key={gr.id}>
                      <Link
                        to={`/gr/${gr.id}`}
                        className="flex w-full items-center gap-3 rounded-lg border border-line bg-surface-subtle px-3 py-2.5 text-left text-sm transition-colors hover:border-primary-300 hover:bg-primary-50 group"
                      >
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-100 group-hover:bg-primary-200 shrink-0">
                          <Warehouse className="h-4 w-4 text-primary-600" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-sm font-semibold text-fg-secondary truncate">{gr.number}</span>
                            <StatusBadge status={GR_STATUS_DOC[gr.status]} label={GR_STATUS_LABELS[gr.status]} />
                          </div>
                          <p className="text-xs text-fg-muted mt-0.5 truncate">
                            {gr.gr_type === 'physical' ? 'Physical' : 'Service'} · Received {formatDate(gr.received_at)}
                          </p>
                        </div>
                        <ExternalLink className="h-4 w-4 shrink-0 text-fg-faint group-hover:text-primary-400" />
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="flex flex-col items-center justify-center py-12 text-center">
                  <Factory aria-hidden className="mb-3 h-8 w-8 stroke-[1.5] text-fg-faint" />
                  <p className="text-sm text-fg-muted">No goods receipts linked to this PO yet.</p>
                  <p className="text-xs text-fg-faint mt-1">They will appear here once received.</p>
                </div>
              )}
            </div>
          )}

          {/* Attachments Tab */}
          {activeTab === 'Attachments' && (
            <div className="rounded-xl bg-surface shadow-sm p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold text-fg-muted">Attachments</h2>
                {['approved', 'issued', 'partially_received', 'fully_received', 'closed', 'nc_pending', 'nc_milk'].includes(po.status) && (
                  <Button variant="secondary" size="sm"
                    type="button"
                    onClick={() => regeneratePdf.mutate()}
                    disabled={regeneratePdf.isPending}
                   
                    title="Generate the approved-PO PDF and attach it (replaces the existing one)"
                  >
                    <RotateCcw className={`h-3.5 w-3.5 ${regeneratePdf.isPending ? 'animate-spin' : ''}`} />
                    {regeneratePdf.isPending ? 'Generating…' : 'Regenerate PDF'}
                  </Button>
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
                  <p className="text-sm text-fg-muted">
                    {po.status === 'approved' ? 'PDF is being generated…' : 'No attachments yet'}
                  </p>
                  {po.status !== 'approved' && (
                    <Button variant="link" onClick={handleDownloadPdf} className="mt-1 text-xs">
                      Generate preview PDF
                    </Button>
                  )}
                </div>
              )}
            </div>
          )}

          {/* History Tab */}
          {activeTab === 'History' && (
            <div className="rounded-xl bg-surface shadow-sm p-6">
              <h2 className="text-sm font-semibold text-fg-muted mb-4">Audit History</h2>
              {events && events.length > 0 ? (
                <ul className="flex flex-col gap-3">
                  {events.map((e) => {
                    const line = historyLine(e, workflowSteps)
                    return (
                      <li key={e.id} className="flex gap-3 text-sm">
                        <span className="text-xs text-fg-muted whitespace-nowrap pt-0.5">{formatDate(e.created_at)}</span>
                        <div>
                          <span className="font-medium text-fg capitalize">{line.action}</span>
                          <span className="text-fg-muted ml-1">{line.role}</span>
                          {line.actorName && <span className="text-fg-muted ml-1">— {line.actorName}</span>}
                          {line.note && <p className="text-fg-muted text-xs mt-0.5 italic">{line.note}</p>}
                        </div>
                      </li>
                    )
                  })}
                </ul>
              ) : (
                <div className="text-sm text-fg-muted text-center py-8">No history yet</div>
              )}
            </div>
          )}
        </div>

        {/* Right: approval timeline */}
        <div className="lg:col-span-4">
          <div className="rounded-xl bg-surface shadow-sm p-5 sticky top-6">
            <h2 className="text-sm font-semibold text-fg-muted mb-4">Approval Timeline</h2>
            <ApprovalTimeline
              steps={approvalSteps}
              // See PrDetailPage: a draft's step 0 also renders as "current".
              onSendReminder={APPROVABLE_STATUSES.includes(po.status) ? sendReminder : undefined}
              reminder={reminder}
            />

            {/* Place Order panel for procurement officer */}
            {canPlaceOrder && (
              <div className="mt-5 border-t border-line-subtle pt-4 flex flex-col gap-2">
                <p className="text-xs font-medium text-fg-muted mb-1">Ready to Place Order</p>
                <Button
                  onClick={() => setShowPlaceOrder(true)}
                  className="w-full"
                >
                  <ShoppingCart className="h-4 w-4" />
                  Place Order
                </Button>
              </div>
            )}

            {/* Document Chain */}
            <DocumentChainTree currentType="po" id={po.id} />
          </div>
        </div>
      </div>

      {/* Fixed bottom approver action bar */}
      {canApprove && (
        <div className="fixed bottom-0 left-60 right-0 z-10 bg-surface border-t border-line px-6 py-3 flex items-center justify-between shadow-up max-md:left-0">
          <p className="text-sm text-fg-muted">
            Reviewing <span className="font-medium text-fg">{po.number}</span>
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
                <div className="absolute right-0 bottom-full mb-1 w-44 rounded-lg border border-line bg-surface py-1 shadow-lg z-20">
                  <MenuItem tone="warning"
                    onClick={() => { setPendingAction('return'); setMoreOpen(false) }}
                   
                  >
                    <RotateCcw className="h-4 w-4" /> Return for Revision
                  </MenuItem>
                  <MenuItem tone="danger"
                    onClick={() => { setPendingAction('reject'); setMoreOpen(false) }}
                   
                  >
                    <XCircle className="h-4 w-4" /> Reject
                  </MenuItem>
                </div>
              )}
            </div>
            <Button variant="success"
              onClick={() => setPendingAction('approve')}
             
            >
              <CheckCircle2 className="h-4 w-4" /> Approve
            </Button>
          </div>
        </div>
      )}

      {confirmDlg.dialog}

      {/* Approval modal */}
      {pendingAction && (
        <ApprovalModal
          action={pendingAction}
          poNumber={po.number}
          onConfirm={(comment) => handleConfirm(pendingAction, comment)}
          onClose={() => { setPendingAction(null); poAction.reset() }}
          isPending={poAction.isPending}
          error={poAction.isError ? errorMessage(poAction.error, 'Purchase order action failed') : null}
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
