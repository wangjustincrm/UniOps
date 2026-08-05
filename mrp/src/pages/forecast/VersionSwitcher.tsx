// Version switcher — top-right of ForecastPage. Trigger button shows the
// selected version + status badge; the list itself renders in a portal
// (fixed-positioned against the trigger's rect) so it isn't clipped by the
// grid's `overflow-x-auto` wrapper — repo convention, see
// feedback_uniops_overlay_dropdown_portal.
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, Plus, Check } from 'lucide-react'
import { cn } from '@/lib/utils'
import { StatusBadge } from '@/components/StatusBadge'
import type { ForecastVersion } from './forecastApi'

export function VersionSwitcher({
  versions, selectedId, onSelect, onCreateNew,
}: {
  versions: ForecastVersion[]
  selectedId: string | null
  onSelect: (id: string) => void
  onCreateNew: () => void
}) {
  const [open, setOpen] = useState(false)
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const selected = versions.find((v) => v.id === selectedId) ?? null

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      const target = e.target as Node
      if (triggerRef.current?.contains(target)) return
      if (panelRef.current?.contains(target)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  function toggleOpen() {
    if (!open && triggerRef.current) {
      const r = triggerRef.current.getBoundingClientRect()
      setRect({ top: r.bottom + 4, left: Math.max(8, r.right - 320), width: 320 })
    }
    setOpen((v) => !v)
  }

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={toggleOpen}
        className="flex min-h-[44px] items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm hover:border-neutral-300"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {selected ? (
          <>
            <span className="font-medium text-neutral-800">{selected.version_no}</span>
            <StatusBadge status={selected.status} />
          </>
        ) : (
          <span className="text-neutral-500">No version selected</span>
        )}
        <ChevronDown className={cn('h-3.5 w-3.5 text-neutral-400 transition-transform', open && 'rotate-180')} />
      </button>

      {open && rect && createPortal(
        <div
          ref={panelRef}
          role="listbox"
          aria-label="Forecast versions"
          style={{ position: 'fixed', top: rect.top, left: rect.left, width: rect.width }}
          className="z-[80] max-h-80 overflow-y-auto rounded-lg border border-neutral-200 bg-white py-1 shadow-lg"
        >
          {versions.length === 0 && (
            <p className="px-3 py-3 text-sm text-neutral-400">No forecast versions yet.</p>
          )}
          {versions.map((v) => (
            <button
              key={v.id}
              type="button"
              role="option"
              aria-selected={v.id === selectedId}
              onClick={() => { onSelect(v.id); setOpen(false) }}
              className={cn(
                'flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-neutral-50',
                v.id === selectedId && 'bg-primary-50/60',
              )}
            >
              <span className="flex min-w-0 items-center gap-2">
                {v.id === selectedId
                  ? <Check className="h-3.5 w-3.5 shrink-0 text-primary-600" />
                  : <span className="h-3.5 w-3.5 shrink-0" />}
                <span className="min-w-0">
                  <span className="block truncate font-medium text-neutral-800">{v.version_no}</span>
                  <span className="block text-xs text-neutral-500">
                    {v.horizon_start_month} · {v.horizon_months}mo{v.note ? ` · ${v.note}` : ''}
                  </span>
                </span>
              </span>
              <StatusBadge status={v.status} />
            </button>
          ))}
          <div className="mt-1 border-t border-neutral-100 pt-1">
            <button
              type="button"
              onClick={() => { setOpen(false); onCreateNew() }}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium text-primary-600 hover:bg-primary-50"
            >
              <Plus className="h-4 w-4" />
              New Version…
            </button>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
