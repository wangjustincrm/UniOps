import { useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, CheckCircle2, Clock, Truck, Paperclip,
  AlertTriangle, Package, ExternalLink, X, RotateCcw,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn, formatAmount, formatDate, formatDateTime } from '@/lib/utils'
import { useGr, useGrAction } from '@/hooks/useGrs'
import { usePo } from '@/hooks/usePos'
import { useAuthStore } from '@/stores/auth.store'
import { type GrStatus, type ApiGr, type ApiGrLineItem } from '@/services/gr'
import { useGrAttachments, useDeleteGrAttachment, useRegenerateGrPdf } from '@/hooks/useGrAttachments'
import { grAttachmentService } from '@/services/grAttachments'

// Statuses the backend accepts for POST /gr/{id}/attachments/regenerate-pdf
// (anything from the acknowledge step onward, excluding cancelled).
const GR_PDF_REGENERATABLE_STATUSES: GrStatus[] = ['collection_pending', 'collected', 'confirmed', 'discrepancy']

const CONDITION_CONFIG = {
  good:        { label: 'Good',        cls: 'bg-success-50 text-success-700' },
  discrepancy: { label: 'Discrepancy', cls: 'bg-warning-50 text-warning-700' },
  damaged:     { label: 'Damaged',     cls: 'bg-danger-50 text-danger-600'   },
}

// ─── GR Status helpers ────────────────────────────────────────────────────────

const GR_STATUS_CONFIG: Record<GrStatus, { label: string; variant: 'neutral' | 'warning' | 'info' | 'success' | 'danger'; dot: string }> = {
  pending_ack:        { label: 'Pending Acknowledgement', variant: 'warning',  dot: 'bg-warning-500' },
  collection_pending: { label: 'Collection Pending',      variant: 'warning',  dot: 'bg-warning-500' },
  collected:          { label: 'Collected',               variant: 'success',  dot: 'bg-success-600' },
  confirmed:          { label: 'Confirmed',               variant: 'success',  dot: 'bg-success-600' },
  discrepancy:        { label: 'Discrepancy',             variant: 'danger',   dot: 'bg-danger-600'  },
  cancelled:          { label: 'Cancelled',               variant: 'neutral',  dot: 'bg-neutral-400' },
}

function GrStatusBadge({ status }: { status: GrStatus }) {
  const cfg = GR_STATUS_CONFIG[status]
  return (
    <Badge variant={cfg.variant}>
      <span className={cn('size-1.5 rounded-full', cfg.dot)} />
      {cfg.label}
    </Badge>
  )
}

// ─── GR Timeline ─────────────────────────────────────────────────────────────

function GrTimeline({ gr }: { gr: ApiGr }) {
  const isPhysical = gr.gr_type === 'physical'

  const steps = [
    {
      key: 'received',
      label: isPhysical ? 'Goods Received' : 'Service Delivered',
      sublabel: gr.received_by ? `By ${gr.received_by}` : undefined,
      date: formatDateTime(gr.received_at),
      done: true,
      icon: Package,
    },
    {
      key: 'ack',
      label: 'Acknowledged',
      sublabel: gr.acknowledged_by ? `By ${gr.acknowledged_by}` : 'Awaiting requester',
      date: gr.acknowledged_at ? formatDateTime(gr.acknowledged_at) : undefined,
      done: !!gr.acknowledged_at,
      pending: !gr.acknowledged_at,
      icon: CheckCircle2,
    },
    isPhysical
      ? {
          key: 'collected',
          label: 'Collection Confirmed',
          sublabel: gr.collected_by ? `By ${gr.collected_by}` : 'Awaiting requester',
          date: gr.collected_at ? formatDateTime(gr.collected_at) : undefined,
          done: !!gr.collected_at,
          pending: !!gr.acknowledged_at && !gr.collected_at,
          icon: Truck,
        }
      : {
          key: 'confirmed',
          label: 'Service Confirmed',
          sublabel: gr.collected_by ? `By ${gr.collected_by}` : 'Awaiting requester',
          date: gr.collected_at ? formatDateTime(gr.collected_at) : undefined,
          done: gr.status === 'confirmed',
          pending: !!gr.acknowledged_at && gr.status !== 'confirmed',
          icon: CheckCircle2,
        },
  ]

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-5">
      <h3 className="text-sm font-semibold text-neutral-900 mb-5">GR Progress</h3>
      <div className="relative flex flex-col gap-0">
        {steps.map((step, i) => {
          const Icon = step.icon
          return (
            <div key={step.key} className="flex gap-3">
              {/* Connector line */}
              <div className="flex flex-col items-center">
                <div className={cn(
                  'flex h-7 w-7 items-center justify-center rounded-full ring-2 flex-shrink-0 z-10',
                  step.done
                    ? 'bg-success-600 ring-success-100 text-white'
                    : step.pending
                    ? 'bg-white ring-primary-400 text-primary-500 animate-pulse'
                    : 'bg-white ring-neutral-200 text-neutral-400'
                )}>
                  <Icon className="h-3.5 w-3.5" />
                </div>
                {i < steps.length - 1 && (
                  <div className={cn('w-0.5 flex-1 my-1', step.done ? 'bg-success-300' : 'bg-neutral-200')} style={{ minHeight: 24 }} />
                )}
              </div>
              {/* Content */}
              <div className="pb-5 min-w-0">
                <p className={cn('text-sm font-medium', step.done ? 'text-neutral-900' : 'text-neutral-500')}>
                  {step.label}
                </p>
                {step.sublabel && <p className="text-xs text-neutral-400 mt-0.5">{step.sublabel}</p>}
                {step.date && <p className="text-xs text-neutral-400 mt-0.5">{step.date}</p>}
                {step.pending && !step.done && (
                  <span className="inline-flex items-center gap-1 mt-1 text-xs text-primary-600">
                    <Clock className="h-3 w-3" /> Waiting
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── Acknowledge modal ────────────────────────────────────────────────────────

function AcknowledgeModal({
  grNumber,
  onConfirm,
  onCancel,
}: {
  grNumber: string
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
        <h2 className="text-lg font-semibold text-neutral-900">Acknowledge Receipt</h2>
        <p className="mt-2 text-sm text-neutral-600">
          Confirm that you have reviewed{' '}
          <span className="font-mono font-semibold">{grNumber}</span> and the goods/services
          have been received as described. For physical goods, you will be required to
          confirm collection separately.
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <Button variant="secondary" onClick={onCancel}>Cancel</Button>
          <Button onClick={onConfirm}>Confirm Acknowledgement</Button>
        </div>
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function GrDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { user } = useAuthStore()

  const { data: gr, isLoading } = useGr(id ?? '')
  const grAction = useGrAction(id ?? '')
  const { data: po } = usePo(gr?.po_id ?? '')
  const { data: attachments = [] } = useGrAttachments(id ?? '')
  const deleteAttachment = useDeleteGrAttachment(id ?? '')
  const regeneratePdf = useRegenerateGrPdf(id ?? '')

  const [activeTab, setActiveTab] = useState<'details' | 'attachments'>('details')
  const [showAckModal, setShowAckModal] = useState(false)

  if (isLoading) return <div className="p-8 text-center text-neutral-400">Loading…</div>

  if (!gr) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-lg font-semibold text-neutral-500">GR not found</p>
        <Link to="/gr" className="mt-4 text-sm text-primary-600 hover:underline">← Back to GR List</Link>
      </div>
    )
  }

  const totalValue = gr.line_items.reduce((s, l) => s + Number(l.line_total), 0)
  const hasDiscrepancy = gr.line_items.some((l) => l.condition !== 'good')

  const isRequesterRole = user?.role === 'requester' || user?.role === 'dept_manager' || user?.role === 'procurement_officer' || user?.role === 'procurement_manager' || user?.role === 'system_admin'

  // Physical pending_ack → acknowledge modal (transitions to collection_pending)
  const canAcknowledge = gr.status === 'pending_ack' && gr.gr_type === 'physical' && isRequesterRole

  // Service pending_ack → redirect to service-confirm page
  const canConfirmService = gr.status === 'pending_ack' && gr.gr_type === 'service' && isRequesterRole

  const canCollect = gr.status === 'collection_pending' && gr.gr_type === 'physical' &&
    (user?.role === 'requester' || user?.role === 'system_admin')

  const handleAcknowledge = () => {
    grAction.mutate(
      { action: 'acknowledge', acknowledged_by: user?.name },
      { onSuccess: () => setShowAckModal(false) }
    )
  }

  const tabs = [
    { key: 'details' as const,     label: 'Details' },
    { key: 'attachments' as const, label: 'Attachments' },
  ]

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <Link to="/gr" className="mt-1 text-neutral-400 hover:text-neutral-600 transition-colors">
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-neutral-900 font-mono">{gr.number}</h1>
              <GrStatusBadge status={gr.status} />
            </div>
            <p className="mt-1 text-sm text-neutral-500">
              {gr.title} · {gr.vendor_name} · Received {formatDate(gr.received_at)}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {canAcknowledge && (
            <Button onClick={() => setShowAckModal(true)} className="gap-2">
              <CheckCircle2 className="h-4 w-4" />
              Acknowledge Receipt
            </Button>
          )}
          {canConfirmService && (
            <Button onClick={() => navigate(`/gr/${gr.id}/service-confirm`)} className="gap-2">
              <CheckCircle2 className="h-4 w-4" />
              Confirm Service Completion
            </Button>
          )}
          {canCollect && (
            <Button onClick={() => navigate(`/gr/${gr.id}/collect`)} variant="secondary" className="gap-2 border-warning-400 text-warning-700 hover:bg-warning-50">
              <Truck className="h-4 w-4" />
              Confirm Collection
            </Button>
          )}
        </div>
      </div>

      {/* Discrepancy warning */}
      {gr.status === 'discrepancy' && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 p-4 flex gap-3">
          <AlertTriangle className="h-5 w-5 text-danger-600 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-danger-700">Discrepancy on record</p>
            <p className="text-sm text-danger-600 mt-0.5">
              One or more items were received with issues. Invoice creation is blocked until the
              Procurement Officer resolves the discrepancy.
            </p>
          </div>
        </div>
      )}

      {/* Collection pending reminder */}
      {gr.status === 'collection_pending' && gr.gr_type === 'physical' && (
        <div className="rounded-lg border border-warning-200 bg-warning-50 p-4 flex items-center justify-between gap-4">
          <div className="flex gap-3">
            <Truck className="h-5 w-5 text-warning-500 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-warning-700">Collection Required</p>
              <p className="text-sm text-warning-600 mt-0.5">
                Please go to <strong>{gr.storage_location}</strong> to collect your goods, then record the collection.
              </p>
            </div>
          </div>
          {canCollect && (
            <Button
              size="sm"
              variant="secondary"
              className="border-warning-400 text-warning-700 hover:bg-warning-100 flex-shrink-0"
              onClick={() => navigate(`/gr/${gr.id}/collect`)}
            >
              Collect Now
            </Button>
          )}
        </div>
      )}

      <div className="flex gap-6 items-start">
        {/* Main content */}
        <div className="flex-1 min-w-0 flex flex-col gap-4">
          {/* Tabs */}
          <div className="flex gap-1 border-b border-neutral-200">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => setActiveTab(t.key)}
                className={cn(
                  'px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px',
                  activeTab === t.key
                    ? 'border-primary-600 text-primary-600'
                    : 'border-transparent text-neutral-500 hover:text-neutral-700'
                )}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* Details tab */}
          {activeTab === 'details' && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {/* GR Info */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">Receipt Details</h3>
                <div className="flex flex-col gap-2">
                  <MetaRow label="GR Number"   value={gr.number} mono />
                  <MetaRow label="GR Type"     value={gr.gr_type === 'physical' ? 'Physical Goods' : 'Service'} />
                  <MetaRow label="Received By" value={gr.received_by} />
                  <MetaRow label="Received On" value={formatDate(gr.received_at)} />
                  {gr.storage_location && gr.storage_location !== '—' && (
                    <MetaRow label="Storage Location" value={gr.storage_location} />
                  )}
                  {gr.notes && <MetaRow label="Notes" value={gr.notes} />}
                </div>
              </div>
              {/* Linked Documents */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">Linked Documents</h3>
                <div className="flex flex-col gap-3">
                  {po && (
                    <div className="flex items-center justify-between rounded-lg border border-neutral-200 px-3 py-2.5">
                      <div>
                        <p className="text-xs text-neutral-400">Purchase Order</p>
                        <Link to={`/po/${gr.po_id}`} className="text-sm font-medium text-primary-600 hover:underline font-mono">
                          {gr.po_number}
                        </Link>
                      </div>
                      <Link to={`/po/${gr.po_id}`}>
                        <ExternalLink className="h-4 w-4 text-neutral-400 hover:text-primary-600" />
                      </Link>
                    </div>
                  )}
                </div>
              </div>
              {/* Value summary */}
              <div className="rounded-xl border border-neutral-200 bg-white p-5 sm:col-span-2">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">Value Summary</h3>
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <p className="text-xs text-neutral-400">Lines Received</p>
                    <p className="text-lg font-bold text-neutral-900 mt-0.5">{gr.line_items.length}</p>
                  </div>
                  <div>
                    <p className="text-xs text-neutral-400">Total Received Value</p>
                    <p className="text-lg font-bold text-neutral-900 mt-0.5 font-mono">{formatAmount(totalValue, gr.currency)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-neutral-400">Issues</p>
                    <p className={cn('text-lg font-bold mt-0.5', hasDiscrepancy ? 'text-danger-600' : 'text-success-600')}>
                      {hasDiscrepancy
                        ? `${gr.line_items.filter((l) => l.condition !== 'good').length} line(s)`
                        : 'None'}
                    </p>
                  </div>
                </div>
              </div>

              {/* Line Items */}
              <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden sm:col-span-2">
                <div className="px-5 py-3 border-b border-neutral-200 bg-neutral-50">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-400">
                    Line Items ({gr.line_items.length})
                  </h3>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-neutral-200 bg-neutral-50">
                        <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 w-8">#</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">Description</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500 w-24">Ordered</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500 w-24">Received</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 w-16">Unit</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-neutral-500 w-32">Line Total</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500 w-32">Condition</th>
                      </tr>
                    </thead>
                    <tbody>
                      {gr.line_items.map((line, idx) => (
                        <LineDetailRow key={line.id} line={line} idx={idx} currency={gr.currency} />
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="px-4 py-4 border-t border-neutral-200 flex justify-end">
                  <div className="min-w-52">
                    <div className="flex justify-between text-sm">
                      <span className="text-neutral-500">Total Received Value</span>
                      <span className="font-mono font-semibold text-neutral-900">{formatAmount(totalValue, gr.currency)}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Attachments tab */}
          {activeTab === 'attachments' && (
            <div className="rounded-xl border border-neutral-200 bg-white p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
                  Attachments
                </h2>
                {GR_PDF_REGENERATABLE_STATUSES.includes(gr.status) && (
                  <button
                    type="button"
                    onClick={() => regeneratePdf.mutate()}
                    disabled={regeneratePdf.isPending}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-100 disabled:opacity-50"
                    title="Generate the GR PDF and attach it (replaces the existing one)"
                  >
                    <RotateCcw className={`h-3.5 w-3.5 ${regeneratePdf.isPending ? 'animate-spin' : ''}`} />
                    {regeneratePdf.isPending ? 'Generating…' : 'Regenerate PDF'}
                  </button>
                )}
              </div>
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
                        onClick={() => grAttachmentService.download(id!, att.id, att.filename)}
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
        </div>

        {/* Right sidebar — Timeline */}
        <div className="w-64 flex-shrink-0">
          <GrTimeline gr={gr} />
        </div>
      </div>

      {/* Acknowledge modal */}
      {showAckModal && (
        <AcknowledgeModal
          grNumber={gr.number}
          onConfirm={handleAcknowledge}
          onCancel={() => setShowAckModal(false)}
        />
      )}
    </div>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function LineDetailRow({ line, idx, currency }: { line: ApiGrLineItem; idx: number; currency: string }) {
  const cond = CONDITION_CONFIG[line.condition]
  return (
    <>
      <tr className={cn('border-b border-neutral-100', idx % 2 === 1 && 'bg-neutral-50/50')}>
        <td className="px-4 py-3 text-center text-neutral-400 text-xs">{idx + 1}</td>
        <td className="px-4 py-3">
          <div className="text-neutral-800">{line.description}</div>
          {line.material_id && <div className="text-xs text-neutral-400 font-mono mt-0.5">{line.material_id}</div>}
        </td>
        <td className="px-4 py-3 text-right font-mono text-xs text-neutral-500">{line.qty_ordered}</td>
        <td className="px-4 py-3 text-right font-mono text-xs font-semibold text-neutral-900">{line.qty_received}</td>
        <td className="px-4 py-3 text-xs text-neutral-500">{line.unit}</td>
        <td className="px-4 py-3 text-right font-mono text-xs font-semibold text-neutral-900">
          {formatAmount(line.line_total, currency)}
        </td>
        <td className="px-4 py-3">
          <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium', cond.cls)}>
            {cond.label}
          </span>
        </td>
      </tr>
      {line.discrepancy_notes && (
        <tr className={cn('border-b border-neutral-100', idx % 2 === 1 && 'bg-neutral-50/50')}>
          <td />
          <td colSpan={6} className="px-4 pb-3">
            <div className="rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700">
              <span className="font-medium">Issue: </span>{line.discrepancy_notes}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

function MetaRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="text-neutral-400 min-w-36 flex-shrink-0">{label}</span>
      <span className={cn('text-neutral-800 font-medium', mono && 'font-mono text-xs')}>{value}</span>
    </div>
  )
}
