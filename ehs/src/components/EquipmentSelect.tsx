/**
 * A piece of equipment from the equipment register (Settings → Equipment).
 *
 * Round 10 (open box): "add this equipment as a picker on all forms so that we
 * can identify what piece of equipment contributed to the incidents. Include it
 * on the hazard register, job analysis, and work permits."
 *
 * ★ Round 12, INC-13: "allow for the user to start typing in the asset name
 * and shorten the list". It was a dropdown of the first 500 machines by name —
 * of 7,170 — so the WWTP's and the warehouse's could not be chosen at all. It
 * now asks the server as you type.
 *
 * ★ INC-10/INC-11: given the area the record is filed against, it offers that
 * area's machines (and, when the area has none of its own, the nearest area
 * above it that has some), plus the few with no area. "Show all areas" is the
 * way out when the machine was moved and the register not told.
 *
 * Offers what is in service; a record that already names one since retired
 * shows it as itself, marked so, and keeps it (the server checks the same).
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { api } from '@/lib/api'
import { inputClass } from '@/components/ui'

export interface EquipmentItem {
  id: string; asset_no: string | null; name: string
  location_path: string | null; is_active: boolean
  system_label?: string | null
}

interface EquipmentPage { items: EquipmentItem[]; total: number; scope_path: string | null }

export function equipmentLabel(e: EquipmentItem): string {
  return `${e.asset_no ? `${e.asset_no} — ` : ''}${e.name}`
}

// ── Looking machines up by id ────────────────────────────────────────────────
// A list of hazards names a machine on every row. One request per row would be
// fifty requests; these are collected for a moment and asked for together.
// ★ A failed request rejects: "not in the register" is an answer, and a
// network blip given as that answer was kept for ten minutes (B2 review ②).
type Waiter = { resolve: (e: EquipmentItem | null) => void; reject: (err: unknown) => void }
let waiting = new Map<string, Waiter[]>()
let timer: ReturnType<typeof setTimeout> | null = null

function flush() {
  const batch = waiting
  waiting = new Map()
  timer = null
  const ids = [...batch.keys()]
  for (let i = 0; i < ids.length; i += 100) {
    const chunk = ids.slice(i, i + 100)
    const qs = new URLSearchParams()
    chunk.forEach((id) => qs.append('ids', id))
    api.get<EquipmentItem[]>(`/api/v1/equipment/lookup?${qs}`)
      .then((rows) => {
        const found = new Map(rows.map((r) => [r.id, r]))
        chunk.forEach((id) => batch.get(id)?.forEach((w) => w.resolve(found.get(id) ?? null)))
      })
      .catch((err) => chunk.forEach((id) => batch.get(id)?.forEach((w) => w.reject(err))))
  }
}

function lookUp(id: string): Promise<EquipmentItem | null> {
  return new Promise((resolve, reject) => {
    waiting.set(id, [...(waiting.get(id) ?? []), { resolve, reject }])
    if (!timer) timer = setTimeout(flush, 20)
  })
}

export function useEquipment(id: string | null | undefined) {
  return useQuery({
    queryKey: ['equipment', 'one', id],
    queryFn: () => lookUp(id as string),
    enabled: !!id,
    staleTime: 10 * 60_000,
  })
}

/** "EQ-12 — Capper (retired)", for a record's read-only view. */
export function useEquipmentName(id: string | null | undefined): string | null {
  const one = useEquipment(id)
  if (!id) return null
  if (one.isPending) return '…'
  if (one.isError) return 'Equipment could not be loaded'
  if (!one.data) return 'Equipment not in the register'
  return equipmentLabel(one.data) + (one.data.is_active ? '' : ' (retired)')
}

function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

const SHOWN = 30

function lastPart(path: string | null): string | null {
  return path ? path.split('/').pop() ?? path : null
}

interface SystemCount { system_id: string | null; label: string | null; count: number; is_active: boolean }

/** The Systems that have machines, for narrowing a pick. */
function useSystems(enabled: boolean) {
  return useQuery({
    queryKey: ['equipment', 'summary'],
    queryFn: () => api.get<{ systems: SystemCount[] }>('/api/v1/equipment/summary'),
    enabled,
    staleTime: 10 * 60_000,
  })
}

export default function EquipmentSelect({
  value, onChange, locationId, id, ariaLabel = 'Equipment', systemId,
}: {
  value: string | null
  onChange: (id: string | null) => void
  /** The area the record is filed against. Narrows the choices to it. */
  locationId?: string | null
  id?: string
  ariaLabel?: string
  /** ★ Round 12, JSA-01/02: a System the record itself saves (a JSA). Given,
   *  it narrows the list and the picker's own System choice is not shown.
   *  Not given (incidents, hazards, permits — D8), the picker offers a System
   *  that only narrows the list and is not saved. */
  systemId?: string | null
}) {
  const [term, setTerm] = useState('')
  const [open, setOpen] = useState(false)
  const [everywhere, setEverywhere] = useState(false)
  const [ownSystem, setOwnSystem] = useState('')
  const fixedSystem = systemId !== undefined
  const system = fixedSystem ? systemId ?? null : ownSystem || null
  const systems = useSystems(!fixedSystem)
  const search = useDebounced(term.trim())
  const near = everywhere ? null : locationId ?? null
  // A new area is a new question: narrow again.
  useEffect(() => { setEverywhere(false) }, [locationId])

  const chosen = useEquipment(value)
  const page = useQuery({
    queryKey: ['equipment', 'pick', search, near, system],
    queryFn: () => {
      const qs = new URLSearchParams({ limit: String(SHOWN) })
      if (search) qs.set('search', search)
      if (near) qs.set('near_location_id', near)
      if (system) qs.set('system_id', system)
      return api.get<EquipmentPage>(`/api/v1/equipment?${qs}`)
    },
    enabled: open,
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  })

  const pick = (eid: string | null) => { onChange(eid); setTerm(''); setOpen(false) }
  const current = chosen.data
  const currentText = current
    ? equipmentLabel(current) + (current.is_active ? '' : ' (retired)')
    : value
      ? chosen.isPending ? 'Loading…'
        : chosen.isError ? 'Equipment could not be loaded' : 'Equipment not in the register'
      : ''
  const items = page.data?.items ?? []
  const listId = `${id ?? 'equipment'}-list`

  return (
    <div onBlur={(e) => {
      if (!e.currentTarget.contains(e.relatedTarget)) { setOpen(false); setTerm('') }
    }}>
      {!fixedSystem && (systems.data?.systems ?? []).length > 0 && (
        <select aria-label={`${ariaLabel}: narrow by System`} value={ownSystem}
                onChange={(e) => { setOwnSystem(e.target.value); setOpen(true) }}
                className="mb-1 min-h-[32px] w-full rounded-md border border-neutral-200 bg-neutral-50 px-2 text-xs text-neutral-700 focus:border-primary-400 focus:outline-none">
          <option value="">Any System</option>
          {systems.data!.systems
            .filter((s) => s.system_id && s.is_active && s.count > 0)
            .sort((a, b) => (a.label ?? '').localeCompare(b.label ?? ''))
            .map((s) => <option key={s.system_id} value={s.system_id!}>{s.label} ({s.count})</option>)}
        </select>
      )}
      <div className="relative">
        <input
          id={id}
          className={`${inputClass} ${value ? 'pr-9' : ''}`}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-label={ariaLabel}
          autoComplete="off"
          placeholder={currentText || 'Type a name or asset number'}
          value={open ? term : currentText}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onChange={(e) => { setTerm(e.target.value); setOpen(true) }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && open && items[0]) { e.preventDefault(); pick(items[0].id) }
            if (e.key === 'Escape') setOpen(false)
          }}
        />
        {value && !open && (
          <button type="button" aria-label="Clear the equipment"
                  onClick={(e) => { e.preventDefault(); pick(null) }}
                  className="absolute right-1 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700">
            <X className="h-4 w-4" aria-hidden />
          </button>
        )}
      </div>
      {open && (
        <div onMouseDown={(e) => e.preventDefault()}
             className="mt-1 rounded-lg border border-neutral-200 bg-white">
          {locationId && (
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-neutral-100 px-3 py-2 text-xs text-neutral-600">
              <span>
                {everywhere
                  ? 'Showing equipment in every area'
                  : page.data
                    ? page.data.scope_path
                      ? <>Showing equipment in <span className="font-medium text-neutral-800">{page.data.scope_path}</span></>
                      : 'No equipment is registered in this area'
                    : 'Finding equipment in this area…'}
              </span>
              <button type="button"
                      onClick={(e) => { e.preventDefault(); setEverywhere(!everywhere) }}
                      className="min-h-[28px] font-medium text-primary-700 hover:underline">
                {everywhere ? 'Only this area' : 'Show all areas'}
              </button>
            </div>
          )}
          <ul id={listId} role="listbox" aria-label="Equipment" className="max-h-60 overflow-y-auto">
            {items.map((e) => (
              <li key={e.id} role="option" aria-selected={e.id === value}>
                <button type="button" onClick={(ev) => { ev.preventDefault(); pick(e.id) }}
                        className="block min-h-[40px] w-full px-3 py-2 text-left hover:bg-neutral-50">
                  <span className="block text-sm text-neutral-800">{equipmentLabel(e)}</span>
                  <span className="block text-xs text-neutral-500">
                    {[e.system_label, lastPart(e.location_path) ?? 'No area'].filter(Boolean).join(' · ')}
                  </span>
                </button>
              </li>
            ))}
            {items.length === 0 && (
              <li className="px-3 py-2 text-sm text-neutral-500">
                {page.isError ? 'The equipment register could not be loaded.'
                  : page.isPending ? 'Loading…'
                  : search ? `Nothing matches “${search}”.` : 'No equipment to choose from here.'}
              </li>
            )}
          </ul>
          {page.data && page.data.total > items.length && (
            <p className="border-t border-neutral-100 px-3 py-1.5 text-xs text-neutral-500">
              {items.length} of {page.data.total.toLocaleString()} shown — keep typing to narrow it.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

/** " · EQ-12 — Capper", after a record's area in a list or a heading. */
export function EquipmentName({ id }: { id: string | null | undefined }) {
  const name = useEquipmentName(id)
  return name ? <>{` · ${name}`}</> : null
}
