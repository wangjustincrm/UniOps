import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { adminApi, type RefHit } from '@/services/adminApi'

import { Input, Button } from '@uniops/shell'
interface Props {
  system: string
  source: string
  valueLabel: string           // current display (e.g. existing vendor_name / requester)
  onPick: (hit: RefHit) => void
}

export function ReferencePicker({ system, source, valueLabel, onPick }: Props) {
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

  const toggle = () => {
    setRect(triggerRef.current?.getBoundingClientRect() ?? null)
    setOpen((o) => !o)
  }

  return (
    <>
      <Button variant="secondary" size="md" ref={triggerRef} type="button" onClick={toggle}
        className="truncate">
        {valueLabel || <span className="text-fg-muted">Select…</span>}
      </Button>
      {open && rect && createPortal(
        <div ref={popRef} style={{ position: 'fixed', top: rect.bottom + 4, left: rect.left, width: Math.max(rect.width, 260) }}
          className="z-[60] rounded-lg border border-line bg-surface shadow-lg">
          <Input inputSize="sm" autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…"
            className="m-2 w-[calc(100%-1rem)]" />
          <ul className="max-h-64 overflow-y-auto pb-1">
            {hits.map((h) => (
              <li key={h.id}>
                <button type="button" onClick={() => { onPick(h); setOpen(false) }}
                  className="block w-full truncate px-3 py-1.5 text-left text-sm hover:bg-primary-50">{h.label}</button>
              </li>
            ))}
            {hits.length === 0 && <li className="px-3 py-2 text-xs text-fg-muted">No matches</li>}
          </ul>
        </div>, document.body)}
    </>
  )
}
