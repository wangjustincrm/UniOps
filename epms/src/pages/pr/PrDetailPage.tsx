import { useState, useRef } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import { ArrowLeft, CheckCircle2, XCircle, RotateCcw, MessageSquare, X, FileText, Pencil, ChevronDown } from 'lucide-react'
import { createPortal } from 'react-dom'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { ApprovalTimeline } from '@/components/pr/ApprovalTimeline'
import { formatAmount, formatDate, cn } from '@/lib/utils'
import type { ApprovalStep, DocumentStatus, WorkflowNodeDef } from '@/types'
import { useAuthStore } from '@/stores/auth.store'
import { useConfig } from '@/hooks/useConfig'
import { usePr, usePrAction, usePrEvents, usePrWorkflowSteps } from '@/hooks/usePrs'
import { useApprovalReminder } from '@/hooks/useApprovalReminder'
import { prService } from '@/services/pr'
import type { ApiEvent } from '@/services/pr'
import { useTasks } from '@/hooks/useTasks'
import { useBudgetOverview, useFactors } from '@/hooks/useBudget'
import { usePrAttachments, useUploadAttachment, useDeleteAttachment, useRegeneratePrPdf } from '@/hooks/usePrAttachments'
import { prAttachmentService } from '@/services/prAttachments'
import { AttachmentsEditor } from '@/components/shared/AttachmentsEditor'
import { DocumentChainTree } from '@/components/shared/DocumentChainTree'
import { generatePrHtml } from '@/lib/pr-document'
import { downloadPdf } from '@/lib/pdf-utils'

const TYPE_LABELS: Record<number, string> = {
  1: 'Raw Mat./Pack.',
  2: 'Consumables',
  3: 'Spare Parts',
  4: 'Service',
  5: 'Fixed Asset',
  6: 'Project',
}

function buildWorkflowSteps(
  nodes: WorkflowNodeDef[],
  status: string,
  stepIdx: number,
  events: ApiEvent[] = [],
  createdByName?: string | null,
): ApprovalStep[] {
  // Index approve events by step_idx so each workflow node shows who acted.
  const approveEventByStep = events
    .filter((e) => e.action === 'approve')
    .reduce<Record<number, ApiEvent>>((acc, e) => {
      if (!(e.step_idx in acc)) acc[e.step_idx] = e
      return acc
    }, {})
  const submitEvent = events.find((e) => e.action === 'submit')

  const created: ApprovalStep = {
    id: 'created',
    role: 'Requester',
    // Prefer the document's creator (always set, incl. imported data); fall back
    // to the submit event's actor for records created before created_by tracking.
    actorName: createdByName ?? submitEvent?.actor_name ?? undefined,
    status: 'completed',
    action: 'Created',
    channel: 'Web',
  }
  const approvalNodes: ApprovalStep[] = nodes.map((node, i) => {
    let s: ApprovalStep['status']
    if (['approved', 'paid', 'closed'].includes(status)) {
      s = 'completed'
    } else if (status === 'rejected') {
      s = i < stepIdx ? 'completed' : i === stepIdx ? 'skipped' : 'pending'
    } else if (status === 'returned' || status === 'cancelled') {
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

const APPROVABLE_STATUSES: DocumentStatus[] = ['submitted', 'in_review']

// ─── Approval Action Modal ────────────────────────────────────────────────────

type ApprovalAction = 'approve' | 'reject' | 'return'

interface ApprovalModalProps {
  action: ApprovalAction
  prNumber: string
  onConfirm: (comment: string) => void
  onClose: () => void
  // The modal stays mounted until the action resolves, so without this the
  // confirm button is live for the whole request. A second click re-posts the
  // same action: usually a 409 the user reads as a failure, but for 'approve'
  // it can silently consume the NEXT step's task when the same person approves
  // two consecutive steps — two levels passed on one intended click.
  isPending: boolean
}

function ApprovalModal({ action, prNumber, onConfirm, onClose, isPending }: ApprovalModalProps) {
  const [comment, setComment] = useState('')
  const needsComment = action !== 'approve'
  const canSubmit = !needsComment || comment.trim().length > 0

  const config = {
    approve: {
      title: 'Approve Purchase Requisition',
      label: 'Approve',
      icon: <CheckCircle2 className="h-5 w-5 text-success-600" />,
      bgIcon: 'bg-success-50',
      btn: 'bg-success-600 hover:bg-success-700 text-white',
      commentLabel: 'Comment (optional)',
      placeholder: 'Add an optional comment…',
    },
    reject: {
      title: 'Reject Purchase Requisition',
      label: 'Reject',
      icon: <XCircle className="h-5 w-5 text-danger-600" />,
      bgIcon: 'bg-danger-50',
      btn: 'bg-danger-600 hover:bg-danger-700 text-white',
      commentLabel: 'Reason for rejection *',
      placeholder: 'Explain why this PR is being rejected…',
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
              <p className="text-xs text-neutral-500">{prNumber}</p>
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-600">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-6 py-5 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">
              {config.commentLabel}
            </label>
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

const TABS = ['Details', 'Attachments', 'History'] as const
type Tab = typeof TABS[number]

export default function PrDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { user } = useAuthStore()
  const { data: config } = useConfig()
  const { data: workflowSteps } = usePrWorkflowSteps(id ?? '')
  const [activeTab, setActiveTab] = useState<Tab>('Details')
  const [pendingAction, setPendingAction] = useState<ApprovalAction | null>(null)
  const [moreOpen, setMoreOpen] = useState(false)
  const moreRef = useRef<HTMLDivElement>(null)

  const { data: pr, isLoading } = usePr(id ?? '')
  useDocTabTitle(pr?.number)
  const { data: events } = usePrEvents(id ?? '')
  const prAction = usePrAction(id ?? '')
  const { reminder, sendReminder } = useApprovalReminder(() => prService.remind(id ?? ''))
  const { data: attachments = [] } = usePrAttachments(id ?? '')
  const uploadAttachment = useUploadAttachment(id ?? '')
  const deleteAttachment = useDeleteAttachment(id ?? '')
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const regeneratePdf = useRegeneratePrPdf(id ?? '')
  const { data: budgetData } = useBudgetOverview()
  const budgetAccount = pr?.budget_code
    ? (budgetData?.accounts ?? []).find((a) => a.code === pr.budget_code)
    : undefined
  // Resolve factor_code/value_code to human names for the Decomposition Factors block.
  const factorsAccountId =
    pr?.factor_combo && budgetAccount?.decomposition_enabled ? budgetAccount.id : null
  const { data: prFactors = [] } = useFactors(factorsAccountId)

  const stepIdx = pr ? (pr.approval_step_idx ?? 0) : 0
  const currentNode = pr && (workflowSteps ?? [])[stepIdx]

  // Task-based approval check: the approval engine assigns tasks to specific users.
  // Checking whether the current user has an active approve_pr task for THIS PR
  // is the only reliable way to determine canApprove — role string comparison
  // alone breaks for routing tokens (gm_or_opm) and multi-role users (PRD §1.2, §3.3.6).
  const { data: myTasks } = useTasks({ is_completed: false })
  const hasApproveTask = !!pr && !!(myTasks?.items ?? []).some(
    (t) => t.document_id === pr.id && t.type === 'approve_pr'
  )
  // system_admin bypass per PRD §3.3.6 — admin can act on any submitted/in_review doc.
  const isAdmin = user?.role === 'system_admin'
  // While tasks are still loading, fall back to role comparison so buttons appear
  // immediately without flicker (backend will enforce actual authorization on action).
  const tasksLoaded = myTasks !== undefined
  const roleMatchesStep = !currentNode || user?.role === currentNode.role
  const canApprove =
    !!user &&
    !!pr &&
    APPROVABLE_STATUSES.includes(pr.status as DocumentStatus) &&
    (hasApproveTask || isAdmin || (!tasksLoaded && roleMatchesStep))

  const handleConfirm = (action: ApprovalAction, comment: string) => {
    if (!pr) return
    const apiAction = action === 'approve' ? 'approve' : action === 'return' ? 'return' : 'reject'
    prAction.mutate(
      { action: apiAction, comment: comment || undefined },
      { onSuccess: () => setPendingAction(null) }
    )
  }

  const handleDownloadPdf = () => {
    if (!pr) return
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    downloadPdf(generatePrHtml(pr as any, config as any), `${pr.number}_Purchase_Requisition.pdf`)
  }

  if (isLoading) {
    return <div className="flex items-center justify-center py-24 text-sm text-neutral-400">Loading…</div>
  }

  if (!pr) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <div className="text-5xl mb-4">🔍</div>
        <h2 className="text-xl font-semibold text-neutral-700">PR Not Found</h2>
        <p className="mt-2 text-sm text-neutral-400">
          The purchase requisition you're looking for doesn't exist.
        </p>
        <BackLink to="/pr" className="mt-4">
          <Button variant="secondary">Back to PR List</Button>
        </BackLink>
      </div>
    )
  }

  const approvalSteps = buildWorkflowSteps(
    workflowSteps ?? [],
    pr.status,
    pr.approval_step_idx ?? 0,
    events ?? [],
    pr.created_by_name,
  )
  const hasMaterial = pr.type === 1 || pr.type === 3

  return (
    <div className={cn('flex flex-col gap-6', canApprove && 'pb-16')}>
      {/* Page header */}
      <div className="rounded-lg border border-neutral-200 bg-white px-6 py-4">
        <div className="flex items-center gap-3 mb-3">
          <BackLink to="/pr">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4" />
              Back to PR List
            </Button>
          </BackLink>
        </div>
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-xl font-bold text-neutral-900">{pr.number}</h1>
              <StatusBadge status={pr.status} />
            </div>
            <p className="mt-1 text-neutral-600">{pr.title} — {pr.vendor_name || '—'}</p>
            <p className="mt-0.5 text-sm text-neutral-400">Submitted {formatDate(pr.submitted_at ?? '')}</p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {['draft', 'returned', 'submitted', 'in_review'].includes(pr.status) && !canApprove && (
              <>
                {['draft', 'returned'].includes(pr.status) ? (
                  <Link to={`/pr/${pr.id}/edit`}>
                    <Button variant="secondary" size="sm">
                      <Pencil className="h-3.5 w-3.5" />
                      Edit
                    </Button>
                  </Link>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      if (confirm(`Recall ${pr.number} for editing? It will be moved back to Draft.`)) {
                        prAction.mutate(
                          { action: 'recall' },
                          { onSuccess: () => navigate(`/pr/${pr.id}/edit`) }
                        )
                      }
                    }}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                    Recall to Edit
                  </Button>
                )}
                <Button
                  variant="secondary"
                  size="sm"
                  className="border-danger-200 text-danger-600 hover:bg-danger-50"
                  onClick={() => {
                    if (confirm(`Withdraw ${pr.number}? This will cancel the PR.`)) {
                      prAction.mutate({ action: 'cancel' })
                    }
                  }}
                  disabled={prAction.isPending}
                >
                  Withdraw
                </Button>
              </>
            )}
            {/* Approver actions moved to fixed bottom bar */}
            {pr.status === 'approved' && !pr.po_id &&
              (user?.role === 'procurement_officer' || user?.role === 'procurement_manager' || user?.role === 'system_admin') && (
              <Link to={`/po/new?prId=${pr.id}`}>
                <Button size="sm">Create PO</Button>
              </Link>
            )}
            {pr.po_id && (
              <Link to={`/po/${pr.po_id}`}>
                <Button variant="secondary" size="sm">View PO: {pr.po_number}</Button>
              </Link>
            )}
          </div>
        </div>
      </div>

      {/* Main content + timeline */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left: tabs */}
        <div className="lg:col-span-8 flex flex-col gap-4">
          {/* Tab bar */}
          <div className="flex border-b border-neutral-200">
            {TABS.map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={[
                  'px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors',
                  activeTab === tab
                    ? 'border-primary-600 text-primary-700'
                    : 'border-transparent text-neutral-500 hover:text-neutral-700',
                ].join(' ')}
              >
                {tab}
              </button>
            ))}
          </div>

          {/* Details tab */}
          {activeTab === 'Details' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-6">
              <section>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-3">Details</h2>
                <dl className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2 text-sm">
                  {([
                    ['Procurement Type', TYPE_LABELS[pr.type] ?? `Type ${pr.type}`],
                    ['Vendor', pr.vendor_name || '—'],
                    ['Department', pr.department_name || '—'],
                    ['Cost Center', pr.cost_center_name || '—'],
                    ['Budget Code', pr.budget_code ? (budgetAccount ? `${pr.budget_code} — ${budgetAccount.name}` : pr.budget_code) : '—'],
                    ...(pr.type === 5 ? [['Fixed Asset ID', pr.fixed_asset_id || '—'] as [string, string]] : []),
                    ...(pr.type === 6 ? [['Project No.', pr.project_code || '—'] as [string, string]] : []),
                    ['Currency', pr.currency ?? 'CAD'],
                    [`Total Amount (${pr.currency ?? 'CAD'})`, formatAmount(pr.amount, pr.currency ?? 'CAD')],
                    ['Required By Date', formatDate(pr.required_by ?? '')],
                    ...(pr.type === 4 || pr.type === 6
                      ? [['Service/Project Expected Completion Date',
                          formatDate(pr.service_completion_date ?? '')] as [string, string]]
                      : []),
                    ['Delivery Address', pr.delivery_address || '—'],
                    ['Notes', pr.notes || '—'],
                  ] as [string, string][]).map(([label, value]) => (
                    <div key={label} className="flex flex-col gap-0.5">
                      <dt className="text-xs font-medium text-neutral-500">{label}</dt>
                      <dd className="text-neutral-900">{value}</dd>
                    </div>
                  ))}
                </dl>
              </section>

              {/* Decomposition Factors — only when the Budget Account had factors at PR time */}
              {pr.factor_combo && Object.keys(pr.factor_combo).length > 0 && (
                <section>
                  <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-3">
                    Decomposition Factors
                  </h2>
                  <dl className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-3 text-sm">
                    {Object.entries(pr.factor_combo).map(([factorCode, valueCode]) => {
                      const factor = prFactors.find((f) => f.factor_code === factorCode)
                      const value = factor?.values.find((v) => v.value_code === valueCode)
                      const factorLabel = factor?.factor_name ?? factorCode
                      const valueLabel = value
                        ? `${value.value_code} — ${value.value_name}`
                        : valueCode
                      return (
                        <div key={factorCode} className="flex flex-col gap-0.5">
                          <dt className="text-xs font-medium text-neutral-500">{factorLabel}</dt>
                          <dd className="text-neutral-900">{valueLabel}</dd>
                        </div>
                      )
                    })}
                  </dl>
                </section>
              )}

              {/* Over-Budget Justification (OBG-007) — visible to all approvers */}
              {pr.over_budget && (
                <section>
                  <div className="rounded-md border border-warning-300 bg-warning-50 px-4 py-3">
                    <h2 className="text-xs font-semibold uppercase tracking-wide text-warning-700 mb-1">Over-Budget Justification</h2>
                    <p className="text-sm text-warning-900 whitespace-pre-wrap">
                      {pr.over_budget_justification || <span className="italic text-warning-600">(no justification provided)</span>}
                    </p>
                  </div>
                </section>
              )}

              {/* Line Items */}
              <section>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-3">
                  Line Items <span className="normal-case font-normal text-neutral-400">({pr.line_items.length})</span>
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
                          {/* Always shown for consistency with the Create PR page, which always exposes this input. */}
                          <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 w-36">Supplier Item ID</th>
                          <th className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 w-20">Qty</th>
                          <th className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 w-20">Unit</th>
                          <th className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 w-32">Unit Price</th>
                          <th className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 w-32">Line Total</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pr.line_items.map((item, i) => (
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
                            <td className="px-4 py-2.5 amount text-right text-neutral-900">{formatAmount(item.unit_price, pr.currency ?? 'CAD')}</td>
                            <td className="px-4 py-2.5 amount text-right font-semibold text-neutral-900">{formatAmount(item.line_total, pr.currency ?? 'CAD')}</td>
                          </tr>
                        ))}
                      </tbody>
                      <tfoot>
                        <tr className="border-t-2 border-neutral-200 bg-neutral-50">
                          <td
                            colSpan={6 + (hasMaterial ? 1 : 0)}
                            className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500"
                          >
                            Total ({pr.currency ?? 'CAD'})
                          </td>
                          <td className="px-4 py-3 amount text-right text-base font-bold text-neutral-900">
                            {formatAmount(pr.amount, pr.currency ?? 'CAD')}
                          </td>
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                </div>
              </section>
            </div>
          )}

          {/* Attachments tab */}
          {activeTab === 'Attachments' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
                  Attachments
                </h2>
                {pr.status === 'approved' && (
                  <button
                    type="button"
                    onClick={() => regeneratePdf.mutate()}
                    disabled={regeneratePdf.isPending}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-100 disabled:opacity-50"
                    title="Generate the approved-PR PDF and attach it (replaces the existing one)"
                  >
                    <RotateCcw className={`h-3.5 w-3.5 ${regeneratePdf.isPending ? 'animate-spin' : ''}`} />
                    {regeneratePdf.isPending ? 'Generating…' : 'Regenerate PDF'}
                  </button>
                )}
              </div>
              {downloadError && (
                <div className="mb-3 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-600">
                  {downloadError}
                </div>
              )}
              <AttachmentsEditor
                inputId="pr-detail-file-upload"
                attachments={attachments}
                isUploading={uploadAttachment.isPending}
                isDeleting={deleteAttachment.isPending}
                onUpload={(file) => uploadAttachment.mutateAsync(file)}
                onDelete={(attId) => deleteAttachment.mutate(attId)}
                onDownload={(att) => {
                  setDownloadError(null)
                  prAttachmentService.download(id!, att.id, att.filename).catch(() => {
                    setDownloadError(`Could not download "${att.filename}". Please try again or contact IT if it persists.`)
                  })
                }}
              />
            </div>
          )}

          {/* History tab */}
          {activeTab === 'History' && (
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-4">
                Audit History
              </h2>
              {events && events.length > 0 ? (
                <ul className="flex flex-col gap-3">
                  {events.map((ev) => (
                    <li key={ev.id} className="flex gap-3 text-sm">
                      <span className="text-xs text-neutral-400 whitespace-nowrap pt-0.5">{formatDate(ev.created_at)}</span>
                      <div>
                        <span className="font-medium text-neutral-800 capitalize">{ev.action.replace(/_/g, ' ')}</span>
                        <span className="text-neutral-500 ml-1">by {ev.actor_role.replace(/_/g, ' ')}</span>
                        {ev.actor_name && <span className="text-neutral-400 ml-1">— {ev.actor_name}</span>}
                        {ev.comment && <p className="text-neutral-400 text-xs mt-0.5 italic">{ev.comment}</p>}
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
            <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500 mb-4">
              Approval Timeline
            </h2>
            <ApprovalTimeline
              steps={approvalSteps}
              // Only offered while the PR is genuinely sitting on an approver.
              // The timeline marks step 0 "current" for a draft too, so without
              // this the link would appear on drafts and always answer 409.
              onSendReminder={
                APPROVABLE_STATUSES.includes(pr.status as DocumentStatus) ? sendReminder : undefined
              }
              reminder={reminder}
            />

            {/* Approver actions moved to fixed bottom bar */}

            {/* Document Chain */}
            <DocumentChainTree currentType="pr" id={pr.id} />
          </div>
        </div>
      </div>

      {/* Fixed bottom approver action bar */}
      {canApprove && (
        <div className="fixed bottom-0 left-60 right-0 z-10 bg-white border-t border-neutral-200 px-6 py-3 flex items-center justify-between shadow-[0_-1px_8px_rgba(0,0,0,0.06)] max-md:left-0">
          <p className="text-sm text-neutral-500">
            Reviewing <span className="font-medium text-neutral-900">{pr?.number}</span>
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

      {/* Approval action modal */}
      {pendingAction && (
        <ApprovalModal
          action={pendingAction}
          prNumber={pr.number}
          onConfirm={(comment) => handleConfirm(pendingAction, comment)}
          onClose={() => setPendingAction(null)}
          isPending={prAction.isPending}
        />
      )}
    </div>
  )
}
