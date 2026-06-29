import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useRef } from 'react'
import { ArrowLeft, CheckCircle, XCircle, RotateCcw, Banknote, AlertTriangle, Paperclip, Upload, Download, Trash2, Circle, Clock } from 'lucide-react'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { api } from '@/lib/api'
import ProcessPaymentModal from '@/components/ProcessPaymentModal'
import { StatusBadge } from '@/components/ui/badge'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ApprovalEvent {
  id: string
  actor_name: string
  action: string
  comment: string | null
  from_status: string
  to_status: string
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
}

// ── Badges ────────────────────────────────────────────────────────────────────

const ACTION_ICONS: Record<string, React.ReactNode> = {
  submit:  <CheckCircle className="h-4 w-4 text-blue-500" />,
  approve: <CheckCircle className="h-4 w-4 text-green-500" />,
  reject:  <XCircle className="h-4 w-4 text-red-500" />,
  return:  <RotateCcw className="h-4 w-4 text-orange-500" />,
  pay:     <Banknote className="h-4 w-4 text-emerald-500" />,
}

// ── Action modal ──────────────────────────────────────────────────────────────

function ActionModal({
  action, onConfirm, onClose, loading,
}: {
  action: string
  onConfirm: (comment: string) => void
  onClose: () => void
  loading: boolean
}) {
  const [comment, setComment] = useState('')
  const labels: Record<string, { title: string; color: string }> = {
    submit:  { title: 'Submit for Approval', color: 'bg-[#085E5E] text-white' },
    approve: { title: 'Approve Claim', color: 'bg-green-600 text-white' },
    reject:  { title: 'Reject Claim', color: 'bg-red-600 text-white' },
    return:  { title: 'Return for Revision', color: 'bg-orange-500 text-white' },
    pay:     { title: 'Record Payment', color: 'bg-emerald-600 text-white' },
  }
  const cfg = labels[action] ?? { title: action, color: 'bg-neutral-800 text-white' }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm rounded-xl border border-neutral-200 bg-white shadow-xl">
        <div className="p-5">
          <h3 className="text-base font-semibold text-neutral-900">{cfg.title}</h3>
          <div className="mt-3">
            <label className="mb-1 block text-xs font-medium text-neutral-600">
              Comment {action === 'return' || action === 'reject' ? '(required)' : '(optional)'}
            </label>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={3}
              className="w-full rounded border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 resize-none"
              placeholder="Add a comment…"
            />
          </div>
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
            disabled={loading || ((action === 'return' || action === 'reject') && !comment.trim())}
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

// ── Attachments card ──────────────────────────────────────────────────────────

function AttachmentsCard({ claimId, canUpload }: { claimId: string; canUpload: boolean }) {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')

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

  function formatSize(bytes: number) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-neutral-100">
        <h2 className="text-sm font-semibold text-neutral-700 flex items-center gap-2">
          <Paperclip className="h-4 w-4 text-neutral-400" />
          Attachments {attachments.length > 0 && `(${attachments.length})`}
        </h2>
        {canUpload && (
          <button
            type="button"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
            className="flex items-center gap-1.5 text-xs font-medium text-primary-700 hover:text-primary-900 disabled:opacity-50"
          >
            <Upload className="h-3.5 w-3.5" />
            {uploading ? 'Uploading…' : 'Upload file'}
          </button>
        )}
        <input ref={fileRef} type="file" className="hidden" onChange={handleFileChange}
          accept=".pdf,.jpg,.jpeg,.png,.webp,.xlsx,.csv,.doc,.docx" />
      </div>

      {uploadError && (
        <div className="mx-5 mt-3 flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
          <AlertTriangle className="h-4 w-4 shrink-0" />{uploadError}
        </div>
      )}

      {attachments.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 text-center text-sm text-neutral-400">
          <Paperclip className="h-6 w-6 mb-2 opacity-40" />
          No attachments
          {canUpload && <p className="text-xs mt-1">Click "Upload file" to add receipts or supporting documents</p>}
        </div>
      ) : (
        <div className="divide-y divide-neutral-100">
          {attachments.map(att => (
            <div key={att.id} className="flex items-center gap-3 px-5 py-3">
              <Paperclip className="h-4 w-4 text-neutral-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-neutral-800 truncate">{att.file_name}</p>
                <p className="text-xs text-neutral-400">{formatSize(att.file_size_bytes)}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {att.download_url && (
                  <a href={att.download_url} target="_blank" rel="noreferrer"
                    className="flex items-center gap-1 text-xs font-medium text-primary-600 hover:text-primary-800">
                    <Download className="h-3.5 w-3.5" />Download
                  </a>
                )}
                {canUpload && (
                  <button onClick={() => deleteMutation.mutate(att.id)}
                    className="text-neutral-300 hover:text-red-500 transition-colors">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
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

function ApprovalStatusCard({ claimId }: { claimId: string }) {
  const { data: steps = [], isLoading } = useQuery<ApprovalStep[]>({
    queryKey: ['approval-status', claimId],
    queryFn: () => api.get<ApprovalStep[]>(`/api/v1/expenses/${claimId}/approval-status`),
  })

  if (isLoading || steps.length === 0) return null

  const approvedCount = steps.filter(s => s.state === 'approved').length

  const stateIcon = (state: ApprovalStep['state']) => {
    switch (state) {
      case 'approved': return <CheckCircle className="h-5 w-5 text-green-500" />
      case 'current':  return <Clock className="h-5 w-5 text-[#085E5E]" />
      case 'returned': return <RotateCcw className="h-5 w-5 text-orange-500" />
      case 'rejected': return <XCircle className="h-5 w-5 text-red-500" />
      default:         return <Circle className="h-5 w-5 text-neutral-300" />
    }
  }
  const stateLabel: Record<ApprovalStep['state'], string> = {
    approved: 'Approved', current: 'Pending', waiting: 'Waiting',
    returned: 'Returned', rejected: 'Rejected',
  }
  const stateBadge: Record<ApprovalStep['state'], string> = {
    approved: 'bg-green-50 text-green-700',
    current:  'bg-[#085E5E]/10 text-[#085E5E]',
    waiting:  'bg-neutral-100 text-neutral-500',
    returned: 'bg-orange-50 text-orange-700',
    rejected: 'bg-red-50 text-red-700',
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-neutral-100">
        <h2 className="text-sm font-semibold text-neutral-700">Approval Status</h2>
        <span className="text-xs text-neutral-400">{approvedCount} of {steps.length} approved</span>
      </div>
      <ol className="divide-y divide-neutral-50">
        {steps.map((s, i) => (
          <li key={s.step_idx} className="flex items-start gap-3 px-5 py-3">
            <div className="flex flex-col items-center">
              {stateIcon(s.state)}
              {i < steps.length - 1 && <div className="mt-1 h-6 w-px bg-neutral-200" />}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-neutral-800">{s.label}</span>
                <span className={cn('rounded-full px-2 py-0.5 text-[10px] font-medium', stateBadge[s.state])}>
                  {stateLabel[s.state]}
                </span>
              </div>
              {s.state === 'approved' && s.actor_name && (
                <p className="mt-0.5 text-xs text-neutral-500">
                  by {s.actor_name}{s.acted_at ? ` · ${formatDate(s.acted_at)}` : ''}
                </p>
              )}
              {s.state === 'current' && (
                <p className="mt-0.5 text-xs text-neutral-400">Awaiting this approver</p>
              )}
              {s.comment && (
                <p className="mt-0.5 text-xs text-neutral-600 italic">"{s.comment}"</p>
              )}
            </div>
            <span className="shrink-0 text-[10px] font-mono text-neutral-300">Step {s.step_idx + 1}</span>
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

  // Shared with AttachmentsCard (same query key) — used to gate submit (EXP-007/TRV-008).
  const { data: attachments = [] } = useQuery<Attachment[]>({
    queryKey: ['expense-attachments', id],
    queryFn: () => api.get<Attachment[]>(`/api/v1/expenses/${id}/attachments`),
  })

  // Server-computed permissions — approval roles (Finance BP, etc.) are assignments,
  // not JWT roles, so authorization is resolved server-side via tasks + role_management.
  const { data: perms } = useQuery<{ is_owner: boolean; can_approve: boolean; can_pay: boolean }>({
    queryKey: ['expense-permissions', id],
    queryFn: () => api.get(`/api/v1/expenses/${id}/permissions`),
  })

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
  const payMutation = useMutation({
    mutationFn: (bankAccountId: string) => api.post(`/api/v1/expenses/${id}/pay`, { bank_account_id: bankAccountId }),
    onSuccess: () => { setActiveAction(null); invalidateAll() },
  })

  if (isLoading) {
    return <div className="flex items-center justify-center py-24 text-sm text-neutral-400">Loading…</div>
  }
  if (!claim) {
    return <div className="py-24 text-center text-sm text-red-500">Expense claim not found</div>
  }

  const TYPE_LABELS: Record<string, string> = {
    EXP: 'General Expense', MIL: 'Mileage Claim', TRV: 'Travel Expense',
  }
  const typeLabel = TYPE_LABELS[claim.claim_type]
    ?? (claim.claim_type.startsWith('CFM') ? `Custom Form (${claim.claim_type.replace(/^CFM_?/, '')})` : claim.claim_type)

  // EXP-007 / TRV-008: receipt-based claims require ≥1 attachment before submission.
  const requiresAttachment = ['EXP', 'TRV'].includes(claim.claim_type)
  const missingAttachment = requiresAttachment && claim.status === 'draft' && attachments.length === 0

  // Permissions resolved server-side (see the /permissions query above).
  const isOwner = perms?.is_owner ?? false
  const canApprove = perms?.can_approve ?? false
  const canPay = perms?.can_pay ?? false

  return (
    <div className="flex flex-col gap-6 max-w-4xl">
      {/* Back + header */}
      <div>
        <button
          onClick={() => navigate('/expenses')}
          className="mb-4 flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700"
        >
          <ArrowLeft className="h-4 w-4" />
          Expense Claims
        </button>
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-neutral-900">{claim.claim_number}</h1>
              <StatusBadge status={claim.status} />
              {claim.is_over_budget && (
                <span className="flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200 px-2 py-0.5 text-xs text-amber-700">
                  <AlertTriangle className="h-3 w-3" />
                  Over Budget
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-neutral-500">
              {typeLabel} · {claim.employee_name} · {claim.department_name}
            </p>
          </div>

          {/* Action buttons */}
          <div className="flex gap-2">
            {claim.status === 'draft' && isOwner && (
              <button
                onClick={() => setActiveAction('submit')}
                disabled={missingAttachment}
                title={missingAttachment ? 'Attach at least one receipt before submitting' : undefined}
                className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Submit
              </button>
            )}
            {claim.status === 'returned' && isOwner && (
              <button
                onClick={() => navigate(`/expenses/edit/${claim.id}`)}
                className="rounded-lg border border-neutral-200 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
              >
                Edit
              </button>
            )}
            {(claim.status === 'submitted' || claim.status === 'in_review') && canApprove && (
              <>
                <button
                  onClick={() => setActiveAction('return')}
                  className="rounded-lg border border-orange-200 px-4 py-2 text-sm font-medium text-orange-700 hover:bg-orange-50"
                >
                  Return
                </button>
                <button
                  onClick={() => setActiveAction('reject')}
                  className="rounded-lg border border-red-200 px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50"
                >
                  Reject
                </button>
                <button
                  onClick={() => setActiveAction('approve')}
                  className="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700"
                >
                  Approve
                </button>
              </>
            )}
            {claim.status === 'approved' && canPay && (
              <button
                onClick={() => setActiveAction('pay')}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700"
              >
                Mark as Processed
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Claim summary */}
      <div className="grid grid-cols-2 gap-4 rounded-xl border border-neutral-200 bg-white p-5">
        <div>
          <p className="text-xs font-medium text-neutral-500">Submission Date</p>
          <p className="mt-0.5 text-sm font-medium text-neutral-800">{formatDate(claim.submission_date)}</p>
        </div>
        <div>
          <p className="text-xs font-medium text-neutral-500">Currency</p>
          <p className="mt-0.5 text-sm font-medium text-neutral-800">{claim.currency}</p>
        </div>
        {claim.vehicle_description && (
          <div>
            <p className="text-xs font-medium text-neutral-500">Vehicle</p>
            <p className="mt-0.5 text-sm font-medium text-neutral-800">{claim.vehicle_description}</p>
          </div>
        )}
        {claim.total_km && (
          <div>
            <p className="text-xs font-medium text-neutral-500">Total km</p>
            <p className="mt-0.5 text-sm font-mono font-medium text-neutral-800">{Number(claim.total_km).toFixed(1)} km</p>
          </div>
        )}
        {claim.notes && (
          <div className="col-span-2">
            <p className="text-xs font-medium text-neutral-500">Notes</p>
            <p className="mt-0.5 text-sm text-neutral-700">{claim.notes}</p>
          </div>
        )}
        <div className="col-span-2 border-t border-neutral-100 pt-3 mt-1">
          <div className="flex justify-between text-sm">
            <span className="text-neutral-500">Subtotal (pre-tax)</span>
            <span className="font-mono">{formatAmount(claim.net_amount, claim.currency)}</span>
          </div>
          {Number(claim.tax_amount) > 0 && (
            <div className="flex justify-between text-sm mt-1">
              <span className="text-neutral-500">Tax (HST)</span>
              <span className="font-mono text-neutral-600">{formatAmount(claim.tax_amount, claim.currency)}</span>
            </div>
          )}
          <div className="flex justify-between text-sm mt-1 font-semibold border-t border-neutral-100 pt-2">
            <span>Total (Reimbursable)</span>
            <span className="font-mono text-primary-700">{formatAmount(claim.total_amount, claim.currency)}</span>
          </div>
        </div>
      </div>

      {/* Approval status — who has approved, who is pending */}
      <ApprovalStatusCard claimId={claim.id} />

      {/* Line items (EXP / TRV / CFM — MIL uses the trip log below) */}
      {claim.claim_type !== 'MIL' && claim.line_items.length > 0 && (
        <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
          <div className="border-b border-neutral-100 px-5 py-3">
            <h2 className="text-sm font-semibold text-neutral-700">Line Items</h2>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 border-b border-neutral-100">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium text-neutral-500 w-8">#</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-neutral-500 w-28">Date</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-neutral-500">Description</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-neutral-500">Budget Account</th>
                <th className="px-4 py-2 text-right text-xs font-medium text-neutral-500">Total</th>
                <th className="px-4 py-2 text-right text-xs font-medium text-neutral-500">Tax</th>
                <th className="px-4 py-2 text-right text-xs font-medium text-neutral-500">Net</th>
              </tr>
            </thead>
            <tbody>
              {claim.line_items.map((li) => (
                <tr key={li.id} className="border-b border-neutral-100">
                  <td className="px-4 py-2 text-xs text-neutral-400 font-mono">{li.line_number}</td>
                  <td className="px-4 py-2 text-xs text-neutral-600">{formatDate(li.expense_date)}</td>
                  <td className="px-4 py-2 text-xs text-neutral-800">{li.description}</td>
                  <td className="px-4 py-2 text-xs text-neutral-600">{li.budget_account_code} — {li.budget_account_name}</td>
                  <td className="px-4 py-2 text-right text-xs font-mono">{formatAmount(li.total_amount)}</td>
                  <td className="px-4 py-2 text-right text-xs font-mono text-neutral-500">{formatAmount(li.tax_amount)}</td>
                  <td className="px-4 py-2 text-right text-xs font-mono font-medium text-primary-700">{formatAmount(li.net_amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Trip items (MIL) */}
      {claim.claim_type === 'MIL' && claim.trip_items.length > 0 && (
        <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
          <div className="border-b border-neutral-100 px-5 py-3">
            <h2 className="text-sm font-semibold text-neutral-700">Trip Log</h2>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 border-b border-neutral-100">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium text-neutral-500 w-8">#</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-neutral-500 w-28">Date</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-neutral-500">Route</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-neutral-500">Purpose</th>
                <th className="px-4 py-2 text-right text-xs font-medium text-neutral-500">km</th>
                <th className="px-4 py-2 text-right text-xs font-medium text-neutral-500">Rate</th>
                <th className="px-4 py-2 text-right text-xs font-medium text-neutral-500">Amount</th>
              </tr>
            </thead>
            <tbody>
              {claim.trip_items.map((ti) => (
                <tr key={ti.id} className="border-b border-neutral-100">
                  <td className="px-4 py-2 text-xs text-neutral-400 font-mono">{ti.trip_number}</td>
                  <td className="px-4 py-2 text-xs text-neutral-600">{formatDate(ti.trip_date)}</td>
                  <td className="px-4 py-2 text-xs text-neutral-800">
                    {ti.from_location} → {ti.to_location}
                    {ti.is_round_trip && <span className="ml-1.5 text-[10px] text-neutral-400 bg-neutral-100 rounded px-1 py-0.5">Round trip</span>}
                  </td>
                  <td className="px-4 py-2 text-xs text-neutral-600">{ti.purpose}</td>
                  <td className="px-4 py-2 text-right text-xs font-mono">{Number(ti.distance_km).toFixed(1)}{ti.is_round_trip ? ' ×2' : ''}</td>
                  <td className="px-4 py-2 text-right text-xs font-mono text-neutral-500">${Number(ti.rate_per_km).toFixed(4)}</td>
                  <td className="px-4 py-2 text-right text-xs font-mono font-medium text-primary-700">{formatAmount(ti.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Attachment requirement hint */}
      {missingAttachment && (
        <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>At least one receipt attachment is required before this claim can be submitted.</span>
        </div>
      )}

      {/* Attachments */}
      <AttachmentsCard claimId={claim.id} canUpload={['draft', 'returned'].includes(claim.status)} />

      {/* Approval timeline */}
      {claim.approval_events.length > 0 && (
        <div className="rounded-xl border border-neutral-200 bg-white p-5">
          <h2 className="mb-4 text-sm font-semibold text-neutral-700">Approval Timeline</h2>
          <div className="space-y-3">
            {claim.approval_events.map((evt) => (
              <div key={evt.id} className="flex items-start gap-3">
                <div className="mt-0.5 shrink-0">{ACTION_ICONS[evt.action] ?? <CheckCircle className="h-4 w-4 text-neutral-400" />}</div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-neutral-800">{evt.actor_name}</span>
                    <span className="text-xs text-neutral-500 capitalize">{evt.action}d</span>
                    <span className="text-xs text-neutral-400">{formatDate(evt.created_at)}</span>
                  </div>
                  {evt.comment && (
                    <p className="mt-0.5 text-xs text-neutral-600 italic">"{evt.comment}"</p>
                  )}
                </div>
                <span className="shrink-0">
                  <StatusBadge status={evt.to_status} />
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Mark as Processed modal */}
      {activeAction === 'pay' && (
        <ProcessPaymentModal
          docNumber={claim.claim_number}
          currency={claim.currency}
          amount={Number(claim.total_amount)}
          busy={payMutation.isPending}
          onConfirm={(bankAccountId) => payMutation.mutate(bankAccountId)}
          onClose={() => setActiveAction(null)}
        />
      )}

      {/* Action modal (submit / approve / return / reject) */}
      {activeAction && activeAction !== 'pay' && (
        <ActionModal
          action={activeAction}
          onClose={() => setActiveAction(null)}
          loading={actionMutation.isPending}
          onConfirm={(comment) => actionMutation.mutate({ action: activeAction, comment })}
        />
      )}

      {/* Action error */}
      {actionMutation.isError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {(actionMutation.error as Error).message}
        </div>
      )}
    </div>
  )
}
