/**
 * Choosing a plant area.
 *
 * Only leaves are offered: an interior node is a grouping (Building 102, or
 * Pretreatment, which HSE's list names only as `Pretreatment – <line>`), and
 * picking one would record work against a whole building. Depth is not the
 * test — the tree is deliberately ragged, so "childless" is the only rule that
 * holds across branches.
 *
 * The incident form has the same logic inline. Worth folding into this once
 * there is a reason to touch that file.
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

interface LocationNode {
  id: string
  name: string
  path: string
  depth: number
  children: LocationNode[]
}

function leaves(nodes: LocationNode[], out: LocationNode[] = []): LocationNode[] {
  for (const n of nodes) {
    if ((n.children ?? []).length === 0) out.push(n)
    else leaves(n.children, out)
  }
  return out
}

/** `Kingston Plant/Building 103/Filling` -> `Building 103 › Filling`. */
function trail(path: string): string {
  return path.split('/').slice(1).join(' › ')
}

export function LocationSelect({
  value, onChange, disabled = false, placeholder = 'Select an area',
}: {
  value: string | null
  onChange: (id: string | null, path: string | null) => void
  disabled?: boolean
  placeholder?: string
}) {
  const { data } = useQuery({
    queryKey: ['locations'],
    queryFn: () => api.get<LocationNode[]>('/api/v1/settings/locations'),
    staleTime: 10 * 60_000,
  })
  const options = useMemo(() => leaves(data ?? []), [data])

  return (
    <select
      disabled={disabled}
      value={value ?? ''}
      onChange={(e) => {
        const hit = options.find((o) => o.id === e.target.value)
        onChange(hit?.id ?? null, hit?.path ?? null)
      }}
      className="min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:border-primary-400 focus:outline-none"
    >
      <option value="">{placeholder}</option>
      {options.map((o) => (
        <option key={o.id} value={o.id}>{trail(o.path)}</option>
      ))}
    </select>
  )
}
