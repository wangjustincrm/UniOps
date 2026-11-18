/**
 * TravelerPicker — multi-select combobox for travel application travelers.
 *
 * Adapted from booking/src/components/AttendeePicker.tsx:
 * - 300ms debounced directory search (q >= 2 chars)
 * - Dropdown portaled to document.body (createPortal + fixed positioning)
 * - Outside-click closes (excludes both trigger and overlay refs)
 * - Selected users shown as removable chips
 * - Already-selected users excluded from results
 */
import { useState, useRef, useEffect, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { X, Search } from 'lucide-react'
import { api } from '@/lib/api'

import { Input, Button, MenuItem } from '@uniops/shell'
export interface Traveler { user_id: string; user_name: string }
interface DirectoryUser { id: string; full_name: string; email?: string }

export function TravelerPicker({ value, onChange }: {
  value: Traveler[]; onChange: (t: Traveler[]) => void
}) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  // Raw directory results for the current query — NOT filtered against `value` here.
  // Filtering happens client-side in `results` below so that chip add/remove doesn't
  // trigger a redundant network request.
  const [rawResults, setRawResults] = useState<DirectoryUser[]>([])
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null)
  const triggerRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)

  // debounced directory search (q >= 2 chars). Depends on `q` only — adding/removing
  // a chip must not re-fetch, only re-filter (see `results` below).
  useEffect(() => {
    if (q.trim().length < 2) { setRawResults([]); return }
    let cancelled = false
    const h = setTimeout(async () => {
      const rows = await api.get<DirectoryUser[]>(`/api/v1/users/directory?q=${encodeURIComponent(q.trim())}`)
      if (!cancelled) setRawResults(rows)
    }, 300)
    return () => { clearTimeout(h); cancelled = true }
  }, [q])

  // Exclude already-selected travelers from the raw results, client-side, so
  // selecting/removing a chip re-filters without re-fetching.
  const results = useMemo(() => {
    const chosen = new Set(value.map(v => v.user_id))
    return rawResults.filter(r => !chosen.has(r.id))
  }, [rawResults, value])

  const reposition = () => {
    const r = triggerRef.current?.getBoundingClientRect()
    if (r) setPos({ top: r.bottom + 4, left: r.left, width: r.width })
  }
  useEffect(() => {
    if (!open) return
    reposition()
    const onScroll = () => reposition(); const onResize = () => reposition()
    window.addEventListener('scroll', onScroll, true); window.addEventListener('resize', onResize)
    const onDoc = (e: MouseEvent) => {
      if (triggerRef.current?.contains(e.target as Node)) return
      if (overlayRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => {
      window.removeEventListener('scroll', onScroll, true); window.removeEventListener('resize', onResize)
      document.removeEventListener('mousedown', onDoc)
    }
  }, [open])

  const add = (u: DirectoryUser) => {
    onChange([...value, { user_id: u.id, user_name: u.full_name }]); setQ(''); setRawResults([]); setOpen(false)
  }
  const remove = (id: string) => onChange(value.filter(v => v.user_id !== id))

  const dropdown = open && pos && results.length > 0 ? createPortal(
    <div ref={overlayRef} className="z-[9999] rounded-lg border border-line bg-surface shadow-lg"
      style={{ position: 'fixed', top: pos.top, left: pos.left, width: pos.width, maxHeight: 240, overflowY: 'auto' }}>
      {results.map(u => (
        <MenuItem type="button" key={u.id} onMouseDown={e => e.preventDefault()} onClick={() => add(u)}
         >
          <span className="font-medium text-fg">{u.full_name}</span>
          {u.email && <span className="text-xs text-fg-muted">{u.email}</span>}
        </MenuItem>
      ))}
    </div>, document.body) : null

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {value.map(t => (
          <span key={t.user_id} className="inline-flex items-center gap-1 rounded-full bg-primary-100 px-2.5 py-1 text-xs font-medium text-primary-700">
            {t.user_name}
            <Button variant="ghost" size="icon-xs" type="button" onClick={() => remove(t.user_id)} aria-label={`Remove ${t.user_name}`}>
              <X className="h-3 w-3" />
            </Button>
          </span>
        ))}
      </div>
      <div ref={triggerRef} className="relative">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-fg-muted" />
        <Input value={q} onFocus={() => setOpen(true)} onChange={e => { setQ(e.target.value); setOpen(true) }}
          placeholder="Search employees by name or email…"
          className="w-full pl-9 pr-3" />
      </div>
      {dropdown}
    </div>
  )
}
