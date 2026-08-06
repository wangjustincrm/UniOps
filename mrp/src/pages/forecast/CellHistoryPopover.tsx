// Per-cell change history — a lightweight createPortal popover triggered by
// MatrixGrid's onCellHistoryClick affordance (see components/MatrixGrid.tsx,
// the tiny History icon in each cell's corner). Shows
// seriesApi.getChangeLog(material, month) newest-first, e.g.
// "2026-07-14: 50 -> 60". Portaled + position:fixed per repo convention
// (feedback_uniops_overlay_dropdown_portal) — a plain `absolute` panel would
// get clipped by MatrixGrid's own overflow-x-auto/overflow-y-auto scroll
// wrappers.
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Loader2, X as XIcon } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { seriesApi } from './seriesApi'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

/** old_qty/new_qty are Decimal-as-string, possibly null (e.g. a brand new cell's "old" value). */
function formatQty(raw: string | null): string {
  if (raw === null) return '—'
  const n = Number(raw)
  return Number.isFinite(n) ? new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(n) : raw
}

/** changed_at is a full timestamp; the brief's example format is just the
 *  date ("2026-07-14: 50 -> 60") — trim rather than pulling in a date lib. */
function formatDate(iso: string): string {
  return iso.slice(0, 10)
}

export function CellHistoryPopover({
  materialCode, materialLabel, month, anchorRect, onClose,
}: {
  materialCode: string
  materialLabel: string
  month: string
  /** The clicked cell's bounding rect (from anchorEl.getBoundingClientRect()) — plain fields, not a live DOMRect, so the caller can pass a snapshot. */
  anchorRect: { top: number; left: number; bottom: number; right: number }
  onClose: () => void
}) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current?.contains(e.target as Node)) return
      onClose()
    }
    const escHandler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', handler)
    document.addEventListener('keydown', escHandler)
    return () => {
      document.removeEventListener('mousedown', handler)
      document.removeEventListener('keydown', escHandler)
    }
  }, [onClose])

  const query = useQuery({
    queryKey: ['series-change-log', materialCode, month],
    queryFn: () => seriesApi.getChangeLog(materialCode, month),
  })

  // Sort defensively (newest-first) rather than trusting the endpoint's
  // ordering — cheap, and this is the one place staleness would be visible.
  const items = [...(query.data?.items ?? [])].sort((a, b) => b.changed_at.localeCompare(a.changed_at))

  // Below-right of the clicked cell, clamped so it doesn't run off the
  // viewport's right edge — cells near the last of ~27 month columns are
  // common on this wide grid.
  const width = 288
  const left = Math.min(anchorRect.left, Math.max(8, window.innerWidth - width - 8))
  const top = anchorRect.bottom + 4

  return createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-label={`Change history — ${materialLabel}, ${month}`}
      style={{ position: 'fixed', top, left, width }}
      className="z-[80] flex max-h-72 flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-lg"
    >
      <div className="flex items-center justify-between gap-2 border-b border-neutral-100 px-3 py-2">
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold text-neutral-800">{materialLabel}</p>
          <p className="text-[11px] text-neutral-400">{month}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close history"
          className="-m-2.5 flex min-h-[44px] min-w-[44px] shrink-0 items-center justify-center text-neutral-400 hover:text-neutral-600"
        >
          <XIcon className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="overflow-y-auto px-3 py-2">
        {query.isLoading ? (
          <div className="flex items-center gap-1.5 py-3 text-xs text-neutral-400">
            <Loader2 className="h-3 w-3 animate-spin" /> Loading…
          </div>
        ) : query.isError ? (
          <p role="alert" className="py-2 text-xs text-danger-600">{errMsg(query.error, 'Could not load history.')}</p>
        ) : items.length === 0 ? (
          <p className="py-2 text-xs text-neutral-400">No edits recorded for this cell yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {items.map((it, i) => (
              <li key={`${it.changed_at}-${i}`} className="text-xs text-neutral-700">
                <span className="text-neutral-400">{formatDate(it.changed_at)}:</span>{' '}
                {formatQty(it.old_qty)} → {formatQty(it.new_qty)}
                {it.changed_by && <span className="text-neutral-400"> · {it.changed_by}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>,
    document.body,
  )
}
