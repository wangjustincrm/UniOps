import { X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { adminApi, type RefHit } from '@/services/adminApi'

import { Button, Input, MenuItem } from '@uniops/shell'
export interface RefListItem { id: string; label: string }

interface Props {
  system: string
  source: string
  value: RefListItem[]
  onChange: (next: RefListItem[]) => void
}

/**
 * Multi-select for a `reference_list` field — a JSONB array of ids with no FK
 * behind it (payment_applications.invoice_ids / gr_ids).
 *
 * Deliberately a list of removable chips rather than a checkbox list: the
 * candidate set is every invoice in the system, and what matters to the
 * operator is seeing exactly what is linked right now. An entry the backend
 * could not resolve is shown in place, not dropped — see _ref_list_labels.
 *
 * The popover is portalled to document.body so it escapes the edit dialog's
 * overflow-y-auto, which would otherwise clip it.
 */
export function ReferenceListPicker({ system, source, value, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<RefHit[]>([])
  const [rect, setRect] = useState<DOMRect | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const t = setTimeout(async () => {
      try { setHits(await adminApi.lookup(system, source, q)) } catch { setHits([]) }
    }, 200)
    return () => clearTimeout(t)
  }, [q, open, system, source])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (triggerRef.current?.contains(e.target as Node)) return
      if (popRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const add = (h: RefHit) => {
    if (value.some((v) => v.id === h.id)) return      // already linked — no-op
    onChange([...value, { id: h.id, label: h.label }])
  }
  const remove = (id: string) => onChange(value.filter((v) => v.id !== id))

  const toggle = () => {
    setRect(triggerRef.current?.getBoundingClientRect() ?? null)
    setOpen((o) => !o)
  }

  return (
    <div className="flex flex-col gap-1.5">
      {value.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line-strong px-3 py-2 text-xs text-fg-muted">
          Nothing linked
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {value.map((v) => (
            <li key={v.id}
              className="flex items-center gap-2 rounded-lg border border-line bg-surface-subtle px-2.5 py-1.5">
              <span className="min-w-0 flex-1 truncate text-xs" title={v.label}>{v.label}</span>
              <Button variant="danger-ghost" size="xs" type="button" onClick={() => remove(v.id)}
                className="shrink-0"
                aria-label={`Remove ${v.label}`}><X className="h-3 w-3" /></Button>
            </li>
          ))}
        </ul>
      )}

      <Button variant="secondary" size="xs" ref={triggerRef} type="button" onClick={toggle}
        className="self-start">
        + Link {source === 'invoices' ? 'invoice' : source === 'grs' ? 'goods receipt' : source}
      </Button>

      {open && rect && createPortal(
        <div ref={popRef}
          style={{ position: 'fixed', top: rect.bottom + 4, left: rect.left, width: Math.max(rect.width, 420) }}
          className="z-[60] rounded-lg border border-line bg-surface shadow-lg">
          <Input inputSize="sm" autoFocus value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Search by number, vendor, PO…"
            className="m-2 w-[calc(100%-1rem)]" />
          <ul className="max-h-64 overflow-y-auto pb-1">
            {hits.map((h) => {
              const already = value.some((v) => v.id === h.id)
              return (
                <li key={h.id}>
                  <MenuItem disabled={already} onClick={() => add(h)}
                    className="block truncate py-1.5 text-xs disabled:cursor-default disabled:bg-surface-subtle disabled:text-fg-muted disabled:opacity-100">
                    {h.label}{already && ' · already linked'}
                  </MenuItem>
                </li>
              )
            })}
            {hits.length === 0 && <li className="px-3 py-2 text-xs text-fg-muted">No matches</li>}
          </ul>
        </div>, document.body)}
    </div>
  )
}
