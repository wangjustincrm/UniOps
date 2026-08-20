// Searchable product/material dropdown for the Consignment Stock entry form
// (design spec §6.6 page 4: "产品（带搜索的下拉）"). Data comes from
// mdm-api's materials master via lib/materials.ts — the same canonical
// `materials` table Task 10's forecast grid rows are keyed against
// (material_code), just queried live here per keystroke instead of
// pre-joined into a grid response, since there's no grid to join into on
// this page.
//
// Portaled to document.body with position computed from the trigger's rect
// — repo convention for any floating panel (feedback_uniops_overlay_dropdown_portal:
// a plain `absolute` dropdown gets clipped by an ancestor's overflow). Same
// shape as mrp's own VersionSwitcher.tsx and epms's PartsPicker
// (PrLineItems.tsx) — deliberately NOT epms's MaterialsPicker in that same
// file, which uses a non-portaled `absolute` panel and would reintroduce
// the clipping bug this page is required to avoid.
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Search, X as XIcon, Package } from 'lucide-react'
import { cn } from '@/lib/utils'
import { isFinishedGood, materialsApi, type MaterialOption, isPurchased } from '@/lib/materials'

// Render cap for the UNBOUNDED (component / all-materials) mode only: an
// empty/broad query there spans the whole ~2600-row master, too many <button>s
// to paint at once, so we show the first N and prompt to narrow. Finished-goods
// mode is a bounded ~130-row set and is rendered in full (scrollable) — the
// user expects to browse every product without typing.
const MAX_RENDERED = 200

export function MaterialPicker({
  value, onSelect, onClear, hasError, disabled, placeholder = 'Search products…',
  finishedGoodsOnly = true, purchasedOnly = false, panelZClass = 'z-[80]',
}: {
  /** Selected material code, or '' for none. */
  value: string
  onSelect: (material: MaterialOption) => void
  onClear: () => void
  hasError?: boolean
  disabled?: boolean
  /** Trigger button placeholder when nothing is selected — e.g. BOM
   *  Explorer's where-used mode reuses this picker for "component" rather
   *  than "product" ('Search components…'). */
  placeholder?: string
  /** Stacking order for the dropdown panel, which is portalled to
   *  `document.body` and therefore competes with whatever else is open.
   *  Defaults to the page-level `z-[80]`; a caller INSIDE an overlay must
   *  raise it above that overlay or the panel opens behind it and the picker
   *  looks like it simply does not respond to clicks — which is exactly what
   *  happened when the supply-parameters drawer (z-[90]) first used it. */
  panelZClass?: string
  /** Offer only what the plant BUYS (raw materials and packaging) — for
   *  screens about purchasing, where a semi-finished item or an air-sampling
   *  point is never the answer. Ignored when `finishedGoodsOnly` is set. */
  purchasedOnly?: boolean
  /** Restrict options to finished goods (see materials.ts isFinishedGood:
   *  CF and S-digit codes). Default true: this picker is product-oriented
   *  everywhere except BOM Explorer's where-used mode, which reverse-looks-up
   *  an arbitrary component (raw/semi/packaging) and passes false. */
  finishedGoodsOnly?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      const target = e.target as Node
      if (triggerRef.current?.contains(target)) return
      if (panelRef.current?.contains(target)) return
      setOpen(false)
      setQ('')
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // Load the WHOLE materials master once (paged to exhaustion by listAll,
  // shared react-query cache key so every picker instance reuses one fetch)
  // and filter client-side. The old approach queried the server per keystroke
  // capped at 30 rows with no paging, which (a) never surfaced a match past
  // the first 30 and (b) had no way to exclude non-products — the empty-query
  // state showed whatever sorted first (ADT*/ENV* sample points), not
  // finished goods. The finished-goods set is ~140 rows and the full master
  // ~2600, both cheap to filter in the browser (the Sales Forecast paste path
  // already loads the full master this way).
  const { data: allMaterials, isLoading } = useQuery({
    queryKey: ['mdm-materials-all'],
    queryFn: () => materialsApi.listAll(),
    enabled: open,
    staleTime: 5 * 60_000,
  })

  const results = useMemo(() => {
    if (!allMaterials) return [] as MaterialOption[]
    const pool = finishedGoodsOnly
      ? allMaterials.filter(isFinishedGood)
      : purchasedOnly
        ? allMaterials.filter(isPurchased)
        : allMaterials
    const term = q.trim().toLowerCase()
    if (!term) return pool
    return pool.filter(
      (m) => m.code.toLowerCase().includes(term) || (m.name?.toLowerCase().includes(term) ?? false),
    )
  }, [allMaterials, finishedGoodsOnly, purchasedOnly, q])

  // Finished-goods mode is bounded (~130) — show them all so the planner can
  // scroll the full product list without typing. Only the unbounded component
  // mode is capped.
  const shown = finishedGoodsOnly ? results : results.slice(0, MAX_RENDERED)
  const overflow = results.length - shown.length

  function openPicker() {
    if (disabled) return
    if (triggerRef.current) {
      const r = triggerRef.current.getBoundingClientRect()
      setRect({ top: r.bottom + 4, left: r.left, width: Math.max(r.width, 360) })
    }
    setOpen(true)
  }

  function handleSelect(m: MaterialOption) {
    onSelect(m)
    setOpen(false)
    setQ('')
  }

  return (
    <div className="relative w-full">
      <div className="flex items-center gap-1">
        <button
          ref={triggerRef}
          type="button"
          onClick={openPicker}
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
          // min-w-0 lets this flex child shrink below its content width so
          // the label's `truncate` actually clips — without it a long
          // "CODE — Long Product Name" spills past the field into the next
          // grid column (covers the Lot Number input).
          className={cn(
            'flex h-10 w-full min-w-0 flex-1 items-center gap-2 rounded-lg border bg-neutral-100 px-3 text-left text-sm transition-colors',
            'focus:outline-none focus:bg-white focus:border-primary-600',
            'disabled:cursor-not-allowed disabled:opacity-60',
            hasError ? 'border-danger-600 bg-danger-50' : 'border-neutral-200',
            open && 'border-primary-600 bg-white',
          )}
        >
          <Search className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
          <span className={cn('min-w-0 flex-1 truncate', value ? 'text-neutral-900' : 'text-neutral-400')}>
            {value || placeholder}
          </span>
        </button>
        {value && !disabled && (
          <button
            type="button"
            onClick={onClear}
            aria-label="Clear selected product"
            className="shrink-0 text-neutral-300 hover:text-danger-500"
          >
            <XIcon className="h-4 w-4" />
          </button>
        )}
      </div>

      {open && rect && createPortal(
        <div
          ref={panelRef}
          role="listbox"
          aria-label="Products"
          style={{ position: 'fixed', top: rect.top, left: rect.left, width: rect.width }}
          className={`${panelZClass} flex flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-lg`}
        >
          <div className="border-b border-neutral-100 p-2">
            <div className="flex items-center gap-2 rounded-md border border-neutral-200 bg-neutral-50 px-2">
              <Search className="h-3.5 w-3.5 text-neutral-400" />
              <input
                autoFocus
                type="text"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search by code or name…"
                className="h-8 flex-1 bg-transparent text-sm focus:outline-none"
              />
            </div>
          </div>
          <div className="max-h-72 overflow-y-auto">
            {isLoading ? (
              <div className="px-3 py-4 text-xs text-neutral-400">Loading…</div>
            ) : !shown.length ? (
              <div className="flex flex-col items-center gap-1 py-6 text-neutral-400">
                <Package className="h-5 w-5" />
                <span className="text-xs">No matching products</span>
              </div>
            ) : (
              shown.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  role="option"
                  aria-selected={m.code === value}
                  onClick={() => handleSelect(m)}
                  className={cn(
                    'block w-full border-b border-neutral-50 px-3 py-2 text-left transition-colors last:border-0 hover:bg-primary-50',
                    m.code === value && 'bg-primary-50/60',
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className="shrink-0 font-mono text-[10px] text-primary-600">{m.code}</span>
                    <span className="truncate text-xs font-medium text-neutral-900">{m.name ?? '—'}</span>
                  </div>
                  {(m.spec || m.base_uom) && (
                    <div className="mt-0.5 text-[10px] text-neutral-400">
                      {[m.spec, m.base_uom].filter(Boolean).join(' · ')}
                    </div>
                  )}
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
