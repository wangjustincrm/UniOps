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
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Search, X as XIcon, Package } from 'lucide-react'
import { cn } from '@/lib/utils'
import { materialsApi, type MaterialOption } from '@/lib/materials'

export function MaterialPicker({
  value, onSelect, onClear, hasError, disabled,
}: {
  /** Selected material code, or '' for none. */
  value: string
  onSelect: (material: MaterialOption) => void
  onClear: () => void
  hasError?: boolean
  disabled?: boolean
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

  // No debounce — matches the one existing precedent for this exact
  // pattern (epms PrLineItems.tsx MaterialsPicker: a useQuery keyed on the
  // raw search string, refetching per keystroke). Page sizes are capped at
  // 30 results server-side, so the worst case is a small, cheap query.
  const { data, isLoading } = useQuery({
    queryKey: ['mdm-materials-picker', q],
    queryFn: () => materialsApi.search(q, 30),
    enabled: open,
  })

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
          className={cn(
            'flex h-10 flex-1 items-center gap-2 rounded-lg border bg-neutral-100 px-3 text-left text-sm transition-colors',
            'focus:outline-none focus:bg-white focus:border-primary-600',
            'disabled:cursor-not-allowed disabled:opacity-60',
            hasError ? 'border-danger-600 bg-danger-50' : 'border-neutral-200',
            open && 'border-primary-600 bg-white',
          )}
        >
          <Search className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
          <span className={cn('flex-1 truncate', value ? 'text-neutral-900' : 'text-neutral-400')}>
            {value || 'Search products…'}
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
          className="z-[80] flex flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-lg"
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
            ) : !data?.items.length ? (
              <div className="flex flex-col items-center gap-1 py-6 text-neutral-400">
                <Package className="h-5 w-5" />
                <span className="text-xs">No matching products</span>
              </div>
            ) : (
              data.items.map((m) => (
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
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
