/** Search-and-pick visitors from the registry.
 *
 * Supports multi-visitor visits: the first chosen visitor is the "primary"
 * (badge holder #1, the one shown in lists / used as the CFIA primary
 * row), the rest are companions. Both single- and multi-visitor flows go
 * through the same component — single visits leave `additional` empty.
 */
import { useState } from 'react'
import { Search, UserPlus, X } from 'lucide-react'
import { useVisitors, type Visitor } from '@/services/api'
import { cn } from '@/lib/utils'

interface Props {
  primary: Visitor | null
  additional: Visitor[]
  onChange: (primary: Visitor | null, additional: Visitor[]) => void
  onNew: () => void
}

export function VisitorSearch({ primary, additional, onChange, onNew }: Props) {
  const [query, setQuery] = useState('')
  const { data, isLoading } = useVisitors(query)
  const items = data?.items ?? []

  const pickedIds = new Set<string>(
    [primary?.id, ...additional.map((v) => v.id)].filter(Boolean) as string[],
  )

  const addVisitor = (v: Visitor) => {
    if (pickedIds.has(v.id)) return
    if (!primary) {
      onChange(v, additional)
    } else {
      onChange(primary, [...additional, v])
    }
    setQuery('')
  }

  const removeVisitor = (id: string) => {
    if (primary?.id === id) {
      // Promote the first companion to primary so the visit always has one.
      const [newPrimary, ...rest] = additional
      onChange(newPrimary ?? null, rest)
    } else {
      onChange(primary, additional.filter((v) => v.id !== id))
    }
  }

  return (
    <div>
      {/* Chosen visitors */}
      {primary && (
        <div className="mb-3 space-y-2">
          <VisitorChip
            visitor={primary}
            badgeLabel="PRIMARY"
            onRemove={() => removeVisitor(primary.id)}
          />
          {additional.map((v) => (
            <VisitorChip
              key={v.id}
              visitor={v}
              badgeLabel="COMPANION"
              onRemove={() => removeVisitor(v.id)}
            />
          ))}
        </div>
      )}

      {/* Search */}
      <label className="block text-sm font-medium text-neutral-700">
        {primary
          ? 'Add another visitor (companion)'
          : 'Search visitor (name / company / email)'}
      </label>
      <div className="relative mt-1">
        <Search className="absolute left-2 top-2.5 h-4 w-4 text-neutral-400" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Start typing…"
          className="w-full rounded-md border border-neutral-300 bg-white py-2 pl-8 pr-3 text-sm outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
        />
      </div>

      {query.length > 0 && (
        <div className="mt-2 max-h-64 overflow-y-auto rounded-md border border-neutral-200 bg-white">
          {isLoading && <p className="px-3 py-2 text-xs text-neutral-500">Searching…</p>}
          {!isLoading && items.length === 0 && (
            <div className="flex items-center justify-between px-3 py-2 text-xs">
              <span className="text-neutral-500">No visitors match — register a new one.</span>
              <button
                type="button"
                onClick={onNew}
                className="ml-2 inline-flex items-center gap-1 rounded bg-primary-600 px-2 py-1 text-white hover:bg-primary-700"
              >
                <UserPlus className="h-3 w-3" />
                New
              </button>
            </div>
          )}
          {items.map((v) => {
            const already = pickedIds.has(v.id)
            return (
              <button
                key={v.id}
                type="button"
                disabled={already}
                onClick={() => addVisitor(v)}
                className={cn(
                  'block w-full border-b border-neutral-100 px-3 py-2 text-left text-sm hover:bg-primary-50/40 disabled:opacity-40 disabled:hover:bg-transparent',
                )}
              >
                <p className="font-medium text-neutral-900">
                  {v.first_name} {v.last_name}
                  {already && <span className="ml-2 text-xs text-neutral-400">(already added)</span>}
                </p>
                <p className="text-xs text-neutral-500">
                  {v.company_name}{v.email ? ` · ${v.email}` : ''}
                </p>
              </button>
            )
          })}
        </div>
      )}

      {query.length === 0 && (
        <button
          type="button"
          onClick={onNew}
          className="mt-2 inline-flex items-center gap-1.5 text-sm text-primary-600 hover:underline"
        >
          <UserPlus className="h-4 w-4" />
          Register a new visitor
        </button>
      )}
    </div>
  )
}

function VisitorChip({
  visitor, badgeLabel, onRemove,
}: {
  visitor: Visitor
  badgeLabel: string
  onRemove: () => void
}) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-md border border-primary-200 bg-primary-50 p-3">
      <div className="min-w-0">
        <p className="text-xs font-medium text-primary-700">{badgeLabel}</p>
        <p className="mt-0.5 text-sm font-semibold text-neutral-900">
          {visitor.first_name} {visitor.last_name}
        </p>
        <p className="text-xs text-neutral-600">
          {visitor.company_name}
          {visitor.email ? ` · ${visitor.email}` : ''}
        </p>
      </div>
      <button
        type="button"
        onClick={onRemove}
        title="Remove visitor"
        className="shrink-0 rounded p-1 text-primary-700 hover:bg-primary-100"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
