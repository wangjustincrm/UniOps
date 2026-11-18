import { useState, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import { NotFoundState } from '@/components/shared/NotFoundState'
import { ChoiceCard, FileInput, Input, TBody, THead, Table, Td, Textarea, Th, Tr, useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { ArrowLeft, CheckCircle2, AlertTriangle, Upload, X, FileText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate, formatQty, todayISO } from '@/lib/utils'
import { useGr, useGrAction } from '@/hooks/useGrs'
import { useAuthStore } from '@/stores/auth.store'
import { isOverReceiptError } from '@/services/gr'
import { serviceAcceptanceNotes } from '@/lib/grNotes'

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
  const replaceTab = useReplaceTab(epmsRoutes)
  const { data: gr, isLoading } = useGr(id ?? '')
  useDocTabTitle(gr?.number && `Confirm ${gr.number}`)
  const grAction = useGrAction(id ?? '')
  const { user } = useAuthStore()

  const [completionDate, setCompletionDate] = useState(
    todayISO()
  )
  const [qualityRating, setQualityRating] = useState<QualityRating>('satisfactory')
  const [acceptanceNotes, setAcceptanceNotes] = useState('')
  const [issueDetails, setIssueDetails] = useState('')
  const [files, setFiles] = useState<UploadedFile[]>([])
  const [dragging, setDragging] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const [overReceiptReason, setOverReceiptReason] = useState('')
  // Asked for only when the server says confirming takes a PO line past its
  // order and the GR carries no reason yet (ISS-0169).
  const needsOverReason = isOverReceiptError(grAction.error)

  // ── Guard ──────────────────────────────────────────────────────────────────

  if (isLoading) return <div className="p-8 text-center text-fg-muted">Loading…</div>

  if (!gr) {
    return (
      <NotFoundState title="GR Not Found" message="The goods receipt you're looking for doesn't exist."
                     backTo="/gr" backLabel="Back to GR List" />
    )
  }

  if (gr.gr_type !== 'service') {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-sm text-fg-muted mb-2">Service confirm is for service GRs only</p>
        <BackLink to={`/gr/${gr.id}`} className="text-sm text-primary-600 hover:underline">← Back to GR</BackLink>
      </div>
    )
  }

  if (!['pending_ack', 'collection_pending'].includes(gr.status)) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-sm text-fg-muted mb-2">This GR is not pending confirmation (status: {gr.status})</p>
        <BackLink to={`/gr/${gr.id}`} className="text-sm text-primary-600 hover:underline">← Back to GR</BackLink>
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
    if (needsOverReason && !overReceiptReason.trim()) return

    // GR Detail reads the "Issues noted" part back (lib/grNotes, ISS-0179).
    const combinedNotes = serviceAcceptanceNotes(
      acceptanceNotes, qualityRating !== 'satisfactory' ? issueDetails : null)

    const apiAction = qualityRating === 'rejected' ? 'reject' : 'confirm'
    grAction.mutate(
      {
        action: apiAction, collected_by: user?.name, collection_notes: combinedNotes,
        over_receipt_reason: overReceiptReason.trim() || undefined,
      },
      { onSuccess: () => replaceTab(`/gr/${id}`) }
    )
  }

  const totalValue = gr.line_items.reduce((s, l) => s + Number(l.line_total), 0)

  // GR-S-005: a service GR can be rejected only once acknowledged
  // (collection_pending). From pending_ack the server refuses it with a 409,
  // so the option is not offered there (ISS-0121, ISS-0179).
  const canReject = gr.status === 'collection_pending'

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to={`/gr/${gr.id}`} className="text-fg-muted hover:text-fg-secondary transition-colors">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-primary-600" />
            <h1 className="text-2xl font-semibold text-fg">Confirm Service Completion</h1>
          </div>
          <p className="mt-1 text-sm text-fg-muted">
            {gr.number} · {gr.po_number} · {gr.vendor_name}
          </p>
        </div>
      </div>

      {/* Service summary */}
      <div className="rounded-xl border border-line bg-surface p-5">
        <h2 className="text-sm font-semibold text-fg mb-3">Service Details</h2>
        <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
          <MetaRow label="Vendor"         value={gr.vendor_name} />
          <MetaRow label="Recorded By"    value={gr.received_by} />
          <MetaRow label="Record Date"    value={formatDate(gr.received_at)} />
          <MetaRow label="Total Value"    value={formatAmount(totalValue, gr.currency)} mono />
        </div>

        {/* Service line items */}
        <div className="mt-4 rounded-lg border border-line overflow-hidden">
          <Table density="compact">
            <THead>
              <Tr className="bg-surface-subtle">
                <Th>#</Th>
                <Th>Description</Th>
                <Th align="right">Delivered</Th>
                <Th className="w-16">Unit</Th>
                <Th align="right">Value</Th>
              </Tr>
            </THead>
            <TBody>
              {gr.line_items.map((item, idx) => (
                <Tr key={item.id} className={cn('border-b border-line-subtle', idx % 2 === 1 && 'bg-surface-subtle/50')}>
                  <Td align="center" className="text-fg-muted text-xs ">{idx + 1}</Td>
                  <Td className="text-fg">{item.description}</Td>
                  <Td align="right" className="font-mono text-xs font-semibold text-fg">{formatQty(item.qty_received)}</Td>
                  <Td className="text-xs text-fg-muted">{item.unit}</Td>
                  <Td align="right" className="font-mono text-xs font-semibold text-fg">
                    {formatAmount(item.line_total, gr.currency)}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>

        {gr.notes && (
          <div className="mt-3 rounded-lg bg-surface-subtle border border-line px-4 py-2.5 text-xs text-fg-secondary">
            {/* Written by whoever recorded the receipt, not the vendor (ISS-0183). */}
            <span className="font-medium">Receipt notes: </span>{gr.notes}
          </div>
        )}
      </div>

      {/* Acceptance form */}
      <div className="rounded-xl border border-line bg-surface p-5 flex flex-col gap-5">
        <h2 className="text-sm font-semibold text-fg">Service Acceptance</h2>

        {/* Completion date */}
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-fg-secondary">
            Service Completion Date <span className="text-danger-fg">*</span>
          </label>
          <Input
            type="date"
            value={completionDate}
            onChange={(e) => setCompletionDate(e.target.value)}
            error={!!dateError} className="w-52"
          />
          {dateError && <p className="text-xs text-danger-fg">Required</p>}
        </div>

        {/* Quality rating */}
        <div className="flex flex-col gap-2">
          <label className="text-sm font-medium text-fg-secondary">Quality Assessment</label>
          <div className="flex gap-3">
            {([
              { value: 'satisfactory', label: 'Satisfactory',    icon: CheckCircle2,   on: 'border-success-400 bg-success-soft', iconCls: 'text-success-fg' },
              { value: 'issues',       label: 'Has Issues',      icon: AlertTriangle,  on: 'border-warning-400 bg-warning-soft', iconCls: 'text-warning-fg' },
              { value: 'rejected',     label: 'Reject Service',  icon: AlertTriangle,  on: 'border-danger-400 bg-danger-soft',   iconCls: 'text-danger-fg' },
            ] as const).filter((opt) => opt.value !== 'rejected' || canReject).map((opt) => {
              const Icon = opt.icon
              const selected = qualityRating === opt.value
              return (
                <ChoiceCard
                  key={opt.value}
                  selected={selected}
                  onClick={() => setQualityRating(opt.value)}
                  // The tone of each option carries its meaning (good / issues / reject).
                  className={cn('flex-row items-center gap-2 px-4 py-2.5 text-sm font-medium', selected && cn(opt.on, 'shadow-none'))}
                >
                  <Icon className={cn('h-4 w-4', selected ? opt.iconCls : 'text-fg-muted')} />
                  {opt.label}
                </ChoiceCard>
              )
            })}
          </div>

          {/* Issue details */}
          {qualityRating !== 'satisfactory' && (
            <div className="mt-1 flex flex-col gap-1">
              <label className="text-xs font-medium text-fg-secondary">
                Issue Details <span className="text-danger-fg">*</span>
              </label>
              <Textarea
                rows={2}
                placeholder="Describe the quality issues observed..."
                value={issueDetails}
                onChange={(e) => setIssueDetails(e.target.value)}
                error={!!issueError} warning className="resize-none"
              />
              {issueError && <p className="text-xs text-danger-fg">Required when issues are reported</p>}
            </div>
          )}
        </div>

        {/* Acceptance notes */}
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-fg-secondary">
            Acceptance Notes <span className="text-danger-fg">*</span>
          </label>
          <Textarea
            rows={3}
            placeholder="Confirm what was delivered, the quality of work, and any outstanding items (min 10 characters)..."
            value={acceptanceNotes}
            onChange={(e) => setAcceptanceNotes(e.target.value)}
            error={!!notesError} className="resize-none"
          />
          <div className="flex justify-between">
            {notesError
              ? <p className="text-xs text-danger-fg">Acceptance notes are required (min 10 characters)</p>
              : <p className="text-xs text-fg-muted">Briefly describe the service outcome and your acceptance decision</p>
            }
            <span className={cn('text-xs', acceptanceNotes.length < 10 ? 'text-fg-muted' : 'text-success-fg')}>
              {acceptanceNotes.length} / 10 min
            </span>
          </div>
        </div>

        {/* File upload */}
        <div className="flex flex-col gap-2">
          <label className="text-sm font-medium text-fg-secondary">Supporting Documents (Optional)</label>
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileRef.current?.click()}
            className={cn(
              'flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed py-8 cursor-pointer transition-colors',
              dragging
                ? 'border-primary-400 bg-primary-50'
                : 'border-line hover:border-primary-300 hover:bg-surface-subtle'
            )}
          >
            <Upload className={cn('h-8 w-8', dragging ? 'text-primary-500' : 'text-fg-faint')} />
            <p className="text-sm text-fg-muted">
              <span className="font-medium text-primary-600">Click to upload</span> or drag &amp; drop
            </p>
            <p className="text-xs text-fg-muted">Service completion certificate, photos, reports · Max 25 MB</p>
            <FileInput
              ref={fileRef}
             
              multiple
              className="hidden"
              onChange={(e) => addFiles(e.target.files)}
            />
          </div>

          {files.length > 0 && (
            <div className="flex flex-col gap-1.5 mt-1">
              {files.map((f, i) => (
                <div key={i} className="flex items-center justify-between rounded-lg border border-line bg-surface-subtle px-3 py-2">
                  <div className="flex items-center gap-2">
                    <FileText className="h-4 w-4 text-fg-muted" />
                    <span className="text-sm text-fg-secondary">{f.name}</span>
                    <span className="text-xs text-fg-muted">{f.size}</span>
                  </div>
                  <Button variant="danger-ghost" size="icon-xs" aria-label="Remove"
                    onClick={(e) => { e.stopPropagation(); setFiles((p) => p.filter((_, j) => j !== i)) }}
                   
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Confirmation summary banner */}
      {qualityRating === 'satisfactory' ? (
        <div className="rounded-lg border border-success-line bg-success-soft p-4 flex gap-3">
          <CheckCircle2 className="h-5 w-5 text-success-fg flex-shrink-0" />
          <p className="text-sm text-success-fg">
            Confirming service completion will mark this GR as <strong>Confirmed</strong> and record the service as
            received on the PO. The Payment Application follows once the vendor's invoice is matched to this PO.
          </p>
        </div>
      ) : qualityRating === 'issues' ? (
        <div className="rounded-lg border border-warning-line bg-warning-soft p-4 flex gap-3">
          <AlertTriangle className="h-5 w-5 text-warning-fg flex-shrink-0 mt-0.5" />
          <p className="text-sm text-warning-fg">
            The GR will still be confirmed; payment follows the matched invoice as usual. The issue details are kept in the
            GR's acceptance notes; the Procurement Officer is not notified, so tell them if the vendor needs to be contacted.
          </p>
        </div>
      ) : (
        <div className="rounded-lg border border-danger-line bg-danger-soft p-4 flex gap-3">
          <AlertTriangle className="h-5 w-5 text-danger-fg flex-shrink-0 mt-0.5" />
          <p className="text-sm text-danger-fg">
            <strong>Rejecting</strong> the service will mark this GR as <strong>Rejected</strong> and close its tasks. Nothing is
            received against the PO. The Procurement Officer is not notified, so tell them if the vendor needs to be contacted.
          </p>
        </div>
      )}

      {grAction.isError && (
        <div className="rounded-lg border border-warning-line bg-warning-soft p-4 flex gap-3">
          <AlertTriangle className="h-5 w-5 text-warning-fg flex-shrink-0 mt-0.5" />
          <div className="flex-1 flex flex-col gap-2">
            <p className="text-sm text-warning-fg">
              {grAction.error instanceof Error ? grAction.error.message : 'The confirmation could not be saved'}
            </p>
            {needsOverReason && (
              <>
                <label className="text-xs font-medium text-warning-fg">
                  Over-receipt reason <span className="text-danger-fg">*</span>
                </label>
                <Textarea
                  rows={2}
                  value={overReceiptReason}
                  onChange={(e) => setOverReceiptReason(e.target.value)}
                  placeholder="Why more than the PO line has open is being confirmed"
                  error={!!(submitted && !overReceiptReason.trim())} warning className="resize-none"
                />
              </>
            )}
          </div>
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
      <span className="text-fg-muted min-w-32 flex-shrink-0">{label}</span>
      <span className={cn('text-fg font-medium', mono && 'font-mono text-xs')}>{value}</span>
    </div>
  )
}
