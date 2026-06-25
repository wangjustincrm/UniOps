import { useState, useRef } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, AlertTriangle, Upload, X, FileText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { useGr, useGrAction } from '@/hooks/useGrs'
import { useAuthStore } from '@/stores/auth.store'

type QualityRating = 'satisfactory' | 'issues' | 'rejected'

// ─── Uploaded file entry ──────────────────────────────────────────────────────

interface UploadedFile {
  name: string
  size: string
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function ServiceGrConfirmPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { data: gr, isLoading } = useGr(id ?? '')
  const grAction = useGrAction(id ?? '')
  const { user } = useAuthStore()

  const [completionDate, setCompletionDate] = useState(
    new Date().toISOString().slice(0, 10)
  )
  const [qualityRating, setQualityRating] = useState<QualityRating>('satisfactory')
  const [acceptanceNotes, setAcceptanceNotes] = useState('')
  const [issueDetails, setIssueDetails] = useState('')
  const [files, setFiles] = useState<UploadedFile[]>([])
  const [dragging, setDragging] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  // ── Guard ──────────────────────────────────────────────────────────────────

  if (isLoading) return <div className="p-8 text-center text-neutral-400">Loading…</div>

  if (!gr) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-lg font-semibold text-neutral-500">GR not found</p>
        <Link to="/gr" className="mt-4 text-sm text-primary-600 hover:underline">← Back to GR List</Link>
      </div>
    )
  }

  if (gr.gr_type !== 'service') {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-sm text-neutral-500 mb-2">Service confirm is for service GRs only</p>
        <Link to={`/gr/${gr.id}`} className="text-sm text-primary-600 hover:underline">← Back to GR</Link>
      </div>
    )
  }

  if (!['pending_ack', 'collection_pending'].includes(gr.status)) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-sm text-neutral-500 mb-2">This GR is not pending confirmation (status: {gr.status})</p>
        <Link to={`/gr/${gr.id}`} className="text-sm text-primary-600 hover:underline">← Back to GR</Link>
      </div>
    )
  }

  // ── File handling ──────────────────────────────────────────────────────────

  const addFiles = (fileList: FileList | null) => {
    if (!fileList) return
    const newFiles: UploadedFile[] = Array.from(fileList).map((f) => ({
      name: f.name,
      size: fmtSize(f.size),
    }))
    setFiles((prev) => [...prev, ...newFiles])
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    addFiles(e.dataTransfer.files)
  }

  // ── Validation ──────────────────────────────────────────────────────────────

  const notesError    = submitted && acceptanceNotes.trim().length < 10
  const issueError    = submitted && qualityRating !== 'satisfactory' && !issueDetails.trim()
  const dateError     = submitted && !completionDate

  // ── Submit ─────────────────────────────────────────────────────────────────

  const handleSubmit = () => {
    setSubmitted(true)
    if (!completionDate || acceptanceNotes.trim().length < 10) return
    if (qualityRating !== 'satisfactory' && !issueDetails.trim()) return

    const combinedNotes = [
      acceptanceNotes.trim(),
      qualityRating !== 'satisfactory' ? `Issues noted: ${issueDetails.trim()}` : '',
    ].filter(Boolean).join(' — ')

    const apiAction = qualityRating === 'rejected' ? 'reject' : 'confirm'
    grAction.mutate(
      { action: apiAction, collected_by: user?.name, collection_notes: combinedNotes },
      { onSuccess: () => navigate(`/gr/${id}`) }
    )
  }

  const totalValue = gr.line_items.reduce((s, l) => s + Number(l.line_total), 0)

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to={`/gr/${gr.id}`} className="text-neutral-400 hover:text-neutral-600 transition-colors">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-primary-600" />
            <h1 className="text-2xl font-bold text-neutral-900">Confirm Service Completion</h1>
          </div>
          <p className="mt-1 text-sm text-neutral-500">
            {gr.number} · {gr.po_number} · {gr.vendor_name}
          </p>
        </div>
      </div>

      {/* Service summary */}
      <div className="rounded-xl border border-neutral-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-neutral-900 mb-3">Service Details</h2>
        <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
          <MetaRow label="Vendor"         value={gr.vendor_name} />
          <MetaRow label="Recorded By"    value={gr.received_by} />
          <MetaRow label="Record Date"    value={formatDate(gr.received_at)} />
          <MetaRow label="Total Value"    value={formatAmount(totalValue, gr.currency)} mono />
        </div>

        {/* Service line items */}
        <div className="mt-4 rounded-lg border border-neutral-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">#</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Description</th>
                <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Delivered</th>
                <th className="px-4 py-2.5 text-left  text-xs font-semibold uppercase tracking-wide text-neutral-500 w-16">Unit</th>
                <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500">Value</th>
              </tr>
            </thead>
            <tbody>
              {gr.line_items.map((item, idx) => (
                <tr key={item.id} className={cn('border-b border-neutral-100', idx % 2 === 1 && 'bg-neutral-50/50')}>
                  <td className="px-4 py-3 text-neutral-400 text-xs text-center">{idx + 1}</td>
                  <td className="px-4 py-3 text-neutral-800">{item.description}</td>
                  <td className="px-4 py-3 text-right font-mono text-xs font-semibold text-neutral-900">{item.qty_received}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{item.unit}</td>
                  <td className="px-4 py-3 text-right font-mono text-xs font-semibold text-neutral-900">
                    {formatAmount(item.line_total, gr.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {gr.notes && (
          <div className="mt-3 rounded-lg bg-neutral-50 border border-neutral-200 px-4 py-2.5 text-xs text-neutral-600">
            <span className="font-medium">Vendor notes: </span>{gr.notes}
          </div>
        )}
      </div>

      {/* Acceptance form */}
      <div className="rounded-xl border border-neutral-200 bg-white p-5 flex flex-col gap-5">
        <h2 className="text-sm font-semibold text-neutral-900">Service Acceptance</h2>

        {/* Completion date */}
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-neutral-700">
            Service Completion Date <span className="text-danger-600">*</span>
          </label>
          <input
            type="date"
            value={completionDate}
            onChange={(e) => setCompletionDate(e.target.value)}
            className={cn(
              'w-52 h-10 px-3 rounded-lg border text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
              dateError ? 'border-danger-400' : 'border-neutral-300'
            )}
          />
          {dateError && <p className="text-xs text-danger-600">Required</p>}
        </div>

        {/* Quality rating */}
        <div className="flex flex-col gap-2">
          <label className="text-sm font-medium text-neutral-700">Quality Assessment</label>
          <div className="flex gap-3">
            {([
              { value: 'satisfactory', label: 'Satisfactory',    icon: CheckCircle2,   on: 'border-success-400 bg-success-50', iconCls: 'text-success-600' },
              { value: 'issues',       label: 'Has Issues',      icon: AlertTriangle,  on: 'border-warning-400 bg-warning-50', iconCls: 'text-warning-500' },
              { value: 'rejected',     label: 'Reject Service',  icon: AlertTriangle,  on: 'border-danger-400 bg-danger-50',   iconCls: 'text-danger-600' },
            ] as const).map((opt) => {
              const Icon = opt.icon
              const selected = qualityRating === opt.value
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setQualityRating(opt.value)}
                  className={cn(
                    'flex items-center gap-2 px-4 py-2.5 rounded-lg border-2 text-sm font-medium transition-colors',
                    selected ? opt.on : 'border-neutral-200 text-neutral-500 hover:border-neutral-300'
                  )}
                >
                  <Icon className={cn('h-4 w-4', selected ? opt.iconCls : 'text-neutral-400')} />
                  {opt.label}
                </button>
              )
            })}
          </div>

          {/* Issue details */}
          {qualityRating !== 'satisfactory' && (
            <div className="mt-1 flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-700">
                Issue Details <span className="text-danger-600">*</span>
              </label>
              <textarea
                rows={2}
                placeholder="Describe the quality issues observed..."
                value={issueDetails}
                onChange={(e) => setIssueDetails(e.target.value)}
                className={cn(
                  'w-full px-3 py-2 rounded-lg border text-sm focus:outline-none focus:ring-1 focus:ring-warning-400 resize-none',
                  issueError ? 'border-danger-400' : 'border-warning-300 bg-warning-50'
                )}
              />
              {issueError && <p className="text-xs text-danger-600">Required when issues are reported</p>}
            </div>
          )}
        </div>

        {/* Acceptance notes */}
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-neutral-700">
            Acceptance Notes <span className="text-danger-600">*</span>
          </label>
          <textarea
            rows={3}
            placeholder="Confirm what was delivered, the quality of work, and any outstanding items (min 10 characters)..."
            value={acceptanceNotes}
            onChange={(e) => setAcceptanceNotes(e.target.value)}
            className={cn(
              'w-full px-3 py-2 rounded-lg border text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 resize-none',
              notesError ? 'border-danger-400' : 'border-neutral-300'
            )}
          />
          <div className="flex justify-between">
            {notesError
              ? <p className="text-xs text-danger-600">Acceptance notes are required (min 10 characters)</p>
              : <p className="text-xs text-neutral-400">Briefly describe the service outcome and your acceptance decision</p>
            }
            <span className={cn('text-xs', acceptanceNotes.length < 10 ? 'text-neutral-400' : 'text-success-600')}>
              {acceptanceNotes.length} / 10 min
            </span>
          </div>
        </div>

        {/* File upload */}
        <div className="flex flex-col gap-2">
          <label className="text-sm font-medium text-neutral-700">Supporting Documents (Optional)</label>
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileRef.current?.click()}
            className={cn(
              'flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed py-8 cursor-pointer transition-colors',
              dragging
                ? 'border-primary-400 bg-primary-50'
                : 'border-neutral-200 hover:border-primary-300 hover:bg-neutral-50'
            )}
          >
            <Upload className={cn('h-8 w-8', dragging ? 'text-primary-500' : 'text-neutral-300')} />
            <p className="text-sm text-neutral-500">
              <span className="font-medium text-primary-600">Click to upload</span> or drag &amp; drop
            </p>
            <p className="text-xs text-neutral-400">Service completion certificate, photos, reports · Max 25 MB</p>
            <input
              ref={fileRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => addFiles(e.target.files)}
            />
          </div>

          {files.length > 0 && (
            <div className="flex flex-col gap-1.5 mt-1">
              {files.map((f, i) => (
                <div key={i} className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <FileText className="h-4 w-4 text-neutral-400" />
                    <span className="text-sm text-neutral-700">{f.name}</span>
                    <span className="text-xs text-neutral-400">{f.size}</span>
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); setFiles((p) => p.filter((_, j) => j !== i)) }}
                    className="text-neutral-300 hover:text-danger-500 transition-colors"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Confirmation summary banner */}
      {qualityRating === 'satisfactory' ? (
        <div className="rounded-lg border border-success-200 bg-success-50 p-4 flex gap-3">
          <CheckCircle2 className="h-5 w-5 text-success-600 flex-shrink-0" />
          <p className="text-sm text-success-700">
            Confirming service completion will mark this GR as <strong>Confirmed</strong> and unlock
            Payment Application creation.
          </p>
        </div>
      ) : qualityRating === 'issues' ? (
        <div className="rounded-lg border border-warning-200 bg-warning-50 p-4 flex gap-3">
          <AlertTriangle className="h-5 w-5 text-warning-500 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-warning-700">
            Submitting with issues will notify the Procurement Officer. The GR will still be confirmed and Payment Application can proceed.
          </p>
        </div>
      ) : (
        <div className="rounded-lg border border-danger-200 bg-danger-50 p-4 flex gap-3">
          <AlertTriangle className="h-5 w-5 text-danger-600 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-danger-700">
            <strong>Rejecting</strong> the service will mark this GR as <strong>Rejected</strong> and notify the Procurement Officer to liaise with the vendor. Payment Application will not be created.
          </p>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between pb-6">
        <Link to={`/gr/${gr.id}`}>
          <Button variant="secondary">Cancel</Button>
        </Link>
        <Button
          onClick={handleSubmit}
          className="gap-2"
          variant={qualityRating === 'rejected' ? 'destructive' : 'primary'}
        >
          <CheckCircle2 className="h-4 w-4" />
          {qualityRating === 'rejected' ? 'Reject Service' : 'Confirm Service Completion'}
        </Button>
      </div>
    </div>
  )
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function MetaRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-2">
      <span className="text-neutral-400 min-w-32 flex-shrink-0">{label}</span>
      <span className={cn('text-neutral-800 font-medium', mono && 'font-mono text-xs')}>{value}</span>
    </div>
  )
}
