import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { ArrowLeft, Truck, CheckCircle2, AlertTriangle, Package } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { useGr, useGrAction } from '@/hooks/useGrs'
import { useAuthStore } from '@/stores/auth.store'
import { type GrLineCondition } from '@/services/gr'

// ─── Condition options ────────────────────────────────────────────────────────

const CONDITIONS: { value: GrLineCondition; label: string; color: string }[] = [
  { value: 'good',        label: 'Good',        color: 'text-success-600' },
  { value: 'discrepancy', label: 'Discrepancy', color: 'text-warning-600' },
  { value: 'damaged',     label: 'Damaged',     color: 'text-danger-600'  },
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
  const grAction = useGrAction(id ?? '')
  const { user } = useAuthStore()

  const [collectionDate, setCollectionDate] = useState(
    new Date().toISOString().slice(0, 10)
  )
  const [generalNotes, setGeneralNotes] = useState('')
  const [lines, setLines] = useState<CollectionLine[]>([])
  const [submitted, setSubmitted] = useState(false)

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

  if (isLoading) return <div className="p-8 text-center text-neutral-400">Loading…</div>

  if (!gr) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-lg font-semibold text-neutral-500">GR not found</p>
        <Link to="/gr" className="mt-4 text-sm text-primary-600 hover:underline">← Back to GR List</Link>
      </div>
    )
  }

  if (gr.gr_type !== 'physical') {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-lg font-semibold text-neutral-500">Collection confirm is for physical GRs only</p>
        <Link to={`/gr/${gr.id}`} className="mt-4 text-sm text-primary-600 hover:underline">← Back to GR</Link>
      </div>
    )
  }

  if (gr.status !== 'collection_pending') {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-sm text-neutral-500 mb-2">This GR is not pending collection (status: {gr.status})</p>
        <Link to={`/gr/${gr.id}`} className="text-sm text-primary-600 hover:underline">← Back to GR</Link>
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
    grAction.mutate(
      { action: 'collect', collected_by: user?.name, collection_notes: generalNotes.trim() || undefined },
      { onSuccess: () => replaceTab(`/gr/${id}`) }
    )
  }

  const allGood = lines.every((l) => l.condition === 'good')
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
            <Truck className="h-5 w-5 text-warning-500" />
            <h1 className="text-2xl font-bold text-neutral-900">Collect Goods</h1>
          </div>
          <p className="mt-1 text-sm text-neutral-500">
            {gr.number} · {gr.po_number} · {gr.vendor_name}
          </p>
        </div>
      </div>

      {/* Task banner */}
      <div className="rounded-xl border border-warning-200 bg-warning-50 p-5">
        <div className="flex gap-3">
          <Package className="h-5 w-5 text-warning-500 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-warning-700">Collection Required</p>
            <p className="text-sm text-warning-600 mt-1">
              Please go to <strong>{gr.storage_location}</strong> to collect your goods, then record the collection below.
            </p>
            <div className="mt-2 grid grid-cols-2 gap-x-8 gap-y-1 text-xs text-warning-600">
              <span>Received by: <strong>{gr.received_by}</strong></span>
              <span>Received on: <strong>{formatDate(gr.received_at)}</strong></span>
              <span>Acknowledged by: <strong>{gr.acknowledged_by ?? '—'}</strong></span>
              <span>Total value: <strong>{formatAmount(totalValue, gr.currency)}</strong></span>
            </div>
          </div>
        </div>
      </div>

      {/* Collection Date */}
      <div className="rounded-xl border border-neutral-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-neutral-900 mb-4">Collection Details</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-neutral-700">
              Collection Date <span className="text-danger-600">*</span>
            </label>
            <input
              type="date"
              value={collectionDate}
              onChange={(e) => setCollectionDate(e.target.value)}
              className={cn(
                'h-10 px-3 rounded-lg border text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                submitted && !collectionDate ? 'border-danger-400' : 'border-neutral-300'
              )}
            />
            {submitted && !collectionDate && (
              <p className="text-xs text-danger-600">Required</p>
            )}
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-neutral-700">General Notes</label>
            <input
              type="text"
              placeholder="Any notes about the collection..."
              value={generalNotes}
              onChange={(e) => setGeneralNotes(e.target.value)}
              className="h-10 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
            />
          </div>
        </div>
      </div>

      {/* Items to collect */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <div className="px-5 py-4 border-b border-neutral-200 bg-neutral-50">
          <h2 className="text-sm font-semibold text-neutral-900">Items to Collect</h2>
          <p className="text-xs text-neutral-400 mt-0.5">
            Record the condition of each item as you collect it
          </p>
        </div>

        <div className="divide-y divide-neutral-100">
          {gr.line_items.map((item, idx) => {
            const line = lines[idx]
            if (!line) return null
            const hasIssue = line.condition !== 'good'
            const noteErr = noteErrors[idx]

            return (
              <div key={item.id} className={cn('px-5 py-4', idx % 2 === 1 && 'bg-neutral-50/40')}>
                {/* Item header */}
                <div className="flex items-start justify-between gap-4">
                  <div className="flex gap-3">
                    <span className="flex-shrink-0 flex h-6 w-6 items-center justify-center rounded-full bg-neutral-100 text-xs font-medium text-neutral-500">
                      {idx + 1}
                    </span>
                    <div>
                      <p className="text-sm font-medium text-neutral-900">{item.description}</p>
                      {item.material_id && (
                        <p className="text-xs text-neutral-400 font-mono mt-0.5">{item.material_id}</p>
                      )}
                    </div>
                  </div>
                  <div className="text-right flex-shrink-0">
                    <p className="text-sm font-mono font-semibold text-neutral-900">
                      {item.qty_received} {item.unit}
                    </p>
                    <p className="text-xs text-neutral-400 mt-0.5">
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
                            ? 'border-success-400 bg-success-50'
                            : opt.value === 'discrepancy'
                            ? 'border-warning-400 bg-warning-50'
                            : 'border-danger-400 bg-danger-50'
                          : 'border-neutral-200 hover:border-neutral-300'
                      )}
                    >
                      <input
                        type="radio"
                        name={`cond-${item.id}`}
                        value={opt.value}
                        checked={line.condition === opt.value}
                        onChange={() => updateLine(idx, { condition: opt.value })}
                        className="h-3.5 w-3.5 accent-current"
                      />
                      <span className={cn('text-xs font-medium', line.condition === opt.value ? opt.color : 'text-neutral-500')}>
                        {opt.label}
                      </span>
                    </label>
                  ))}
                </div>

                {/* Issue notes */}
                {hasIssue && (
                  <div className="mt-3">
                    <label className="text-xs font-medium text-neutral-700">
                      {line.condition === 'damaged' ? 'Damage' : 'Discrepancy'} Details{' '}
                      <span className="text-danger-600">*</span>
                    </label>
                    <textarea
                      rows={2}
                      placeholder="Describe what you found when collecting..."
                      value={line.collectionNotes}
                      onChange={(e) => updateLine(idx, { collectionNotes: e.target.value })}
                      className={cn(
                        'mt-1 w-full px-3 py-2 rounded-lg border text-sm focus:outline-none focus:ring-1 focus:ring-warning-400 resize-none',
                        noteErr ? 'border-danger-400' : 'border-neutral-300'
                      )}
                    />
                    {noteErr && <p className="text-xs text-danger-600 mt-0.5">Notes are required</p>}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Status summary */}
      {allGood ? (
        <div className="rounded-lg border border-success-200 bg-success-50 p-4 flex gap-3">
          <CheckCircle2 className="h-5 w-5 text-success-600 flex-shrink-0" />
          <div>
            <p className="text-sm font-semibold text-success-700">All items verified as Good condition</p>
            <p className="text-sm text-success-600 mt-0.5">
              Confirming collection will mark this GR as Collected and unlock Payment Application creation.
            </p>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-warning-200 bg-warning-50 p-4 flex gap-3">
          <AlertTriangle className="h-5 w-5 text-warning-500 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-semibold text-warning-700">
              {lines.filter((l) => l.condition !== 'good').length} item(s) with issues
            </p>
            <p className="text-sm text-warning-600 mt-0.5">
              Submitting will notify the Procurement Officer. Invoice creation will be blocked until issues are resolved.
            </p>
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
