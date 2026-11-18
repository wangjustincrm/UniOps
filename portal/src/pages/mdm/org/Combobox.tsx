import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown } from 'lucide-react'

import { Button, Input, MenuItem } from '@uniops/shell'
export interface ComboOption { value: string; label: string; hint?: string | null; search?: string }

/** Type-to-search picker over a list the page already holds. Portalled so the panel's edge does not clip it. */
export function Combobox({ options, value, onChange, disabled, label, placeholder = 'Search…' }: {
  options: ComboOption[]; value: string; onChange: (value: string) => void
  disabled?: boolean; label: string; placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [rect, setRect] = useState<DOMRect | null>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const pop = useRef<HTMLDivElement>(null)
  const selected = options.find((o) => o.value === value)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (trigger.current?.contains(e.target as Node) || pop.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return options
    return options.filter((o) => `${o.label} ${o.hint ?? ''} ${o.search ?? ''}`.toLowerCase().includes(needle))
  }, [options, q])

  const pick = (next: string) => {
    setOpen(false); setQ('')
    if (next !== value) onChange(next)
  }

  return (
    <>
      <Button variant="secondary" size="md" ref={trigger} type="button" aria-label={label} disabled={disabled}
        onClick={() => { setRect(trigger.current?.getBoundingClientRect() ?? null); setOpen((o) => !o) }}
        className="mt-1 w-full justify-between">
        <span className="truncate">{selected?.label ?? ''}</span>
        <ChevronDown className="h-4 w-4 shrink-0 text-fg-muted" />
      </Button>
      {open && rect && createPortal(
        <div ref={pop} style={{ position: 'fixed', top: rect.bottom + 4, right: window.innerWidth - rect.right, width: Math.max(rect.width, 300) }}
          className="z-[60] rounded-lg border border-line bg-surface shadow-lg">
          <Input inputSize="sm" autoFocus value={q} placeholder={placeholder} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setOpen(false)
              if (e.key === 'Enter' && filtered[0]) pick(filtered[0].value)
            }}
            className="m-2 w-[calc(100%-1rem)]" />
          <ul className="max-h-72 overflow-y-auto pb-1">
            {filtered.map((o) => (
              <li key={o.value}>
                <MenuItem active={o.value === value} onClick={() => pick(o.value)} className="block py-1.5">
                  <span className="block truncate text-fg">{o.label}</span>
                  {o.hint && <span className="block truncate text-xs text-fg-muted">{o.hint}</span>}
                </MenuItem>
              </li>
            ))}
            {filtered.length === 0 && <li className="px-3 py-2 text-sm text-fg-muted">No match</li>}
          </ul>
        </div>,
        document.body,
      )}
    </>
  )
}
