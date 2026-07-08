/**
 * AttendeePicker — multi-select combobox for booking attendees.
 *
 * - 300ms debounced directory search
 * - Dropdown portaled to document.body (createPortal + fixed positioning)
 * - Outside-click closes (excludes both trigger and overlay refs)
 * - Selected users shown as removable chips
 * - Already-selected users excluded from results
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Search, X, Loader2 } from 'lucide-react'
import { directoryService } from '@/services/api'
import type { DirectoryUserOut } from '@/lib/types'
import { cn } from '@/lib/utils'

interface Props {
  value: DirectoryUserOut[]
  onChange: (users: DirectoryUserOut[]) => void
}

export function AttendeePicker({ value, onChange }: Props) {
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [results, setResults] = useState<DirectoryUserOut[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [dropPos, setDropPos] = useState<{ top: number; left: number; width: number } | null>(null)

  const triggerRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Debounce query by 300ms
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 300)
    return () => clearTimeout(t)
  }, [query])

  // Fetch when debounced query changes
  useEffect(() => {
    if (debouncedQuery.length < 2) {
      setResults([])
      return
    }
    let cancelled = false
    setLoading(true)
    directoryService.search(debouncedQuery).then((data) => {
      if (!cancelled) {
        // Exclude already-selected
        const selectedIds = new Set(value.map((u) => u.id))
        setResults(data.filter((u) => !selectedIds.has(u.id)))
        setLoading(false)
      }
    }).catch(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [debouncedQuery, value])

  // Compute and update dropdown position
  const updatePos = useCallback(() => {
    if (!triggerRef.current) return
    const rect = triggerRef.current.getBoundingClientRect()
    setDropPos({
      top: rect.bottom + window.scrollY + 4,
      left: rect.left + window.scrollX,
      width: rect.width,
    })
  }, [])

  // Open dropdown when query becomes active
  useEffect(() => {
    if (debouncedQuery.length >= 2) {
      updatePos()
      setOpen(true)
    } else {
      setOpen(false)
    }
  }, [debouncedQuery, updatePos])

  // Reposition on scroll / resize
  useEffect(() => {
    if (!open) return
    window.addEventListener('scroll', updatePos, true)
    window.addEventListener('resize', updatePos)
    return () => {
      window.removeEventListener('scroll', updatePos, true)
      window.removeEventListener('resize', updatePos)
    }
  }, [open, updatePos])

  // Outside-click closes, excluding trigger + overlay refs
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      const target = e.target as Node
      if (triggerRef.current?.contains(target)) return
      if (overlayRef.current?.contains(target)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  function select(user: DirectoryUserOut) {
    onChange([...value, user])
    setQuery('')
    setDebouncedQuery('')
    setResults([])
    setOpen(false)
    inputRef.current?.focus()
  }

  function remove(id: string) {
    onChange(value.filter((u) => u.id !== id))
  }

  const dropdown = open && dropPos ? createPortal(
    <div
      ref={overlayRef}
      className="z-[9999] overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-lg"
      style={{
        position: 'fixed',
        top:   dropPos.top - window.scrollY,
        left:  dropPos.left - window.scrollX,
        width: dropPos.width,
        maxHeight: 240,
        overflowY: 'auto',
      }}
    >
      {loading && (
        <div className="flex items-center gap-2 px-3 py-2 text-xs text-neutral-500">
          <Loader2 className="h-3 w-3 animate-spin" />
          Searching…
        </div>
      )}
      {!loading && results.length === 0 && debouncedQuery.length >= 2 && (
        <p className="px-3 py-2 text-xs text-neutral-500">No matches.</p>
      )}
      {results.map((u) => (
        <button
          key={u.id}
          type="button"
          onMouseDown={(e) => e.preventDefault()} // prevent blur before click
          onClick={() => select(u)}
          className="block w-full border-b border-neutral-100 px-3 py-2 text-left hover:bg-[#085E5E]/5 transition-colors last:border-0"
        >
          <p className="text-sm font-medium text-neutral-900">{u.full_name}</p>
          <p className="text-xs text-neutral-500">{u.email}</p>
        </button>
      ))}
    </div>,
    document.body,
  ) : null

  return (
    <div>
      {/* Selected chips */}
      {value.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {value.map((u) => (
            <span
              key={u.id}
              className="inline-flex items-center gap-1 rounded-full bg-[#085E5E]/10 px-2.5 py-0.5 text-xs font-medium text-[#085E5E]"
            >
              {u.full_name}
              <button
                type="button"
                onClick={() => remove(u.id)}
                className="ml-0.5 rounded-full text-[#085E5E]/60 hover:text-[#085E5E] transition-colors"
                aria-label={`Remove ${u.full_name}`}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Search input */}
      <div ref={triggerRef} className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-neutral-400 pointer-events-none" />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => { if (debouncedQuery.length >= 2) { updatePos(); setOpen(true) } }}
          placeholder={value.length > 0 ? 'Add more attendees…' : 'Search by name or email…'}
          className={cn(
            'w-full rounded-md border border-neutral-300 bg-white py-2 pl-8 pr-3 text-sm',
            'outline-none focus:border-[#085E5E]/60 focus:ring-1 focus:ring-[#085E5E]/30',
          )}
        />
      </div>

      {dropdown}
    </div>
  )
}
