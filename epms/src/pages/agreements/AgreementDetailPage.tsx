import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { createPortal } from 'react-dom'
import {
  ArrowLeft, CheckCircle2, RotateCcw, XCircle, MessageSquare, X, FileText, AlertTriangle, ExternalLink, Pencil,
  CreditCard, Upload, Paperclip, Info, Plus,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge, StatusBadge } from '@/components/ui/badge'
import { ApprovalTimeline } from '@/components/pr/ApprovalTimeline'
import { ScheduleTable } from '@/components/agreements/ScheduleTable'
import { ReceiptTable } from '@/components/agreements/ReceiptTable'
import { formatAmount, formatDate, formatBytes, cn } from '@/lib/utils'
import type { ApprovalStep, DocumentStatus } from '@/types'
import { useAuthStore } from '@/stores/auth.store'
import { useConfig, useRolePermissions } from '@/hooks/useConfig'
import { useAgreement, useAgreementAction, useAgreementSchedule } from '@/hooks/useAgreements'
import { useAgreementReceipts } from '@/hooks/useAgreementReceipts'
import { DocumentChainTree } from '@/components/shared/DocumentChainTree'
import { useDepartments } from '@/hooks/useDepartments'
import { useTasks } from '@/hooks/useTasks'
import { useInvoices } from '@/hooks/useInvoices'
import { useUserDirectory } from '@/hooks/useUsers'
import {
  useAgreementAttachments, useUploadAgreementAttachment, useDeleteAgreementAttachment,
} from '@/hooks/useAgreementAttachments'
import { agreementAttachmentService } from '@/services/agreementAttachments'
import type { AgreementStatus, AgreementType } from '@/services/agreement'
import type { InvoiceStatus } from '@/services/invoices'
import { isAgreementAdmissible } from '@/lib/agreements'

const TYPE_LABELS: Record<AgreementType, string> = {
  house_account: 'House Account',
  recurring: 'Recurring',
  milestone: 'Milestone',
}

// Statuses in which the agreement is still awaiting the current approver's
// decision. Mirrors PoDetailPage's APPROVABLE_STATUSES = ['submitted', 'in_review'].
// 'submitted' matters here: the engine's submit branch (engine.py:911) sets
// status to "submitted" and creates the step-0 approve_agr task WITHOUT ever
// touching "in_review" — that only gets written after the FIRST approve
// (engine.py:1018), when a next step remains. Approval-meta confirms this is
// the correct set: _DOC_META["agr"]["valid_approve"] = ("submitted", "in_review")
// (approval-api/app/crud/engine.py:102).
const APPROVABLE_STATUSES: AgreementStatus[] = ['submitted', 'in_review']

// Fallback used only if this CompanyConfig row predates workflow_defs.agr —
// mirrors AGR_WORKFLOW's seed default in epms-api/app/schemas/agreement.py.
const DEFAULT_AGR_WORKFLOW: { id: string; label: string }[] = [
  { id: 'dept_manager', label: 'Department Manager' },
  { id: 'procurement_manager', label: 'Procurement Manager' },
  { id: 'finance_manager', label: 'Finance Manager' },
]

const INVOICE_STATUS_CFG: Record<InvoiceStatus, { label: string; variant: 'neutral' | 'warning' | 'info' | 'success' | 'danger' }> = {
  unmatched: { label: 'Unmatched', variant: 'warning' },
  matched: { label: 'Matched', variant: 'success' },
  exception: { label: 'Exception', variant: 'danger' },
  match_review: { label: 'Pending Review', variant: 'info' },
  approved: { label: 'Approved', variant: 'success' },
  paid: { label: 'Paid', variant: 'neutral' },
}

// Approval-step timeline, adapted from PoDetailPage::buildWorkflowSteps. There
// is no per-agreement events endpoint (unlike PO), so this shows step
// position only — no actor names.
function buildWorkflowSteps(
  nodes: { id: string; label: string }[],
  status: AgreementStatus,
  stepIdx: number,
): ApprovalStep[] {
  const created: ApprovalStep = {
    id: 'created',
    role: 'Creator',
    status: 'completed',
    action: 'Created',
    channel: 'Web',
  }
  const approvalNodes: ApprovalStep[] = nodes.map((node, i) => {
    let s: ApprovalStep['status']
    if (status === 'draft') {
      s = 'pending'
    } else if (['active', 'expired', 'closed'].includes(status)) {
      s = 'completed'
    } else if (status === 'cancelled') {
      s = i < stepIdx ? 'completed' : i === stepIdx ? 'skipped' : 'pending'
    } else {
      // in_review, returned
      s = i < stepIdx ? 'completed' : i === stepIdx ? 'current' : 'pending'
    }
    return { id: node.id, role: node.label, status: s }
  })
  return [created, ...approvalNodes]
}

// Consumed vs. Not-to-Exceed bar. Both fields arrive as JSON strings —
// Number()-coerce before any arithmetic/comparison. The ceiling only warns;
// it never disables any action here (submit/approve/match all stay live).
function NteBar({ consumedAmount, notToExceed, currency }: { consumedAmount: string; notToExceed: string | null; currency: string }) {
  const consumed = Number(consumedAmount)
  const ceiling = notToExceed ? Number(notToExceed) : null
  const pct = ceiling && ceiling > 0 ? Math.min((consumed / ceiling) * 100, 100) : null
  const overCeiling = ceiling !== null && consumed > ceiling

  return (
    <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-5 py-4 flex flex-col gap-2 max-w-sm">
      <div className="flex justify-between text-sm">
        <span className="text-neutral-700">Consumed</span>
        <span className={cn('amount font-semibold', overCeiling ? 'text-danger-600' : 'text-neutral-900')}>
          {formatAmount(consumed, currency)}
        </span>
      </div>
      <div className="flex justify-between text-sm">
        <span className="text-neutral-700">Not to Exceed</span>
        <span className="amount text-neutral-900">{ceiling !== null ? formatAmount(ceiling, currency) : 'No ceiling'}</span>
      </div>
      {ceiling !== null && (
        <div className="h-2 w-full rounded-full bg-neutral-200 mt-1">
          <div
            style={{ width: `${pct ?? 0}%` }}
            className={cn(
              'h-full rounded-full transition-all',
              overCeiling ? 'bg-danger-500' : (pct ?? 0) >= 90 ? 'bg-warning-500' : 'bg-primary-500'
            )}
          />
        </div>
      )}
      {overCeiling && (
        <div className="flex items-center gap-1.5 text-xs font-semibold text-danger-600 mt-0.5">
          <AlertTriangle className="h-3.5 w-3.5" />
          Over ceiling — this is a warning only, no action is blocked.
        </div>
      )}
    </div>
  )
}

// ─── Approval action modal ───────────────────────────────────────────────────

type ApprovalAction = 'approve' | 'return'

function ApprovalModal({ action, agreementNumber, onConfirm, onClose, isPending }: {
  action: ApprovalAction
  agreementNumber: string
  onConfirm: (comment: string) => void
  onClose: () => void
  isPending: boolean
}) {
  const [comment, setComment] = useState('')
  const needsComment = action === 'return'
  const canSubmit = !needsComment || comment.trim().length > 0

  const config = {
    approve: {
      title: 'Approve Agreement',
      label: 'Approve',
      icon: <CheckCircle2 className="h-5 w-5 text-success-600" />,
      bgIcon: 'bg-success-50',
      btn: 'bg-success-600 hover:bg-success-700 text-white',
      commentLabel: 'Comment (optional)',
      placeholder: 'Add an optional comment…',
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
              <p className="text-xs text-neutral-500">{agreementNumber}</p>
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

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AgreementDetailPage() {
  const { id } = useParams()
  const { data: agreement, isLoading } = useAgreement(id ?? '')
  const { data: config } = useConfig()
  const { data: deptData } = useDepartments()
  const departments = deptData?.items ?? []
  const agreementAction = useAgreementAction(id ?? '')
  const { user } = useAuthStore()
  const [pendingAction, setPendingAction] = useState<ApprovalAction | null>(null)

  const { data: invoicesData, isLoading: invoicesLoading } = useInvoices(
    agreement ? { agreement_id: agreement.id } : undefined,
    Boolean(agreement),
  )
  const invoices = invoicesData?.items ?? []
  const legacySettlementCount = invoices.filter((inv) => inv.legacy_settlement).length
  // "Matched-but-unpaid" — same filter PaCreatePage's agreement mode uses to
  // populate its invoice picker. If this is 0, the create page would load to
  // an empty Step 2 with nothing to select, so gate the button on it here.
  const payableInvoiceCount = invoices.filter((inv) => inv.status === 'matched').length

  const perms = useRolePermissions().data?.permissions
  const canWrite = user?.role === 'system_admin' || !!perms?.['epms.agreement.write']
  // Driven by the Access Control Matrix (epms.pa.write) — the same key gates
  // POST /pa, matching PaListPage's canCreate convention — plus the two
  // preconditions POST /pa's agreement route enforces server-side (admission
  // window, at least one matched invoice), so the button isn't offered when
  // clicking it would just 422.
  const canCreatePaPerm = user?.role === 'system_admin' || !!perms?.['epms.pa.write']
  const canCreatePa =
    canCreatePaPerm && !!agreement && isAgreementAdmissible(agreement) && payableInvoiceCount > 0
  // Gates the pickup-receipt AP Approve/Reject buttons — mirrors the backend's
  // ApDep = require_permission("epms.invoice.match") on POST .../ap-review
  // (epms-api/app/api/v1/agreement_receipts.py), the same permission that gates
  // invoice match review elsewhere (InvoiceDetailPage/InvoiceListPage).
  const canApReviewReceipt = user?.role === 'system_admin' || !!perms?.['epms.invoice.match']
  // Recording a receipt is a SEPARATE permission from editing the agreement
  // itself (identity 0007_receipt_write_perm) — mirrors the backend's
  // ReceiptRecordDep = require_permission("epms.agreement.receipt.write") on
  // the create/update/void receipt routes and the receipt-attachment write
  // routes (agreement_receipts.py / agreement_receipt_attachments.py).
  // Deliberately NOT folded into `canWrite`: someone who can tick "Record
  // Agreement Receipts" in the Access Control matrix should not thereby gain
  // the ability to edit the agreement's vendor/terms/schedule.
  const canRecordReceipt = user?.role === 'system_admin' || !!perms?.['epms.agreement.receipt.write']
  // Matches epms-api/app/crud/agreement.py:13 EDITABLE_STATUSES = ("draft", "returned") —
  // both are submit-able AND edit-able. 'returned' must have both, or the
  // Return action is a permanent dead end: the creator can neither fix nor
  // resubmit what came back.
  const canSubmit = canWrite && !!agreement && ['draft', 'returned'].includes(agreement.status)
  const canEdit = canWrite && !!agreement && ['draft', 'returned'].includes(agreement.status)
  // Matches approval-api's _DOC_META["agr"]["valid_cancel"] = ("draft", "returned", "submitted")
  // (engine.py:104) — NOT "in_review": once a human has already acted (the
  // first approve moves it to in_review), cancel is no longer offered.
  const canCancel = canWrite && !!agreement && ['draft', 'returned', 'submitted'].includes(agreement.status)

  // Approval gating follows the open-task convention used across PR/PO/PA:
  // the Approve button only appears when the current user holds an active
  // approve_agr task for THIS agreement, never from a role guess.
  const { data: myTasks } = useTasks({ is_completed: false })
  const hasApproveTask = !!agreement && !!(myTasks?.items ?? []).some(
    (t) => t.document_id === agreement.id && t.type === 'approve_agr'
  )
  const canApprove =
    !!user && !!agreement && APPROVABLE_STATUSES.includes(agreement.status) && hasApproveTask

  // house_account agreements have no schedule rows at all (epms-api's
  // ensure_period_rows returns 0 for non-recurring types, and milestone/period
  // rows are never generated for house_account) — skip the fetch entirely
  // rather than asking for a query that will always come back empty.
  const { data: scheduleData } = useAgreementSchedule(
    agreement && agreement.agreement_type !== 'house_account' ? agreement.id : ''
  )
  const scheduleRows = scheduleData?.items ?? []
  const periodRows = scheduleRows.filter((r) => r.schedule_type === 'period')
  const milestoneRows = scheduleRows.filter((r) => r.schedule_type === 'milestone')
  const periodReceived = periodRows.filter((r) => r.status === 'received').length
  const periodOverdue = periodRows.filter((r) => r.status === 'overdue').length
  const milestoneInvoiced = milestoneRows.filter((r) => r.status === 'received').length
  const milestoneAmountSum = milestoneRows.reduce(
    (sum, r) => sum + (r.expected_amount !== null ? Number(r.expected_amount) : 0), 0
  )

  // GET /users/directory (unlike GET /users) is open to any authenticated
  // user — this is what lets the "Confirmed" column resolve accepted_by to a
  // name for the dept_manager who actually holds the confirm task, not just
  // for a system_admin viewer.
  const { data: usersData } = useUserDirectory()

  // The agreement's responsible person. Recorded on the create/edit form and
  // acted on by the backend — the NTE and expiry alerts are addressed to them
  // (tasks/agreement_overdue.py), and on a recurring agreement they are the
  // assignee of every period-confirmation task (crud/agreement_schedule.py
  // _confirm_assignee) — but the detail page never showed it, so the one
  // person carrying the agreement could not see they carried it.
  //
  // Resolved off the directory already fetched above rather than a second
  // request. That directory lists ACTIVE users only, so an owner_id that
  // resolves to nothing is a deactivated (or deleted) account — a materially
  // different fact from "nobody owns this", and one worth saying out loud:
  // the alerts and confirmation tasks are still being addressed to that
  // account.
  const owner = agreement?.owner_id
    ? usersData?.items?.find((u) => u.id === agreement.owner_id)
    : undefined
  const ownerLabel =
    !agreement?.owner_id ? 'Not assigned'
    : owner ? owner.full_name
    : usersData ? 'Assigned to a deactivated account'
    : '…'

  // house_account is the only agreement_type with pickup receipts at all —
  // recurring/milestone settle against the payment schedule instead. Skip
  // the fetch entirely for the other two types, same convention as the
  // schedule query above.
  const { data: receiptsData } = useAgreementReceipts(
    agreement && agreement.agreement_type === 'house_account' ? agreement.id : ''
  )
  const receipts = receiptsData?.items ?? []

  const { data: attachmentsData, isLoading: attachmentsLoading } = useAgreementAttachments(agreement?.id ?? '')
  const attachments = attachmentsData ?? []
  const uploadAttachment = useUploadAgreementAttachment(agreement?.id ?? '')
  const deleteAttachment = useDeleteAgreementAttachment(agreement?.id ?? '')

  const handleConfirm = (action: ApprovalAction, comment: string) => {
    agreementAction.mutate(
      { action, comment: comment || undefined },
      { onSuccess: () => setPendingAction(null) }
    )
  }

  if (isLoading) return <div className="p-8 text-center text-neutral-400">Loading…</div>

  if (!agreement) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <div className="text-5xl mb-4">🔍</div>
        <h2 className="text-xl font-semibold text-neutral-700">Agreement Not Found</h2>
        <p className="mt-2 text-sm text-neutral-400">The agreement you're looking for doesn't exist.</p>
        <Link to="/agreements" className="mt-4">
          <Button variant="secondary">Back to Agreements</Button>
        </Link>
      </div>
    )
  }

  const workflowNodes = config?.workflow_defs?.agr ?? DEFAULT_AGR_WORKFLOW
  const approvalSteps = buildWorkflowSteps(workflowNodes, agreement.status, agreement.approval_step_idx ?? 0)
  const department = departments.find((d) => d.id === agreement.department_id)

  return (
    <div className={cn('flex flex-col gap-6', canApprove && 'pb-16')}>
      {/* Page header */}
      <div className="rounded-lg border border-neutral-200 bg-white px-6 py-4">
        <div className="flex items-center gap-3 mb-3">
          <Link to="/agreements">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4" />
              Back to Agreements
            </Button>
          </Link>
        </div>
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-bold text-neutral-900">{agreement.number}</h1>
              <StatusBadge status={agreement.status as DocumentStatus} />
              <Badge variant="neutral">{TYPE_LABELS[agreement.agreement_type]}</Badge>
              {legacySettlementCount > 0 && (
                <span title="Invoices settled against this agreement with no receipt evidence — recorded deliberately, with a stated reason, when no receipt exists to reconcile against.">
                  <Badge variant="warning">{legacySettlementCount} settled without receipt</Badge>
                </span>
              )}
            </div>
            <p className="mt-1 text-neutral-600">{agreement.title} — {agreement.vendor_name}</p>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            {canEdit && (
              <Link to={`/agreements/${agreement.id}/edit`}>
                <Button variant="secondary" size="sm">
                  <Pencil className="h-3.5 w-3.5" />
                  Edit
                </Button>
              </Link>
            )}
            {canSubmit && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => agreementAction.mutate({ action: 'submit' })}
                disabled={agreementAction.isPending}
              >
                Submit for Approval
              </Button>
            )}
            {canCancel && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => agreementAction.mutate({ action: 'cancel' })}
                disabled={agreementAction.isPending}
              >
                <XCircle className="h-3.5 w-3.5" />
                Cancel
              </Button>
            )}
            {canCreatePa && (
              <Link to={`/pa/new?agreement_id=${agreement.id}`}>
                <Button size="sm">
                  <CreditCard className="h-3.5 w-3.5" />
                  Create PA
                </Button>
              </Link>
            )}
          </div>
        </div>
      </div>

      {/* Main content + timeline */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left: details */}
        <div className="lg:col-span-8 flex flex-col gap-6">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-6">
            <section>
              <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-3">Terms</h2>
              <dl className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2 text-sm">
                {([
                  ['Vendor', agreement.vendor_name],
                  ['Contract No.', agreement.contract_no || '—'],
                  ['Vendor Contact Email', agreement.contact_email || '—'],
                  ['Vendor Reference', agreement.vendor_reference || '—'],
                  ['Valid From', formatDate(agreement.valid_from)],
                  ['Valid To', formatDate(agreement.valid_to)],
                  ['Grace Days', String(agreement.grace_days)],
                  ['Currency', agreement.currency],
                  ['Tax', agreement.tax_code ? `${agreement.tax_code} (${(Number(agreement.tax_rate ?? 0) * 100).toFixed(2)}%)` : '—'],
                  ['Department', department?.name ?? '—'],
                  ['Owner', ownerLabel],
                  ['Budget Code', agreement.budget_code || '—'],
                  ['Created', formatDate(agreement.created_at)],
                ] as [string, string][]).map(([label, value]) => (
                  <div key={label} className="flex flex-col gap-0.5">
                    <dt className="text-xs font-medium text-neutral-500">{label}</dt>
                    <dd className="text-neutral-900">{value}</dd>
                  </div>
                ))}
                <div className="flex flex-col gap-0.5 sm:col-span-2">
                  <dt className="text-xs font-medium text-neutral-500">Notes</dt>
                  <dd className="text-neutral-900">{agreement.notes || '—'}</dd>
                </div>
              </dl>
            </section>

            <section>
              <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-3">Consumed / Not to Exceed</h2>
              <NteBar consumedAmount={agreement.consumed_amount} notToExceed={agreement.not_to_exceed} currency={agreement.currency} />
            </section>
          </div>

          {/* Payment schedule — recurring (periods) or milestone (stages).
              house_account has no schedule rows at all (crud/agreement_schedule.py
              ensure_period_rows only fires for recurring_type; milestone rows are
              never generated for house_account either), so nothing renders here. */}
          {agreement.agreement_type === 'recurring' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
              <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
                  Payment Schedule <span className="normal-case font-normal text-neutral-400">({periodRows.length})</span>
                </h2>
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-neutral-500">{periodReceived} of {periodRows.length} periods received</span>
                  {periodOverdue > 0 && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-danger-50 border border-danger-200 px-2 py-0.5 font-semibold text-danger-700">
                      <AlertTriangle className="h-3 w-3" />
                      {periodOverdue} overdue
                    </span>
                  )}
                </div>
              </div>
              <ScheduleTable
                scheduleType="period"
                rows={periodRows}
                agreementId={agreement.id}
                agreementNumber={agreement.number}
                currency={agreement.currency}
                myOpenTasks={myTasks?.items ?? []}
                users={usersData?.items}
              />
            </div>
          )}

          {agreement.agreement_type === 'milestone' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
              <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
                  Payment Schedule <span className="normal-case font-normal text-neutral-400">({milestoneRows.length})</span>
                </h2>
                <div className="text-xs text-neutral-500">
                  {milestoneInvoiced} of {milestoneRows.length} stages invoiced — {formatAmount(milestoneAmountSum, agreement.currency)}
                  {agreement.not_to_exceed !== null && <> of {formatAmount(Number(agreement.not_to_exceed), agreement.currency)} NTE</>}
                </div>
              </div>
              <div className="mb-3 flex items-start gap-2 rounded-lg border border-primary-100 bg-primary-50/60 px-3 py-2 text-xs text-primary-800">
                <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                <span>
                  Stages are a record of the payment plan. Matching an invoice to a stage is manual, and no
                  acceptance sign-off is required in this phase.
                </span>
              </div>
              <ScheduleTable
                scheduleType="milestone"
                rows={milestoneRows}
                agreementId={agreement.id}
                agreementNumber={agreement.number}
                currency={agreement.currency}
                myOpenTasks={myTasks?.items ?? []}
                users={usersData?.items}
              />
            </div>
          )}

          {/* Agreement receipts — house_account only. recurring/milestone settle
              against the payment schedule above instead; house_account has
              no schedule rows at all (see the comment on scheduleData).
              Task 10: display-only here, mirroring how a PO shows its GRs but
              creating one is a separate page — recording now happens on the
              standalone /receipts/new page (linked below), not inline. */}
          {agreement.agreement_type === 'house_account' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-5">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
                  Agreement Receipts <span className="normal-case font-normal text-neutral-400">({receipts.length})</span>
                </h2>
                {/* Same admissibility gate the old inline form used (isAgreementAdmissible,
                    defined above) — status alone (agreement_type check above) isn't enough:
                    the backend's create_receipt route accepts a POST against a draft/cancelled/
                    past-grace agreement with no status check of its own, so without this the
                    button would offer recording a receipt that could never be reconciled. */}
                {canRecordReceipt && isAgreementAdmissible(agreement) && (
                  <Link to={`/receipts/new?agreement_id=${agreement.id}`}>
                    <Button size="sm" variant="secondary" className="gap-1.5">
                      <Plus className="h-3.5 w-3.5" />
                      New Receipt
                    </Button>
                  </Link>
                )}
              </div>
              <ReceiptTable
                agreementId={agreement.id}
                receipts={receipts}
                users={usersData?.items}
                currency={agreement.currency}
                canWrite={canRecordReceipt}
                canApReview={canApReviewReceipt}
                readOnly
              />
            </div>
          )}

          {/* Invoices matched to this agreement */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
                Matched Invoices <span className="normal-case font-normal text-neutral-400">({invoices.length})</span>
              </h2>
              {legacySettlementCount > 0 && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-warning-50 border border-warning-200 px-2.5 py-1 text-xs font-semibold text-warning-700">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  {legacySettlementCount} settled without receipt
                </span>
              )}
            </div>

            {invoicesLoading ? (
              <div className="py-8 text-center text-sm text-neutral-400">Loading…</div>
            ) : invoices.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <FileText className="h-8 w-8 text-neutral-300 mb-2" />
                <p className="text-sm text-neutral-400">No invoices matched to this agreement yet.</p>
              </div>
            ) : (
              <div className="rounded-lg border border-neutral-200 overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-neutral-200 bg-neutral-50">
                        <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Invoice</th>
                        <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Vendor Invoice #</th>
                        <th className="px-4 py-2.5 text-right text-xs font-semibold text-neutral-500">Amount</th>
                        <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Date</th>
                        <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Status</th>
                        <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Receipt</th>
                      </tr>
                    </thead>
                    <tbody>
                      {invoices.map((inv) => {
                        const invStatusCfg = INVOICE_STATUS_CFG[inv.status] ?? { label: inv.status, variant: 'neutral' as const }
                        return (
                          <tr key={inv.id} className="border-b border-neutral-100 last:border-0 bg-white hover:bg-primary-50/60 transition-colors">
                            <td className="px-4 py-2.5">
                              <Link to={`/invoices/${inv.id}`} className="inline-flex items-center gap-1 font-mono text-xs font-medium text-primary-700 hover:text-primary-900">
                                {inv.internal_ref}
                                <ExternalLink className="h-3 w-3" />
                              </Link>
                            </td>
                            <td className="px-4 py-2.5 text-neutral-600">{inv.vendor_invoice_number}</td>
                            <td className="px-4 py-2.5 text-right amount font-medium text-neutral-900">{formatAmount(inv.total_amount, inv.currency)}</td>
                            <td className="px-4 py-2.5 text-xs text-neutral-500">{formatDate(inv.invoice_date)}</td>
                            <td className="px-4 py-2.5">
                              <Badge variant={invStatusCfg.variant}>{invStatusCfg.label}</Badge>
                            </td>
                            <td className="px-4 py-2.5">
                              {inv.legacy_settlement ? (
                                <span title={inv.legacy_settlement_reason ?? undefined}>
                                  <Badge variant="warning">Settled without receipt</Badge>
                                </span>
                              ) : (
                                <span className="text-neutral-300">—</span>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>

          {/* Attachments */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
                Attachments <span className="normal-case font-normal text-neutral-400">({attachments.length})</span>
              </h2>
              {canWrite && (
                <label
                  htmlFor="agreement-attachment-upload"
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-100',
                    uploadAttachment.isPending ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
                  )}
                >
                  <Upload className="h-3.5 w-3.5" />
                  {uploadAttachment.isPending ? 'Uploading…' : 'Upload'}
                  <input
                    id="agreement-attachment-upload"
                    type="file"
                    className="sr-only"
                    disabled={uploadAttachment.isPending}
                    onChange={(e) => {
                      const file = e.target.files?.[0]
                      if (file) uploadAttachment.mutate(file)
                      e.target.value = ''
                    }}
                  />
                </label>
              )}
            </div>
            {attachmentsLoading ? (
              <div className="py-8 text-center text-sm text-neutral-400">Loading…</div>
            ) : attachments.length === 0 ? (
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
                    <span className="text-xs text-neutral-400">{formatBytes(att.file_size)}</span>
                    <button
                      type="button"
                      onClick={() => agreementAttachmentService.download(agreement.id, att.id, att.filename)}
                      className="text-xs text-primary-600 hover:underline"
                    >
                      Download
                    </button>
                    {canWrite && (
                      <button
                        type="button"
                        onClick={() => deleteAttachment.mutate(att.id)}
                        disabled={deleteAttachment.isPending}
                        className="text-neutral-300 hover:text-danger-500 disabled:opacity-40"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right: approval timeline */}
        <div className="lg:col-span-4">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5 sticky top-6">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-4">Approval Timeline</h2>
            <ApprovalTimeline steps={approvalSteps} />

            {/* Document Chain */}
            <DocumentChainTree currentType="agr" id={agreement.id} />
          </div>
        </div>
      </div>

      {/* Fixed bottom approver action bar */}
      {canApprove && (
        <div className="fixed bottom-0 left-60 right-0 z-10 bg-white border-t border-neutral-200 px-6 py-3 flex items-center justify-between shadow-[0_-1px_8px_rgba(0,0,0,0.06)] max-md:left-0">
          <p className="text-sm text-neutral-500">
            Reviewing <span className="font-medium text-neutral-900">{agreement.number}</span>
          </p>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => setPendingAction('return')} className="gap-1.5">
              <RotateCcw className="h-3.5 w-3.5" /> Return for Revision
            </Button>
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
          agreementNumber={agreement.number}
          isPending={agreementAction.isPending}
          onConfirm={(comment) => handleConfirm(pendingAction, comment)}
          onClose={() => setPendingAction(null)}
        />
      )}
    </div>
  )
}
