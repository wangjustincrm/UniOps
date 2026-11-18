/** Pick the Host (visited employee) by searching epms-api `/users/directory`. */
import { useState } from 'react'
import { Search } from 'lucide-react'
import { useUserDirectory, type UserBrief } from '@/services/api'

import { Button, Input, MenuItem, Card } from '@uniops/shell'
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
        <p className="mt-1 text-sm font-semibold text-fg">
          {selected.full_name}
        </p>
        <p className="text-xs text-fg-secondary">
          {selected.email}
          {selected.department_name ? ` · ${selected.department_name}` : ''}
        </p>
        <Button variant="link"
          type="button"
          onClick={() => onSelect(null as unknown as UserBrief)}
          className="mt-2 text-xs"
        >
          Change
        </Button>
      </div>
    )
  }

  return (
    <div>
      <label className="block text-sm font-medium text-fg-secondary">
        Search Host (the employee being visited)
      </label>
      <div className="relative mt-1">
        <Search className="absolute left-2 top-2.5 h-4 w-4 text-fg-muted" />
        <Input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Name or email (at least 2 chars)…"
          className="w-full pl-8 pr-3"
        />
      </div>

      {query.length >= 2 && (
        <Card inset className="mt-2 max-h-64 overflow-y-auto">
          {isLoading && <p className="px-3 py-2 text-xs text-fg-muted">Searching…</p>}
          {!isLoading && items.length === 0 && (
            <p className="px-3 py-2 text-xs text-fg-muted">No matches.</p>
          )}
          {items.map((u) => (
            <MenuItem
              key={u.id}
              onClick={() => onSelect(u)}
              className="block border-b border-line-subtle"
            >
              <p className="font-medium text-fg">{u.full_name}</p>
              <p className="text-xs text-fg-muted">
                {u.email}{u.department_name ? ` · ${u.department_name}` : ''}
              </p>
            </MenuItem>
          ))}
        </Card>
      )}
    </div>
  )
}
