/**
 * Choosing a person.
 *
 * Two things have to be possible at once, because the paper form this replaces
 * asks for both: pick an employee, or write in someone who is not one. The
 * Medical Incident form says it outright — "If not a CRM employee, write the
 * injured persons name and company here" — and a contractor being hurt on site
 * is exactly the case a picker that only offers employees would block.
 *
 * So an employee match carries a user id, and anything typed that matches
 * nobody is kept as a plain name with an optional company. The record
 * distinguishes the two; the person filling the form does not have to think
 * about it.
 *
 * The dropdown renders through a portal. Overlays that don't get clipped by a
 * scrolling parent is a house rule here, learned the hard way.
 */
import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Check, Search, UserPlus, X } from 'lucide-react'
import { epmsApi, type DirectoryPerson } from '@/lib/api'

export interface PersonValue {
  userId: string | null
  name: string
  externalCompany: string | null
}

export const EMPTY_PERSON: PersonValue = { userId: null, name: '', externalCompany: null }

export function PersonPicker({
  value,
  onChange,
  placeholder = 'Search by name, or type someone who is not an employee',
  allowExternal = true,
  disabled = false,
}: {
  value: PersonValue
  onChange: (next: PersonValue) => void
  placeholder?: string
  allowExternal?: boolean
  disabled?: boolean
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const anchorRef = useRef<HTMLDivElement>(null)
  const listId = useId()

  const { data, isFetching } = useQuery({
    queryKey: ['directory', query],
    queryFn: () =>
      epmsApi.get<{ items: DirectoryPerson[] }>(
        `/api/v1/users/directory?page_size=20${query ? `&search=${encodeURIComponent(query)}` : ''}`,
      ),
    enabled: open,
    staleTime: 60_000,
  })
  const people = data?.items ?? []

  // Keep the dropdown pinned to the field while it is open.
  useEffect(() => {
    if (!open) return
    const place = () => setRect(anchorRef.current?.getBoundingClientRect() ?? null)
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const el = e.target as Node
      if (anchorRef.current?.contains(el)) return
      if ((el as HTMLElement).closest?.(`[data-picker="${listId}"]`)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, listId])

  const pickEmployee = (p: DirectoryPerson) => {
    onChange({ userId: p.id, name: p.full_name, externalCompany: null })
    setQuery('')
    setOpen(false)
  }

  const useTypedName = () => {
    onChange({ userId: null, name: query.trim(), externalCompany: value.externalCompany })
    setQuery('')
    setOpen(false)
  }

  const typedMatchesNobody =
    allowExternal && query.trim().length >= 2 &&
    !people.some((p) => p.full_name.toLowerCase() === query.trim().toLowerCase())

  // Already chosen — show it as a chip rather than a text field, so it is
  // obvious whether an employee was matched or a name was written in.
  if (value.name) {
    return (
      <div>
        <div className="flex min-h-[44px] items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3 py-2">
          <span className="min-w-0 flex-1 truncate text-sm text-neutral-900">{value.name}</span>
          <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold ${
            value.userId ? 'bg-primary-50 text-primary-700' : 'bg-warning-50 text-warning-700'}`}>
            {value.userId ? 'Employee' : 'Not an employee'}
          </span>
          {!disabled && (
            <button
              type="button"
              aria-label="Clear"
              onClick={() => { onChange(EMPTY_PERSON); setQuery('') }}
              className="shrink-0 rounded p-1 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          )}
        </div>
        {!value.userId && allowExternal && (
          <input
            className="mt-2 min-h-[44px] w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
            placeholder="Their company (optional)"
            value={value.externalCompany ?? ''}
            onChange={(e) => onChange({ ...value, externalCompany: e.target.value || null })}
            disabled={disabled}
          />
        )}
      </div>
    )
  }

  return (
    <div ref={anchorRef} className="relative">
      <div className="flex min-h-[44px] items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3">
        <Search className="h-4 w-4 shrink-0 text-neutral-400" aria-hidden />
        <input
          className="min-h-[44px] w-full border-0 bg-transparent py-2 text-sm outline-none"
          placeholder={placeholder}
          value={query}
          disabled={disabled}
          onFocus={() => setOpen(true)}
          onChange={(e) => { setQuery(e.target.value); setOpen(true) }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              if (people.length === 1) pickEmployee(people[0])
              else if (typedMatchesNobody) useTypedName()
            }
          }}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
        />
      </div>

      {open && rect && createPortal(
        <div
          data-picker={listId}
          id={listId}
          role="listbox"
          className="z-50 max-h-64 overflow-y-auto rounded-lg border border-neutral-200 bg-white py-1 shadow-lg"
          style={{ position: 'fixed', top: rect.bottom + 4, left: rect.left, width: rect.width }}
        >
          {isFetching && people.length === 0 && (
            <p className="px-3 py-2 text-sm text-neutral-500">Searching…</p>
          )}
          {people.map((p) => (
            <button
              key={p.id}
              type="button"
              role="option"
              aria-selected={false}
              onClick={() => pickEmployee(p)}
              className="flex w-full min-h-[44px] items-center gap-2 px-3 py-2 text-left hover:bg-neutral-50"
            >
              <Check className="h-4 w-4 shrink-0 text-transparent" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-neutral-900">{p.full_name}</span>
                <span className="block truncate text-xs text-neutral-500">
                  {p.department_name ? `${p.department_name} · ` : ''}{p.email}
                </span>
              </span>
            </button>
          ))}
          {!isFetching && people.length === 0 && !typedMatchesNobody && (
            <p className="px-3 py-2 text-sm text-neutral-500">
              {query ? 'No employee matches that.' : 'Start typing a name.'}
            </p>
          )}
          {typedMatchesNobody && (
            <button
              type="button"
              onClick={useTypedName}
              className="flex w-full min-h-[44px] items-center gap-2 border-t border-neutral-100 px-3 py-2 text-left hover:bg-neutral-50"
            >
              <UserPlus className="h-4 w-4 shrink-0 text-warning-600" aria-hidden />
              <span className="min-w-0">
                <span className="block truncate text-sm text-neutral-900">
                  Use “{query.trim()}”
                </span>
                <span className="block text-xs text-neutral-500">
                  Someone who is not an employee — a contractor or visitor
                </span>
              </span>
            </button>
          )}
        </div>,
        document.body,
      )}
    </div>
  )
}
