/** Pick the Host (visited employee) by searching epms-api `/users/directory`. */
import { useState } from 'react'
import { Search } from 'lucide-react'
import { useUserDirectory, type UserBrief } from '@/services/api'

export function HostSearch({
  selected,
  onSelect,
}: {
  selected: UserBrief | null
  onSelect: (u: UserBrief) => void
}) {
  const [query, setQuery] = useState('')
  const { data, isLoading } = useUserDirectory(query)
  const items = data?.items ?? []

  if (selected) {
    return (
      <div className="rounded-md border border-primary-200 bg-primary-50 p-3">
        <p className="text-xs font-medium text-primary-700">HOST</p>
        <p className="mt-1 text-sm font-semibold text-neutral-900">
          {selected.full_name}
        </p>
        <p className="text-xs text-neutral-600">
          {selected.email}
          {selected.department_name ? ` · ${selected.department_name}` : ''}
        </p>
        <button
          type="button"
          onClick={() => onSelect(null as unknown as UserBrief)}
          className="mt-2 text-xs text-primary-700 hover:underline"
        >
          Change
        </button>
      </div>
    )
  }

  return (
    <div>
      <label className="block text-sm font-medium text-neutral-700">
        Search Host (the employee being visited)
      </label>
      <div className="relative mt-1">
        <Search className="absolute left-2 top-2.5 h-4 w-4 text-neutral-400" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Name or email (at least 2 chars)…"
          className="w-full rounded-md border border-neutral-300 bg-white py-2 pl-8 pr-3 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
        />
      </div>

      {query.length >= 2 && (
        <div className="mt-2 max-h-64 overflow-y-auto rounded-md border border-neutral-200 bg-white">
          {isLoading && <p className="px-3 py-2 text-xs text-neutral-500">Searching…</p>}
          {!isLoading && items.length === 0 && (
            <p className="px-3 py-2 text-xs text-neutral-500">No matches.</p>
          )}
          {items.map((u) => (
            <button
              key={u.id}
              type="button"
              onClick={() => onSelect(u)}
              className="block w-full border-b border-neutral-100 px-3 py-2 text-left text-sm hover:bg-primary-50/40"
            >
              <p className="font-medium text-neutral-900">{u.full_name}</p>
              <p className="text-xs text-neutral-500">
                {u.email}{u.department_name ? ` · ${u.department_name}` : ''}
              </p>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
