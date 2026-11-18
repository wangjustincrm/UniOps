import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import { NotFoundState } from '@/components/shared/NotFoundState'
import { useReplaceTab, Input, Radio, Textarea } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { ArrowLeft, Truck, CheckCircle2, AlertTriangle, Package } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate, formatQty, todayISO } from '@/lib/utils'
import { useGr, useGrAction } from '@/hooks/useGrs'
import { useAuthStore } from '@/stores/auth.store'
import { useConfig } from '@/hooks/useConfig'
import { collectionNotesWithLines } from '@/lib/grNotes'
import { type GrLineCondition, isOverReceiptError } from '@/services/gr'

// ─── Condition options ────────────────────────────────────────────────────────

const CONDITIONS: { value: GrLineCondition; label: string; color: string }[] = [
  { value: 'good',        label: 'Good',        color: 'text-success-fg' },
  { value: 'discrepancy', label: 'Discrepancy', color: 'text-warning-fg' },
  { value: 'damaged',     label: 'Damaged',     color: 'text-danger-fg'  },
]

// ─── Per-line collection state ────────────────────────────────────────────────

interface CollectionLine {
  id: string
  condition: GrLineCondition
  collectionNotes: string
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function CollectionConfirmPage() {
  const { id } = useParams<{ id: string }>()
  const replaceTab = useReplaceTab(epmsRoutes)
  const { data: gr, isLoading } = useGr(id ?? '')
  useDocTabTitle(gr?.number && `Collect ${gr.number}`)
  const grAction = useGrAction(id ?? '')
  const { user } = useAuthStore()
  const collectionGateOn = !!useConfig().data?.collection_config?.collection_gate_enabled

  const [collectionDate, setCollectionDate] = useState(
    todayISO()
  )
  const [generalNotes, setGeneralNotes] = useState('')
  const [lines, setLines] = useState<CollectionLine[]>([])
  const [submitted, setSubmitted] = useState(false)
  const [overReceiptReason, setOverReceiptReason] = useState('')
  // Asked for only when the server says this collection takes a PO line past
  // its order and the GR carries no reason yet (ISS-0169).
  const needsOverReason = isOverReceiptError(grAction.error)

  useEffect(() => {
    if (gr?.line_items) {
      setLines(gr.line_items.map((l) => ({
        id: l.id,
        condition: l.condition,
        collectionNotes: l.discrepancy_notes ?? '',
      })))
    }
  }, [gr?.id]) // re-init when gr changes

  // ── Guard ──────────────────────────────────────────────────────────────────

  if (isLoading) return <div className="p-8 text-center text-fg-muted">Loading…</div>

  if (!gr) {
    return (
      <NotFoundState title="GR Not Found" message="The goods receipt you're looking for doesn't exist."
                     backTo="/gr" backLabel="Back to GR List" />
    )
  }

  if (gr.gr_type !== 'physical') {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-lg font-semibold text-fg-muted">Collection confirm is for physical GRs only</p>
        <BackLink to={`/gr/${gr.id}`} className="mt-4 text-sm text-primary-600 hover:underline">← Back to GR</BackLink>
      </div>
    )
  }

  if (gr.status !== 'collection_pending') {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-sm text-fg-muted mb-2">This GR is not pending collection (status: {gr.status})</p>
        <BackLink to={`/gr/${gr.id}`} className="text-sm text-primary-600 hover:underline">← Back to GR</BackLink>
      </div>
    )
  }

  // ── Derived state ──────────────────────────────────────────────────────────

  const updateLine = (idx: number, patch: Partial<CollectionLine>) => {
    setLines((prev) => {
      const next = [...prev]
      next[idx] = { ...next[idx], ...patch }
      return next
    })
  }

  const noteErrors = lines.map((l) =>
    submitted && l.condition !== 'good' && !l.collectionNotes.trim()
  )

  // ── Submit ─────────────────────────────────────────────────────────────────

  const handleSubmit = () => {
    setSubmitted(true)
    const hasErrors = lines.some((l) => l.condition !== 'good' && !l.collectionNotes.trim())
    if (!collectionDate || hasErrors) return
    if (needsOverReason && !overReceiptReason.trim()) return
    // GR-C-002: the line conditions are not stored separately — they go into
    // the collection notes with the General Notes (ISS-0185).
    const collectionNotes = collectionNotesWithLines(generalNotes, lines.map((l, i) => ({
      description: gr.line_items[i].description,
      condition: l.condition,
      notes: l.collectionNotes,
      recordedCondition: gr.line_items[i].condition,
    })))
    grAction.mutate(
      {
        action: 'collect', collected_by: user?.name, collection_notes: collectionNotes,
        over_receipt_reason: overReceiptReason.trim() || undefined,
      },
      { onSuccess: () => replaceTab(`/gr/${id}`) }
    )
  }

  const allGood = lines.every((l) => l.condition === 'good')
  // PA creation follows the 3-way match (GR-P-008); only with Collection
  // Confirmation Required on does it also wait for this collection (PA-013).
  // It is not unlocked by collecting (ISS-0191, ISS-0233).
  const paFollows = collectionGateOn
    ? "The Payment Application follows once the vendor's invoice is matched to this PO and its goods are collected."
    : "The Payment Application follows once the vendor's invoice is matched to this PO."
  const totalValue = gr.line_items.reduce((s, l) => s + Number(l.line_total), 0)

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
            <Truck className="h-5 w-5 text-warning-fg" />
            <h1 className="text-2xl font-semibold text-fg">Collect Goods</h1>
          </div>
          <p className="mt-1 text-sm text-fg-muted">
            {gr.number} · {gr.po_number} · {gr.vendor_name}
          </p>
        </div>
      </div>

      {/* Task banner */}
      <div className="rounded-xl border border-warning-line bg-warning-soft p-5">
        <div className="flex gap-3">
          <Package className="h-5 w-5 text-warning-fg flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-warning-fg">Collection Required</p>
            <p className="text-sm text-warning-fg mt-1">
              Please go to <strong>{gr.storage_location || 'Warehouse'}</strong> to collect your goods, then record the collection below.
            </p>
            <div className="mt-2 grid grid-cols-2 gap-x-8 gap-y-1 text-xs text-warning-fg">
              <span>Received by: <strong>{gr.received_by}</strong></span>
              <span>Received on: <strong>{formatDate(gr.received_at)}</strong></span>
              <span>Acknowledged by: <strong>{gr.acknowledged_by ?? '—'}</strong></span>
              <span>Total value: <strong>{formatAmount(totalValue, gr.currency)}</strong></span>
            </div>
          </div>
        </div>
      </div>

      {/* Collection Date */}
      <div className="rounded-xl border border-line bg-surface p-5">
        <h2 className="text-sm font-semibold text-fg mb-4">Collection Details</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-fg-secondary">
              Collection Date <span className="text-danger-fg">*</span>
            </label>
            <Input
              type="date"
              value={collectionDate}
              onChange={(e) => setCollectionDate(e.target.value)}
              className={cn(
                'h-10 px-3 rounded-lg border text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                submitted && !collectionDate ? 'border-danger-400' : 'border-line-strong'
              )}
            />
            {submitted && !collectionDate && (
              <p className="text-xs text-danger-fg">Required</p>
            )}
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-fg-secondary">General Notes</label>
            <Input
              type="text"
              placeholder="Any notes about the collection..."
              value={generalNotes}
              onChange={(e) => setGeneralNotes(e.target.value)}
             
            />
          </div>
        </div>
      </div>

      {/* Items to collect */}
      <div className="rounded-xl border border-line bg-surface overflow-hidden">
        <div className="px-5 py-4 border-b border-line bg-surface-subtle">
          <h2 className="text-sm font-semibold text-fg">Items to Collect</h2>
          <p className="text-xs text-fg-muted mt-0.5">
            Record the condition of each item as you collect it
          </p>
        </div>

        <div className="divide-y divide-line-subtle">
          {gr.line_items.map((item, idx) => {
            const line = lines[idx]
            if (!line) return null
            const hasIssue = line.condition !== 'good'
            const noteErr = noteErrors[idx]

            return (
              <div key={item.id} className={cn('px-5 py-4', idx % 2 === 1 && 'bg-surface-subtle/40')}>
                {/* Item header */}
                <div className="flex items-start justify-between gap-4">
                  <div className="flex gap-3">
                    <span className="flex-shrink-0 flex h-6 w-6 items-center justify-center rounded-full bg-surface-sunken text-xs font-medium text-fg-muted">
                      {idx + 1}
                    </span>
                    <div>
                      <p className="text-sm font-medium text-fg">{item.description}</p>
                      {item.material_id && (
                        <p className="text-xs text-fg-muted font-mono mt-0.5">{item.material_id}</p>
                      )}
                    </div>
                  </div>
                  <div className="text-right flex-shrink-0">
                    <p className="text-sm font-mono font-semibold text-fg">
                      {formatQty(item.qty_received)} {item.unit}
                    </p>
                    <p className="text-xs text-fg-muted mt-0.5">
                      {formatAmount(item.line_total, gr.currency)}
                    </p>
                  </div>
                </div>

                {/* Condition selector */}
                <div className="mt-3 flex gap-4">
                  {CONDITIONS.map((opt) => (
                    <label
                      key={opt.value}
                      className={cn(
                        'flex items-center gap-1.5 cursor-pointer rounded-lg border px-3 py-1.5 transition-colors',
                        line.condition === opt.value
                          ? opt.value === 'good'
                            ? 'border-success-400 bg-success-soft'
                            : opt.value === 'discrepancy'
                            ? 'border-warning-400 bg-warning-soft'
                            : 'border-danger-400 bg-danger-soft'
                          : 'border-line hover:border-line-strong'
                      )}
                    >
                      <Radio
                       
                        name={`cond-${item.id}`}
                        value={opt.value}
                        checked={line.condition === opt.value}
                        onChange={() => updateLine(idx, { condition: opt.value })}
                        className="h-3.5 w-3.5"
                      />
                      <span className={cn('text-xs font-medium', line.condition === opt.value ? opt.color : 'text-fg-muted')}>
                        {opt.label}
                      </span>
                    </label>
                  ))}
                </div>

                {/* Issue notes */}
                {hasIssue && (
                  <div className="mt-3">
                    <label className="text-xs font-medium text-fg-secondary">
                      {line.condition === 'damaged' ? 'Damage' : 'Discrepancy'} Details{' '}
                      <span className="text-danger-fg">*</span>
                    </label>
                    <Textarea
                      rows={2}
                      placeholder="Describe what you found when collecting..."
                      value={line.collectionNotes}
                      onChange={(e) => updateLine(idx, { collectionNotes: e.target.value })}
                      className={cn(
                        'mt-1 w-full px-3 py-2 rounded-lg border text-sm focus:outline-none focus:ring-1 focus:ring-warning-400 resize-none',
                        noteErr ? 'border-danger-400' : 'border-line-strong'
                      )}
                    />
                    {noteErr && <p className="text-xs text-danger-fg mt-0.5">Notes are required</p>}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Status summary */}
      {allGood ? (
        <div className="rounded-lg border border-success-line bg-success-soft p-4 flex gap-3">
          <CheckCircle2 className="h-5 w-5 text-success-fg flex-shrink-0" />
          <div>
            <p className="text-sm font-semibold text-success-fg">All items verified as Good condition</p>
            <p className="text-sm text-success-fg mt-0.5">
              Confirming collection will mark this GR as <strong>Collected</strong> and record the goods as
              received on the PO. {paFollows}
            </p>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-warning-line bg-warning-soft p-4 flex gap-3">
          <AlertTriangle className="h-5 w-5 text-warning-fg flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-warning-fg">
              {lines.filter((l) => l.condition !== 'good').length} item(s) with issues
            </p>
            <p className="text-sm text-warning-fg mt-0.5">
              The conditions and line notes marked here are added to the GR's Collection Notes. Nobody is
              notified and invoices for this PO are not blocked, so tell Procurement if they need to take it
              up with the vendor.
            </p>
          </div>
        </div>
      )}

      {grAction.isError && (
        <div className="rounded-lg border border-warning-line bg-warning-soft p-4 flex gap-3">
          <AlertTriangle className="h-5 w-5 text-warning-fg flex-shrink-0 mt-0.5" />
          <div className="flex-1 flex flex-col gap-2">
            <p className="text-sm text-warning-fg">
              {grAction.error instanceof Error ? grAction.error.message : 'The collection could not be saved'}
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
                  placeholder="Why more than the PO line has open is being received"
                  className={cn(
                    'w-full px-3 py-1.5 rounded border text-sm bg-surface focus:outline-none focus:ring-1 focus:ring-warning-400 resize-none',
                    submitted && !overReceiptReason.trim() ? 'border-danger-400' : 'border-warning-line'
                  )}
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
        <Button onClick={handleSubmit} className="gap-2">
          {allGood ? (
            <>
              <CheckCircle2 className="h-4 w-4" />
              Confirm Collection ✓
            </>
          ) : (
            <>
              <Truck className="h-4 w-4" />
              Submit Collection (with Issues)
            </>
          )}
        </Button>
      </div>
    </div>
  )
}
