// Editable lot-number combo box for the Consignment Stock entry form
// (design spec §6.6 page 4 — the batch number is no longer typed blind).
// When a product is selected its historical batch numbers are loaded live
// from WMS (GET /consignment/lot-history -> INV_LOT_ATT) and offered as a
// searchable, scrollable suggestion list showing each lot's expiry. Picking
// one fills the field AND hands its expiry to the parent (no extra lookup);
// the input stays fully editable so a batch not in WMS can still be typed by
// hand — the parent's onBlur lot-lookup then resolves (or blanks) its expiry,
// exactly as before. Empty suggestions (WMS unreachable / SKU has no lots)
// degrade to a plain editable input — hand entry must never be blocked
// (design doc 6.3).
//
// Portaled to document.body with a fixed position from the input's rect —
// repo convention for floating panels (feedback_uniops_overlay_dropdown_portal),
// same shape as MaterialPicker.tsx in this folder.
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, Loader2, PackageSearch } from 'lucide-react'
import { cn, formatDate } from '@/lib/utils'
import { consignmentApi, type LotHistoryItem } from './consignmentApi'

const MAX_RENDERED = 200

export function LotCombo({
  materialCode, value, onChange, onPick, onBlur, hasError, disabled,
}: {
  /** Selected product code — suggestions are loaded for this SKU; empty
   *  disables the field (nothing to suggest a lot against). */
  materialCode: string
  /** Current lot-number text. */
  value: string
  /** Free-typed edit — parent owns the value and runs its own onBlur lookup. */
  onChange: (lotNo: string) => void
  /** A suggestion was chosen: its expiry is already known, so the parent can
   *  show it without a separate WMS round trip. */
  onPick: (item: LotHistoryItem) => void
  /** Fired when focus leaves the field (parent triggers its lot-lookup). */
  onBlur: () => void
  hasError?: boolean
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['consignment-lot-history', materialCode],
    queryFn: () => consignmentApi.lotHistory(materialCode),
    enabled: open && !!materialCode,
    staleTime: 5 * 60_000,
  })

  const results = useMemo(() => {
    const items = data?.items ?? []
    const term = value.trim().toLowerCase()
    if (!term) return items
    return items.filter((i) => i.lot_no.toLowerCase().includes(term))
  }, [data, value])
  const shown = results.slice(0, MAX_RENDERED)
  const overflow = results.length - shown.length

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      const t = e.target as Node
      if (inputRef.current?.contains(t)) return
      if (panelRef.current?.contains(t)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  function positionPanel() {
    if (inputRef.current) {
      const r = inputRef.current.getBoundingClientRect()
      setRect({ top: r.bottom + 4, left: r.left, width: Math.max(r.width, 260) })
    }
  }

  function openPanel() {
    if (disabled || !materialCode) return
    positionPanel()
    setOpen(true)
  }

  function handlePick(item: LotHistoryItem) {
    onPick(item)
    setOpen(false)
  }

  return (
    <div className="relative w-full">
      <div className="relative flex items-center">
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => { onChange(e.target.value); if (!open) openPanel() }}
          onFocus={openPanel}
          onBlur={onBlur}
          disabled={disabled}
          placeholder={materialCode ? 'Select or type a lot number…' : 'Select a product first'}
          aria-invalid={hasError || undefined}
          className={cn(
            'h-10 w-full min-w-0 rounded-lg border bg-white px-3 pr-9 text-sm transition-colors',
            'focus:outline-none focus:border-primary-600',
            'disabled:cursor-not-allowed disabled:bg-neutral-100 disabled:opacity-60',
            hasError ? 'border-danger-600 bg-danger-50' : 'border-neutral-200',
          )}
        />
        <button
          type="button"
          tabIndex={-1}
          onClick={() => (open ? setOpen(false) : openPanel())}
          disabled={disabled || !materialCode}
          aria-label="Show lot suggestions"
          className="absolute right-2 text-neutral-300 hover:text-neutral-500 disabled:opacity-40"
        >
          <ChevronDown className={cn('h-4 w-4 transition-transform', open && 'rotate-180')} />
        </button>
      </div>

      {open && rect && createPortal(
        <div
          ref={panelRef}
          role="listbox"
          aria-label="Lot numbers in WMS"
          style={{ position: 'fixed', top: rect.top, left: rect.left, width: rect.width }}
          className="z-[80] flex max-h-72 flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-lg"
        >
          <div className="overflow-y-auto">
            {isLoading ? (
              <div className="flex items-center gap-1.5 px-3 py-4 text-xs text-neutral-400">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading batches from WMS…
              </div>
            ) : !shown.length ? (
              <div className="flex flex-col items-center gap-1 py-6 text-neutral-400">
                <PackageSearch className="h-5 w-5" />
                <span className="text-xs">
                  {data?.items.length ? 'No matching lot' : 'No WMS batches for this product — type one'}
                </span>
              </div>
            ) : (
              shown.map((item) => (
                <button
                  key={item.lot_no}
                  type="button"
                  role="option"
                  aria-selected={item.lot_no === value}
                  // onMouseDown (not onClick) so the pick registers before the
                  // input's onBlur fires and would otherwise run a redundant
                  // lot-lookup / close the panel first.
                  onMouseDown={(e) => { e.preventDefault(); handlePick(item) }}
                  className={cn(
                    'flex w-full items-center justify-between gap-3 border-b border-neutral-50 px-3 py-2 text-left transition-colors last:border-0 hover:bg-primary-50',
                    item.lot_no === value && 'bg-primary-50/60',
                  )}
                >
                  <span className="truncate font-mono text-xs text-neutral-800">{item.lot_no}</span>
                  <span className="shrink-0 text-[10px] text-neutral-400">
                    {item.expiry_date ? `exp ${formatDate(item.expiry_date)}` : 'no expiry'}
                  </span>
                </button>
              ))
            )}
            {overflow > 0 && (
              <div className="border-t border-neutral-100 px-3 py-2 text-center text-[10px] text-neutral-400">
                +{overflow} more — keep typing to narrow
              </div>
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
