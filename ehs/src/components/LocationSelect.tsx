/**
 * Choosing where something happened.
 *
 * Staged, in the order HSE asked for: the general plant area first, then — only
 * for Production — which operational area, then optionally which line. Their
 * reasoning, verbatim: "employees may not know the building numbers. Selecting
 * the operational area by name is more intuitive and reduces the chance of
 * recording an incident against the wrong location."
 *
 * Two rules follow from their reply and neither is negotiable:
 *
 * **The line is optional.** "The system should not force the user to select a
 * line if they do not know which line the incident occurred on." So the
 * operational area stays a valid answer on its own, and the line stage opens
 * beneath it rather than replacing it.
 *
 * **Selectability is a property of the node, not a rule about the tree.** The
 * old version offered only childless nodes, which would now make every
 * operational area unselectable the moment Line 1 and Line 2 appeared beneath
 * it — forcing exactly the guess HSE asked us to avoid. `is_selectable` comes
 * from the record; "Production" is the one node that carries it false, because
 * "somewhere in Production" is not a place an incident happened.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

interface LocationNode {
  id: string
  code: string
  name: string
  path: string
  depth: number
  is_selectable: boolean
  children: LocationNode[]
}

function flatten(nodes: LocationNode[], out: LocationNode[] = []): LocationNode[] {
  for (const n of nodes) {
    out.push(n)
    flatten(n.children ?? [], out)
  }
  return out
}

/** The chain from a root down to `id`, so reopening lands where the value came from. */
function trail(nodes: LocationNode[], id: string): LocationNode[] {
  for (const node of nodes) {
    if (node.id === id) return [node]
    const deeper = trail(node.children ?? [], id)
    if (deeper.length) return [node, ...deeper]
  }
  return []
}

/** `Production/102 – Filling/Line 1` -> `Production › 102 – Filling › Line 1`. */
function readable(node: LocationNode | undefined): string | null {
  return node ? (node.path ?? node.name).split('/').join(' › ') : null
}

const selectClass =
  'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
  'text-neutral-900 focus:border-primary-400 focus:outline-none'

export function LocationSelect({
  value, onChange, disabled = false, placeholder = 'Where did it happen?',
}: {
  value: string | null
  /** The id, and its readable trail — form answers keep the wording they were
   *  filled in with, the same rule the vocabulary snapshots follow. */
  onChange: (id: string | null, path: string | null) => void
  disabled?: boolean
  placeholder?: string
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['locations'],
    queryFn: () => api.get<LocationNode[]>('/api/v1/settings/locations'),
    staleTime: 10 * 60_000,
  })

  const roots = useMemo(() => data ?? [], [data])
  const all = useMemo(() => flatten(roots), [roots])

  // Which branch is open. Derived from the value on first load so an existing
  // record reopens showing the path it was filed against, then held locally
  // while somebody is changing it.
  const [generalId, setGeneralId] = useState<string>('')
  const [areaId, setAreaId] = useState<string>('')

  useEffect(() => {
    if (!value || roots.length === 0) return
    const path = trail(roots, value)
    setGeneralId(path[0]?.id ?? '')
    setAreaId(path[1]?.id ?? '')
  }, [value, roots])

  if (isLoading) {
    return <p className="text-sm text-neutral-500">Loading areas…</p>
  }
  if (all.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-neutral-300 p-3 text-sm text-neutral-500">
        No plant areas have been set up yet.
      </p>
    )
  }

  const general = roots.find((n) => n.id === generalId) ?? null
  const areas = general?.children ?? []
  const area = areas.find((n) => n.id === areaId) ?? null
  const lines = area?.children ?? []

  const emit = (id: string | null) =>
    onChange(id, readable(all.find((n) => n.id === id)))

  const pickGeneral = (id: string) => {
    setGeneralId(id)
    setAreaId('')
    const node = roots.find((n) => n.id === id)
    // A general area that is an answer in its own right — the office, the
    // warehouse, outside — is recorded straight away. One that only groups
    // others clears the value until something beneath it is chosen.
    emit(node && node.is_selectable && (node.children ?? []).length === 0 ? id : null)
  }

  const pickArea = (id: string) => {
    setAreaId(id)
    // Recorded immediately, before any line is chosen: the line is optional
    // and this is already a complete answer.
    emit(id || null)
  }

  const pickLine = (id: string) => {
    // Clearing the line falls back to the area, which is a valid answer —
    // not to nothing.
    emit(id || areaId || null)
  }

  const selectedLine = value && value !== areaId && value !== generalId ? value : ''

  return (
    <div className="flex flex-col gap-1.5">
      <select className={selectClass} value={generalId} disabled={disabled}
              aria-label="Plant area"
              onChange={(e) => pickGeneral(e.target.value)}>
        <option value="">{placeholder}</option>
        {roots.map((n) => (
          <option key={n.id} value={n.id}>{n.name}</option>
        ))}
      </select>

      {areas.length > 0 && (
        <select className={selectClass} value={areaId} disabled={disabled}
                aria-label="Operational area"
                onChange={(e) => pickArea(e.target.value)}>
          <option value="">Which area?</option>
          {areas.map((n) => (
            <option key={n.id} value={n.id}>{n.name}</option>
          ))}
        </select>
      )}

      {lines.length > 0 && (
        <>
          <select className={selectClass} value={selectedLine} disabled={disabled}
                  aria-label="Line"
                  onChange={(e) => pickLine(e.target.value)}>
            <option value="">Line not known</option>
            {lines.map((n) => (
              <option key={n.id} value={n.id}>{n.name}</option>
            ))}
          </select>
          <p className="text-xs text-neutral-500">
            Leave the line blank if you are not sure — the area on its own is a
            complete answer.
          </p>
        </>
      )}

      {general && !general.is_selectable && !areaId && (
        <p className="text-xs text-neutral-500">
          {general.name} covers several areas. Choose which one.
        </p>
      )}
    </div>
  )
}
