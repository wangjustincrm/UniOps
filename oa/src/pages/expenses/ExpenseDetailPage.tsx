import { useParams, useNavigate } from 'react-router-dom'
import { useDocTabTitle } from '@/components/BackLink'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useRef } from 'react'
import { ArrowLeft, CheckCircle, XCircle, RotateCcw, Banknote, AlertTriangle, Paperclip, Upload, Download, Trash2, Circle, Clock, FileText } from 'lucide-react'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { api, ApiError } from '@/lib/api'
import { STATUS, ACTION, isEditable, isInApproval } from '@/lib/status'
import ProcessPaymentModal from '@/components/ProcessPaymentModal'
import { StatusBadge } from '@/components/ui/badge'
import { ActionModal } from '@/components/ui/ActionModal'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { ErrorBanner } from '@/components/ui/ErrorBanner'

import { FileInput, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
// ── Types ─────────────────────────────────────────────────────────────────────

interface ApprovalEvent {
  id: string
  actor_name: string
  action: string
  comment: string | null
  // Step the action was taken at (approval events only; null on the payment row).
  step_idx?: number | null
  created_at: string
}

interface LineItem {
  id: string
  line_number: number
  expense_date: string
  description: string
  budget_account_code: string
  budget_account_name: string
  total_amount: number
  tax_amount: number
  net_amount: number
  tax_code?: string | null
  // TRV date-range line (null on single-day lines)
  period_from?: string | null
  period_to?: string | null
  period_days?: number | null
  daily_rate?: number | null
  invoice_amount?: number | null
}

interface TripItem {
  id: string
  trip_number: number
  trip_date: string
  from_location: string
  to_location: string
  purpose: string
  is_round_trip: boolean
  distance_km: number
  rate_per_km: number
  amount: number
}

interface ExpenseClaim {
  id: string
  claim_number: string
  claim_type: string
  employee_id: string
  employee_name: string
  department_name: string
  submission_date: string
  currency: string
  notes: string | null
  vehicle_description: string | null
  vehicle_owned_by: string | null
  total_km: number | null
  total_amount: number
  tax_amount: number
  net_amount: number
  status: string
  approval_step_idx: number
  is_over_budget: boolean
  submitted_at: string | null
  approved_at: string | null
  paid_at: string | null
  created_at: string
  line_items: LineItem[]
  trip_items: TripItem[]
  approval_events: ApprovalEvent[]
  // TRA (Travel Application) fields
  travelers?: { id: string; user_name: string }[]
  transport_modes?: string[]
  travel_destination?: string | null
  travel_country?: string | null
  travel_from_date?: string | null
  travel_to_date?: string | null
  leave_from_date?: string | null
  leave_to_date?: string | null
  purpose?: string | null
  // Custom-form claims: `notes` is the stored answers blob (the edit form reads
  // it); these are its meaning, resolved server-side like the PDF (ISS-0195).
  form_name?: string | null
  form_fields?: { label: string; value: string }[] | null
  user_notes?: string | null
}

interface Attachment {
  id: string; claim_id: string; file_id: string
  file_name: string; file_size_bytes: number; mime_type: string | null
  uploaded_at: string; download_url: string | null
}

interface ApprovalStep {
  step_idx: number
  role: string
  label: string
  state: 'waiting' | 'current' | 'approved' | 'returned' | 'rejected'
  actor_name: string | null
  actor_role: string | null
  acted_at: string | null
  action: string | null
  comment: string | null
  // Pending step: who its task waits on, "Name (delegated: Stand-in)" while
  // they are covered (ABS-006); null for a role-pool task.
  approver_name?: string | null
}

// ── Badges ────────────────────────────────────────────────────────────────────

const ACTION_ICONS: Record<string, React.ReactNode> = {
  [ACTION.SUBMIT]:  <CheckCircle className="h-4 w-4 text-info-fg" />,
  [ACTION.APPROVE]: <CheckCircle className="h-4 w-4 text-success-fg" />,
  [ACTION.REJECT]:  <XCircle className="h-4 w-4 text-danger-fg" />,
  [ACTION.RETURN]:  <RotateCcw className="h-4 w-4 text-warning-fg" />,
  [ACTION.PAY]:     <Banknote className="h-4 w-4 text-success-fg" />,
}

const ACTION_LABELS: Record<string, string> = {
  [ACTION.SUBMIT]:  'Submitted',
  [ACTION.APPROVE]: 'Approved',
  [ACTION.REJECT]:  'Rejected',
  [ACTION.RETURN]:  'Returned',
  [ACTION.PAY]:     'Paid',
}

// ── Attachments card ──────────────────────────────────────────────────────────

/**
 * What an employee is told when an attachment will not download (0218).
 * "File server error 410" meant nothing to them; say what happened and what
 * to do. expense-api answers 404 when the file server has no such file and
 * 502 "File server error <code>" for anything else the file server said —
 * a 404 or 410 (deleted) there is still a missing file.
 */
function downloadFailureMessage(fileName: string, err: unknown): string {
  const status = err instanceof ApiError ? err.status : undefined
  const text = err instanceof Error ? err.message : ''
  const upstream = Number(/File server error (\d{3})/.exec(text)?.[1]) || undefined
  if (status === 404 || status === 410 || upstream === 404 || upstream === 410) {
    return `Could not download "${fileName}". The file could not be found on the file server — please upload it again or contact IT.`
  }
  if ((status === undefined && err instanceof TypeError)  // fetch itself failed: offline, server down
      || status === 502 || status === 503 || status === 504) {
    return `Could not download "${fileName}". The file server is not available right now — please try again in a few minutes, or contact IT if it keeps happening.`
  }
  if (status === 403) {
    return `Could not download "${fileName}". You do not have access to this file.`
  }
  return `Could not download "${fileName}". Please try again, or contact IT if it keeps happening.`
}

function AttachmentsCard({ claimId, canUpload, canRegeneratePdf }: {
  claimId: string; canUpload: boolean
  // Approved / paid claims: the PDF with the approval record is filed here at
  // approval; this rebuilds it (backfill, failed first attempt, name changes).
  canRegeneratePdf: boolean
}) {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [downloadError, setDownloadError] = useState('')
  // The server's own words (e.g. "File server error 410"), kept for IT on the
  // banner's tooltip rather than shown to the employee.
  const [downloadErrorDetail, setDownloadErrorDetail] = useState('')

  const regenPdf = useMutation({
    mutationFn: () => api.post(`/api/v1/expenses/${claimId}/regenerate-pdf`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['expense-attachments', claimId] }),
  })

  const { data: attachments = [] } = useQuery<Attachment[]>({
    queryKey: ['expense-attachments', claimId],
    queryFn: () => api.get<Attachment[]>(`/api/v1/expenses/${claimId}/attachments`),
  })

  const deleteMutation = useMutation({
    mutationFn: (attId: string) =>
      api.delete(`/api/v1/expenses/${claimId}/attachments/${attId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['expense-attachments', claimId] }),
  })

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setUploading(true); setUploadError('')
    try {
      const form = new FormData()
      form.append('file', file)
      // Use the shared api client (absolute VITE_API_URL base + auth) — NOT a relative
      // /api fetch. The OA frontend runs in Docker where the Vite proxy can't reach
      // expense-api, so relative /api requests 502. Mirrors api.get/api.delete above.
      await api.postForm(`/api/v1/expenses/${claimId}/attachments`, form)
      qc.invalidateQueries({ queryKey: ['expense-attachments', claimId] })
    } catch (err: any) {
      setUploadError(err.message || 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  // Authenticated blob download — a plain <a href> to the relative download_url
  // carries no bearer token and (in prod) resolves against the OA origin, whose
  // nginx SPA-fallback returns index.html (the main page) instead of the file.
  // Mirrors InvoiceDetailPage / PaDetailPage.
  // A failed download is said on the page, by file name; it used to reject
  // silently and show only in the browser console (ISS-0218).
  const download = async (att: Attachment) => {
    setDownloadError('')
    setDownloadErrorDetail('')
    try {
      const blob = await api.getBlob(`/api/v1/expenses/${claimId}/attachments/${att.id}/file`)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = att.file_name; a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setDownloadError(downloadFailureMessage(att.file_name, err))
      setDownloadErrorDetail(err instanceof Error ? err.message : String(err))
    }
  }

  function formatSize(bytes: number) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  return (
    <div className="rounded-xl border border-line bg-surface overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-line-subtle">
        <h2 className="text-sm font-semibold text-fg-secondary flex items-center gap-2">
          <Paperclip className="h-4 w-4 text-fg-muted" />
          Attachments {attachments.length > 0 && `(${attachments.length})`}
        </h2>
        <div className="flex items-center gap-4">
        {canRegeneratePdf && (
          <Button variant="link"
            type="button"
            disabled={regenPdf.isPending}
            onClick={() => regenPdf.mutate()}
            className="text-xs"
          >
            <FileText className="h-3.5 w-3.5" />
            {regenPdf.isPending ? 'Generating…' : 'Regenerate PDF'}
          </Button>
        )}
        {canUpload && (
          <Button variant="link"
            type="button"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
            className="text-xs"
          >
            <Upload className="h-3.5 w-3.5" />
            {uploading ? 'Uploading…' : 'Upload file'}
          </Button>
        )}
        </div>
        <FileInput ref={fileRef} className="hidden" onChange={handleFileChange}
          accept=".pdf,.jpg,.jpeg,.png,.webp,.xlsx,.csv,.doc,.docx" />
      </div>

      {uploadError && (
        <div className="mx-5 mt-3">
          <ErrorBanner message={uploadError} />
        </div>
      )}
      {downloadError && (
        <div className="mx-5 mt-3" title={downloadErrorDetail || undefined}>
          <ErrorBanner message={downloadError} />
        </div>
      )}
      {regenPdf.isError && (
        <div className="mx-5 mt-3">
          <ErrorBanner message={regenPdf.error instanceof Error ? regenPdf.error.message : 'PDF generation failed'} />
        </div>
      )}

      {attachments.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 text-center text-sm text-fg-muted">
          <Paperclip className="h-6 w-6 mb-2 opacity-40" />
          No attachments
          {canUpload && <p className="text-xs mt-1">Click "Upload file" to add receipts or supporting documents</p>}
        </div>
      ) : (
        <div className="divide-y divide-line-subtle">
          {attachments.map(att => (
            <div key={att.id} className="flex items-center gap-3 px-5 py-3">
              <Paperclip className="h-4 w-4 text-fg-muted shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-fg truncate">{att.file_name}</p>
                <p className="text-xs text-fg-muted">{formatSize(att.file_size_bytes)}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {att.download_url && (
                  <Button variant="link" type="button" onClick={() => download(att)}
                    className="text-xs">
                    <Download className="h-3.5 w-3.5" />Download
                  </Button>
                )}
                {canUpload && (
                  <Button variant="danger-ghost" size="icon-xs" aria-label="Remove" onClick={() => deleteMutation.mutate(att.id)}
                   >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Approval status card ──────────────────────────────────────────────────────

interface ReminderResponse {
  sent: boolean
  document_number: string
  recipients: string[]
  next_allowed_at: string
}

function ApprovalStatusCard({ claimId }: { claimId: string }) {
  const { data: steps = [], isLoading } = useQuery<ApprovalStep[]>({
    queryKey: ['approval-status', claimId],
    queryFn: () => api.get<ApprovalStep[]>(`/api/v1/expenses/${claimId}/approval-status`),
  })

  // "Send reminder" under the pending step — same behaviour as EPMS's Approval
  // Timeline. The action changes nothing on the page, so the outcome is shown
  // next to the button; the server's 409 (nobody to remind / unreachable) and
  // 429 (already reminded in the last 24h) are sentences meant for the user and
  // are shown verbatim.
  const [reminderMsg, setReminderMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const remindMutation = useMutation({
    mutationFn: () => api.post<ReminderResponse>(`/api/v1/expenses/${claimId}/remind`, {}),
    onMutate: () => setReminderMsg(null),
    onSuccess: (res) => setReminderMsg({ ok: true, text: `Reminder sent to ${res.recipients.join(', ')}.` }),
    onError: (err) => setReminderMsg({
      ok: false,
      text: err instanceof Error ? err.message : 'Could not send the reminder.',
    }),
  })

  if (isLoading || steps.length === 0) return null

  const approvedCount = steps.filter(s => s.state === 'approved').length

  const stateIcon = (state: ApprovalStep['state']) => {
    switch (state) {
      case 'approved': return <CheckCircle className="h-5 w-5 text-success-fg" />
      case 'current':  return <Clock className="h-5 w-5 text-primary-700" />
      case 'returned': return <RotateCcw className="h-5 w-5 text-warning-fg" />
      case 'rejected': return <XCircle className="h-5 w-5 text-danger-fg" />
      default:         return <Circle className="h-5 w-5 text-fg-faint" />
    }
  }
  const stateLabel: Record<ApprovalStep['state'], string> = {
    approved: 'Approved', current: 'Pending', waiting: 'Waiting',
    returned: 'Returned', rejected: 'Rejected',
  }
  const stateBadge: Record<ApprovalStep['state'], string> = {
    approved: 'bg-success-soft text-success-fg',
    current:  'bg-primary-700/10 text-primary-700',
    waiting:  'bg-surface-sunken text-fg-muted',
    returned: 'bg-warning-soft text-warning-fg',
    rejected: 'bg-danger-soft text-danger-fg',
  }

  return (
    <div className="rounded-xl border border-line bg-surface overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-line-subtle">
        <h2 className="text-sm font-semibold text-fg-secondary">Approval Status</h2>
        <span className="text-xs text-fg-muted">{approvedCount} of {steps.length} approved</span>
      </div>
      <ol className="divide-y divide-line-subtle">
        {steps.map((s, i) => (
          <li key={s.step_idx} className="flex items-start gap-3 px-5 py-3">
            <div className="flex flex-col items-center">
              {stateIcon(s.state)}
              {i < steps.length - 1 && <div className="mt-1 h-6 w-px bg-surface-strong" />}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-fg">{s.label}</span>
                <span className={cn('rounded-full px-2 py-0.5 text-2xs font-medium', stateBadge[s.state])}>
                  {stateLabel[s.state]}
                </span>
              </div>
              {s.state === 'approved' && s.actor_name && (
                <p className="mt-0.5 text-xs text-fg-muted">
                  by {s.actor_name}{s.acted_at ? ` · ${formatDate(s.acted_at)}` : ''}
                </p>
              )}
              {/* The step that sent the claim back (or rejected it): who, when
                  and why, so the claimant sees where it stopped (ISS-0194). */}
              {(s.state === 'returned' || s.state === 'rejected') && s.actor_name && (
                <>
                  <p className="mt-0.5 text-xs text-fg-muted">
                    by {s.actor_name}{s.acted_at ? ` · ${formatDate(s.acted_at)}` : ''}
                  </p>
                  {s.comment && (
                    <p className="mt-1 whitespace-pre-line rounded bg-surface-subtle px-2 py-1.5 text-xs text-fg-secondary">{s.comment}</p>
                  )}
                </>
              )}
              {s.state === 'current' && (
                <>
                  <p className="mt-0.5 text-xs text-fg-muted">
                    {s.approver_name ? `Awaiting ${s.approver_name}` : 'Awaiting this approver'}
                  </p>
                  <Button variant="link"
                    type="button"
                    onClick={() => remindMutation.mutate()}
                    disabled={remindMutation.isPending}
                    className="mt-1 text-xs"
                  >
                    {remindMutation.isPending ? 'Sending reminder…' : 'Send reminder'}
                  </Button>
                  {reminderMsg && !remindMutation.isPending && (
                    <p className={cn('mt-1 text-xs', reminderMsg.ok ? 'text-success-fg' : 'text-warning-fg')}>
                      {reminderMsg.text}
                    </p>
                  )}
                </>
              )}
              {/* Approval comments are shown once, in the Approval Timeline below. */}
            </div>
            <span className="shrink-0 text-2xs font-mono text-fg-faint">Step {s.step_idx + 1}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ExpenseDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [activeAction, setActiveAction] = useState<string | null>(null)

  const { data: claim, isLoading } = useQuery<ExpenseClaim>({
    queryKey: ['expense', id],
    queryFn: () => api.get<ExpenseClaim>(`/api/v1/expenses/${id}`),
  })
  useDocTabTitle(claim?.claim_number)

  // Shared with AttachmentsCard (same query key) — used to gate submit (EXP-007/TRV-008).
  const { data: attachments = [] } = useQuery<Attachment[]>({
    queryKey: ['expense-attachments', id],
    queryFn: () => api.get<Attachment[]>(`/api/v1/expenses/${id}/attachments`),
  })

  // Server-computed permissions — approval roles (Finance BP, etc.) are assignments,
  // not JWT roles, so authorization is resolved server-side via tasks + role_management.
  const { data: perms } = useQuery<{ is_owner: boolean; can_edit: boolean; can_submit: boolean; can_approve: boolean; can_pay: boolean; can_delete: boolean }>({
    queryKey: ['expense-permissions', id],
    queryFn: () => api.get(`/api/v1/expenses/${id}/permissions`),
  })

  // Same query (and cache) as ApprovalStatusCard — the timeline names the step
  // a return / reject was taken at from these labels (ISS-0194).
  const { data: approvalSteps = [] } = useQuery<ApprovalStep[]>({
    queryKey: ['approval-status', id],
    queryFn: () => api.get<ApprovalStep[]>(`/api/v1/expenses/${id}/approval-status`),
  })
  const stepLabel = (evt: ApprovalEvent) =>
    (evt.action === ACTION.RETURN || evt.action === ACTION.REJECT) && evt.step_idx != null
      ? approvalSteps.find((s) => s.step_idx === evt.step_idx)?.label ?? `Step ${evt.step_idx + 1}`
      : null

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ['expense', id] })
    qc.invalidateQueries({ queryKey: ['expense-permissions', id] })
    qc.invalidateQueries({ queryKey: ['approval-status', id] })
    qc.invalidateQueries({ queryKey: ['expense-list'] })
  }

  const actionMutation = useMutation({
    mutationFn: ({ action, comment }: { action: string; comment: string }) =>
      api.post(`/api/v1/expenses/${id}/action`, { action, comment: comment || null }),
    onSuccess: () => { setActiveAction(null); invalidateAll() },
  })

  // 'pay' is NOT a generic /action — it has its own endpoint with payment fields.
  // A refusal is the server's sentence for the payer and is shown in the modal;
  // it used to be swallowed, so Confirm Payment looked dead (ISS-0205).
  const payMutation = useMutation({
    mutationFn: (bankAccountId: string) => api.post(`/api/v1/expenses/${id}/pay`, { bank_account_id: bankAccountId }),
    onSuccess: () => { setActiveAction(null); invalidateAll() },
  })

  // TRA-only: hard-delete an unapproved Travel Application. `deleteMutation`
  // above is the attachment delete — different thing.
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const deleteClaim = useMutation({
    mutationFn: () => api.delete(`/api/v1/expenses/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['travel-list'] })
      navigate('/travel')
    },
    onError: (e: unknown) => setDeleteError(e instanceof Error ? e.message : 'Delete failed'),
  })

  // TRA-only: regenerate the Travel Application PDF and refresh the attachments card.
  const regenPdf = useMutation({
    mutationFn: () => api.post(`/api/v1/travel-applications/${id}/pdf`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['expense-attachments', id] }),
  })

  if (isLoading) {
    return <div className="flex items-center justify-center py-24 text-sm text-fg-muted">Loading…</div>
  }
  if (!claim) {
    return <div className="py-24 text-center text-sm text-danger-fg">Expense claim not found</div>
  }

  const TYPE_LABELS: Record<string, string> = {
    EXP: 'General Expense', MIL: 'Mileage Claim', TRV: 'Travel Expense', TRA: 'Travel Application',
  }
  const isCfm = claim.claim_type.startsWith('CFM')
  const typeLabel = TYPE_LABELS[claim.claim_type]
    ?? (isCfm ? `Custom Form (${claim.claim_type.replace(/^CFM_?/, '')})` : claim.claim_type)
  // A custom form's `notes` is its answers blob; the employee's remark is user_notes.
  const notesText = isCfm ? claim.user_notes : claim.notes

  // EXP-007 / TRV-008: receipt-based claims require ≥1 attachment before submission.
  // A TRV made only of date-range lines is exempt (same rule as the server).
  const trvAllRange = claim.claim_type === 'TRV' && claim.line_items.length > 0
    && claim.line_items.every((li) => li.period_from)
  const requiresAttachment = ['EXP', 'TRV'].includes(claim.claim_type) && !trvAllRange
  // Draft and returned alike: a returned claim is resubmitted from here too.
  const missingAttachment = requiresAttachment && isEditable(claim.status) && attachments.length === 0

  // Permissions resolved server-side (see the /permissions query above).
  // Edit and Submit are separate answers (OA.md §7.3): a Finance Manager may
  // edit someone else's claim but not submit it; a System Admin may do both.
  const canEdit = perms?.can_edit ?? false
  const canSubmit = perms?.can_submit ?? false
  const canApprove = perms?.can_approve ?? false
  const canPay = perms?.can_pay ?? false
  // False server-side for EXP/MIL/TRV, so this shared component needs no
  // client-side claim-type check.
  const canDelete = perms?.can_delete ?? false

  // TRA claims live under the Travel Applications section, not Expense Claims.
  const backHref = claim.claim_type === 'TRA' ? '/travel' : '/expenses'
  const backLabel = claim.claim_type === 'TRA' ? 'Travel Applications' : 'Expense Claims'

  return (
    <div className="flex flex-col gap-6 max-w-6xl">
      {/* Back + header */}
      <div>
        <Button variant="subtle" size="xs"
          onClick={() => navigate(backHref)}
          className="mb-4 text-sm"
        >
          <ArrowLeft className="h-4 w-4" />
          {backLabel}
        </Button>
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold text-fg">{claim.claim_number}</h1>
              <StatusBadge status={claim.status} />
              {claim.is_over_budget && (
                <span className="flex items-center gap-1 rounded-full bg-warning-soft border border-warning-line px-2 py-0.5 text-xs text-warning-fg">
                  <AlertTriangle className="h-3 w-3" />
                  Over Budget
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-fg-muted">
              {typeLabel} · {claim.employee_name} · {claim.department_name}
            </p>
          </div>

          {/* Action buttons */}
          <div className="flex gap-2">
            {/* Returned claims need Submit as well as Edit: Save Changes leaves the
                claim `returned` (approval-api accepts submit from draft or
                returned), and with only Edit here a returned claim could be
                changed but never sent back into approval. */}
            {isEditable(claim.status) && canSubmit && (
              <Button size="md"
                onClick={() => setActiveAction(ACTION.SUBMIT)}
                disabled={missingAttachment}
                title={missingAttachment ? 'Attach at least one receipt before submitting' : undefined}
               
              >
                Submit
              </Button>
            )}
            {isEditable(claim.status) && canEdit && (
              <Button variant="secondary" size="md"
                onClick={() => navigate(`/expenses/${claim.id}/edit`)}
               
              >
                Edit
              </Button>
            )}
            {isInApproval(claim.status) && canApprove && (
              <>
                <Button variant="warning-outline" size="md"
                  onClick={() => setActiveAction(ACTION.RETURN)}
                 
                >
                  Return
                </Button>
                <Button variant="danger-outline" size="md"
                  onClick={() => setActiveAction(ACTION.REJECT)}
                 
                >
                  Reject
                </Button>
                <Button variant="success" size="md"
                  onClick={() => setActiveAction(ACTION.APPROVE)}
                 
                >
                  Approve
                </Button>
              </>
            )}
            {claim.status === STATUS.APPROVED && canPay && (
              <Button variant="success" size="md"
                onClick={() => setActiveAction(ACTION.PAY)}
               
              >
                Mark as Processed
              </Button>
            )}
            {canDelete && (
              <Button variant="danger-outline" size="md"
                onClick={() => { setDeleteError(''); setConfirmingDelete(true) }}
               
              >
                <Trash2 className="h-4 w-4" />
                Delete
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Two columns, as on the EPMS document pages: the claim on the left,
          its approval chain and history on the right. */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
      <div className="lg:col-span-8 flex flex-col gap-6 min-w-0">

      {/* Claim summary */}
      <div className="grid grid-cols-2 gap-4 rounded-xl border border-line bg-surface p-5">
        <div>
          <p className="text-xs font-medium text-fg-muted">Submission Date</p>
          <p className="mt-0.5 text-sm font-medium text-fg">{formatDate(claim.submission_date)}</p>
        </div>
        <div>
          <p className="text-xs font-medium text-fg-muted">Currency</p>
          <p className="mt-0.5 text-sm font-medium text-fg">{claim.currency}</p>
        </div>
        {claim.vehicle_description && (
          <div>
            <p className="text-xs font-medium text-fg-muted">Vehicle</p>
            <p className="mt-0.5 text-sm font-medium text-fg">{claim.vehicle_description}</p>
          </div>
        )}
        {/* Only a mileage claim has km (user, 2026-10-14) — a general expense
            once carried total_km = 0 from a save bug and showed "Total km". */}
        {claim.claim_type === 'MIL' && claim.total_km != null && (
          <div>
            <p className="text-xs font-medium text-fg-muted">Total km</p>
            <p className="mt-0.5 text-sm font-mono font-medium text-fg">{Number(claim.total_km).toFixed(1)} km</p>
          </div>
        )}
        {notesText && (
          <div className="col-span-2">
            <p className="text-xs font-medium text-fg-muted">Notes</p>
            <p className="mt-0.5 whitespace-pre-line text-sm text-fg-secondary">{notesText}</p>
          </div>
        )}
        {/* A custom form carries no lines and no amount (CFM-002). */}
        {!isCfm && (
        <div className="col-span-2 border-t border-line-subtle pt-3 mt-1">
          <div className="flex justify-between text-sm">
            <span className="text-fg-muted">Subtotal (pre-tax)</span>
            <span className="font-mono">{formatAmount(claim.net_amount, claim.currency)}</span>
          </div>
          {Number(claim.tax_amount) > 0 && (
            <div className="flex justify-between text-sm mt-1">
              <span className="text-fg-muted">Tax (HST)</span>
              <span className="font-mono text-fg-secondary">{formatAmount(claim.tax_amount, claim.currency)}</span>
            </div>
          )}
          <div className="flex justify-between text-sm mt-1 font-semibold border-t border-line-subtle pt-2">
            <span>Total (Reimbursable)</span>
            <span className="font-mono text-primary-700">{formatAmount(claim.total_amount, claim.currency)}</span>
          </div>
        </div>
        )}
      </div>

      {/* Custom form answers, labelled as on the claim PDF (OA.md §6.2) */}
      {isCfm && claim.form_fields && (
        <div className="rounded-xl border border-line bg-surface p-5 flex flex-col gap-3">
          <h2 className="text-sm font-semibold text-fg-secondary">
            Form Details{claim.form_name ? ` — ${claim.form_name}` : ''}
          </h2>
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
            {claim.form_fields.map((f, i) => (
              <div key={i}>
                <dt className="text-fg-muted">{f.label}</dt>
                <dd className="whitespace-pre-line text-fg">{f.value || '—'}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {/* Travel Application details (TRA — no money/line items, has its own PDF) */}
      {claim.claim_type === 'TRA' && (
        <div className="rounded-xl border border-line bg-surface p-5 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-fg-secondary">Travel Application</h2>
            <Button variant="link" onClick={() => regenPdf.mutate()} disabled={regenPdf.isPending}
              className="text-xs">
              {regenPdf.isPending ? 'Generating…' : 'Regenerate PDF'}
            </Button>
          </div>
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <div><dt className="text-fg-muted">Travelers</dt>
              <dd>{(claim.travelers ?? []).map((t) => t.user_name).join(', ') || '—'}</dd></div>
            <div><dt className="text-fg-muted">Number of Persons</dt>
              <dd>{(claim.travelers ?? []).length}</dd></div>
            <div><dt className="text-fg-muted">Destination</dt><dd>{claim.travel_destination || '—'}</dd></div>
            <div><dt className="text-fg-muted">Dates</dt>
              <dd>{formatDate(claim.travel_from_date)} → {formatDate(claim.travel_to_date)}</dd></div>
            <div className="sm:col-span-2"><dt className="text-fg-muted">Reasons</dt><dd>{claim.purpose || '—'}</dd></div>
            <div className="sm:col-span-2"><dt className="text-fg-muted">Transportation & Accommodation</dt>
              <dd>{(claim.transport_modes ?? []).join(', ') || '—'}</dd></div>
            <div><dt className="text-fg-muted">Leave</dt>
              <dd>{claim.leave_from_date ? `${formatDate(claim.leave_from_date)} → ${formatDate(claim.leave_to_date)}` : '—'}</dd></div>
            <div className="sm:col-span-2"><dt className="text-fg-muted">Remarks</dt><dd>{claim.notes || '—'}</dd></div>
          </dl>
        </div>
      )}

      {/* Line items (EXP / TRV / CFM — MIL uses the trip log below, TRA has no line items) */}
      {claim.claim_type !== 'MIL' && claim.claim_type !== 'TRA' && claim.line_items.length > 0 && (
        <div className="rounded-xl border border-line bg-surface overflow-hidden">
          <div className="border-b border-line-subtle px-5 py-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-fg-secondary">Line Items</h2>
            {claim.claim_type === 'TRV' && claim.travel_country && claim.travel_country !== 'CA' && (
              <span className="text-xs text-fg-muted">Outside Canada ({claim.travel_country}) — no sales tax</span>
            )}
          </div>
          <Table density="compact">
            <THead>
              <Tr>
                <Th className="w-8">#</Th>
                <Th className="w-28">Date</Th>
                <Th>Description</Th>
                <Th>Budget Account</Th>
                <Th align="right">Total</Th>
                <Th align="right">Tax</Th>
                <Th align="right">Net</Th>
              </Tr>
            </THead>
            <TBody>
              {claim.line_items.map((li) => (
                <Tr key={li.id}>
                  <Td className="text-xs text-fg-muted font-mono">{li.line_number}</Td>
                  <Td className="text-xs text-fg-secondary">
                    {li.period_from
                      ? <>{formatDate(li.period_from)} –<br />{formatDate(li.period_to!)}</>
                      : formatDate(li.expense_date)}
                  </Td>
                  <Td className="text-xs text-fg">
                    {li.description}
                    {li.period_from && (
                      <div className="text-fg-muted">{li.period_days} days × {formatAmount(li.daily_rate ?? 0, claim.currency)}</div>
                    )}
                    {claim.claim_type === 'TRV' && li.tax_code && (
                      <div className="text-fg-muted">Tax {li.tax_code}</div>
                    )}
                    {li.invoice_amount != null && (
                      <div className="text-fg-muted">Invoice {formatAmount(li.invoice_amount, claim.currency)}</div>
                    )}
                  </Td>
                  <Td className="text-xs text-fg-secondary">{li.budget_account_code} — {li.budget_account_name}</Td>
                  <Td align="right" className="text-xs font-mono">{formatAmount(li.total_amount, claim.currency)}</Td>
                  <Td align="right" className="text-xs font-mono text-fg-muted">{formatAmount(li.tax_amount, claim.currency)}</Td>
                  <Td align="right" className="text-xs font-mono font-medium text-primary-700">{formatAmount(li.net_amount, claim.currency)}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
      )}

      {/* Trip items (MIL) */}
      {claim.claim_type === 'MIL' && claim.trip_items.length > 0 && (
        <div className="rounded-xl border border-line bg-surface overflow-hidden">
          <div className="border-b border-line-subtle px-5 py-3">
            <h2 className="text-sm font-semibold text-fg-secondary">Trip Log</h2>
          </div>
          <Table density="compact">
            <THead>
              <Tr>
                <Th className="w-8">#</Th>
                <Th className="w-28">Date</Th>
                <Th>Route</Th>
                <Th>Purpose</Th>
                <Th align="right">km</Th>
                <Th align="right">Rate</Th>
                <Th align="right">Amount</Th>
              </Tr>
            </THead>
            <TBody>
              {claim.trip_items.map((ti) => (
                <Tr key={ti.id}>
                  <Td className="text-xs text-fg-muted font-mono">{ti.trip_number}</Td>
                  <Td className="text-xs text-fg-secondary">{formatDate(ti.trip_date)}</Td>
                  <Td className="text-xs text-fg">
                    {ti.from_location} → {ti.to_location}
                    {ti.is_round_trip && <span className="ml-1.5 text-2xs text-fg-muted bg-surface-sunken rounded px-1 py-0.5">Round trip</span>}
                  </Td>
                  <Td className="text-xs text-fg-secondary">{ti.purpose}</Td>
                  <Td align="right" className="text-xs font-mono">{Number(ti.distance_km).toFixed(1)}{ti.is_round_trip ? ' ×2' : ''}</Td>
                  <Td align="right" className="text-xs font-mono text-fg-muted">${Number(ti.rate_per_km).toFixed(4)}</Td>
                  <Td align="right" className="text-xs font-mono font-medium text-primary-700">{formatAmount(ti.amount, claim.currency)}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
      )}

      {/* Attachment requirement hint */}
      {missingAttachment && (
        <div className="flex items-start gap-3 rounded-lg border border-warning-line bg-warning-soft px-4 py-3 text-sm text-warning-fg">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>At least one receipt attachment is required before this claim can be submitted.</span>
        </div>
      )}

      {/* Attachments */}
      <AttachmentsCard
        claimId={claim.id}
        canUpload={isEditable(claim.status)}
        // TRA keeps its own button on the Travel Application card.
        canRegeneratePdf={claim.claim_type !== 'TRA'
          && (claim.status === STATUS.APPROVED || claim.status === STATUS.PAID)}
      />
      </div>

      {/* Right: approval chain + history */}
      <div className="lg:col-span-4 flex flex-col gap-6 lg:sticky lg:top-6 self-start w-full">
        {/* Approval status — who has approved, who is pending */}
        <ApprovalStatusCard claimId={claim.id} />

        {/* Approval timeline — every action on the claim, with its comment */}
        {claim.approval_events.length > 0 && (
          <div className="rounded-xl border border-line bg-surface p-5">
            <h2 className="mb-4 text-sm font-semibold text-fg-secondary">Approval Timeline</h2>
            <div className="space-y-3">
              {claim.approval_events.map((evt) => (
                <div key={evt.id} className="flex items-start gap-3">
                  <div className="mt-0.5 shrink-0">{ACTION_ICONS[evt.action] ?? <CheckCircle className="h-4 w-4 text-fg-muted" />}</div>
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-x-2">
                      <span className="text-sm font-medium text-fg">{evt.actor_name}</span>
                      <span className="text-xs text-fg-muted">
                        {ACTION_LABELS[evt.action] ?? evt.action}
                        {stepLabel(evt) && ` at ${stepLabel(evt)}`}
                      </span>
                    </div>
                    <p className="text-xs text-fg-muted">{formatDate(evt.created_at)}</p>
                    {evt.comment && (
                      <p className="mt-1 whitespace-pre-line rounded bg-surface-subtle px-2 py-1.5 text-xs text-fg-secondary">{evt.comment}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      </div>

      {/* Mark as Processed modal */}
      {activeAction === ACTION.PAY && (
        <ProcessPaymentModal
          docNumber={claim.claim_number}
          currency={claim.currency}
          amount={Number(claim.total_amount)}
          busy={payMutation.isPending}
          error={payMutation.isError ? (payMutation.error instanceof Error ? payMutation.error.message : 'Payment failed') : undefined}
          onConfirm={(bankAccountId) => payMutation.mutate(bankAccountId)}
          onClose={() => { payMutation.reset(); setActiveAction(null) }}
        />
      )}

      {/* Action modal (submit / approve / return / reject) */}
      {activeAction && activeAction !== ACTION.PAY && (
        <ActionModal
          action={activeAction}
          docNumber={claim.claim_number}
          onClose={() => setActiveAction(null)}
          loading={actionMutation.isPending}
          error={actionMutation.isError ? (actionMutation.error as Error).message : undefined}
          onConfirm={(comment) => actionMutation.mutate({ action: activeAction, comment })}
        />
      )}

      {/* Delete confirmation (unapproved Travel Applications only) */}
      {confirmingDelete && (
        <ConfirmDialog
          title={`Delete ${claim.claim_number}?`}
          message="This travel application and its approval history will be permanently deleted. This cannot be undone."
          onConfirm={() => deleteClaim.mutate()}
          onClose={() => setConfirmingDelete(false)}
          loading={deleteClaim.isPending}
          error={deleteError}
        />
      )}
    </div>
  )
}
