/**
 * Choosing from one of HSE's reference lists.
 *
 * Every list they supplied is two levels deep — a heading with items under it —
 * so the group is a label, never a choice. This is the same rule the area
 * picker follows: you select the specific thing, not the bucket it sits in.
 * Keeping the two consistent matters more than either rule on its own, because
 * someone who learns one and meets the other will assume it works the same way.
 *
 * A flat list is used rather than a tree widget. These lists run to ninety
 * entries, and on a phone an expanding tree is more taps than a filtered list.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, Search, X } from 'lucide-react'
import { api } from '@/lib/api'

export interface VocabItem {
  id: string
  code: string
  label: string
  parent_id: string | null
  path: string | null
}

function useVocabulary(code: string) {
  return useQuery({
    queryKey: ['vocabulary', code],
    queryFn: () => api.get<VocabItem[]>(`/api/v1/settings/vocabularies/${code}/items`),
    staleTime: 10 * 60_000,
  })
}

/** `Chemical Exposure Items/Chemical Gloves` -> `Chemical Exposure Items › Chemical Gloves` */
function trail(item: VocabItem): string {
  return (item.path ?? item.label).split('/').join(' › ')
}

/** Leaves only — a heading is a label, not a choice. A list with no children at
 *  all (immediate causes, body parts) is all leaves. */
function selectable(items: VocabItem[]): VocabItem[] {
  const parents = new Set(items.map((i) => i.parent_id).filter(Boolean))
  return items.filter((i) => !parents.has(i.id))
}

export function VocabularyMultiSelect({
  vocabulary, value, onChange, placeholder = 'Search…', emptyHint,
}: {
  vocabulary: string
  value: string[]
  onChange: (ids: string[]) => void
  placeholder?: string
  emptyHint?: string
}) {
  const { data, isLoading } = useVocabulary(vocabulary)
  const [query, setQuery] = useState('')

  const options = useMemo(() => selectable(data ?? []), [data])
  const chosen = useMemo(
    () => options.filter((o) => value.includes(o.id)),
    [options, value],
  )
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    const pool = options.filter((o) => !value.includes(o.id))
    if (!q) return pool.slice(0, 40)
    return pool.filter((o) => trail(o).toLowerCase().includes(q)).slice(0, 40)
  }, [options, value, query])

  if (isLoading) {
    return <p className="text-sm text-neutral-500">Loading…</p>
  }
  if (options.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-neutral-300 p-3 text-sm text-neutral-500">
        {emptyHint ?? 'This list has no entries yet. Add them under Settings.'}
      </p>
    )
  }

  return (
    <div>
      {chosen.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-1.5">
          {chosen.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => onChange(value.filter((v) => v !== c.id))}
                className="flex min-h-[32px] items-center gap-1.5 rounded-full bg-primary-50 px-2.5 text-xs font-medium text-primary-700"
              >
                {trail(c)}
                <X className="h-3 w-3" aria-hidden />
                <span className="sr-only">Remove</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400"
                aria-hidden />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={placeholder}
          className="min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white pl-8 pr-3 text-sm focus:border-primary-400 focus:outline-none"
        />
      </div>

      <ul className="mt-1.5 max-h-56 overflow-y-auto rounded-lg border border-neutral-200 bg-white">
        {matches.length === 0 ? (
          <li className="px-3 py-2.5 text-sm text-neutral-500">Nothing matches “{query}”.</li>
        ) : matches.map((o) => (
          <li key={o.id}>
            <button
              type="button"
              onClick={() => { onChange([...value, o.id]); setQuery('') }}
              className="flex min-h-[40px] w-full items-center justify-between gap-2 px-3 text-left text-sm text-neutral-800 hover:bg-neutral-50"
            >
              <span className="min-w-0 truncate">{trail(o)}</span>
              <Check className="h-3.5 w-3.5 shrink-0 text-neutral-300" aria-hidden />
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Single choice from a list, returning the item so the caller can snapshot the
 *  label — a cause keeps the wording it was recorded under. */
export function VocabularySelect({
  vocabulary, value, onChange, placeholder = 'Select…',
}: {
  vocabulary: string
  value: string | null
  onChange: (item: VocabItem | null) => void
  placeholder?: string
}) {
  const { data } = useVocabulary(vocabulary)
  const options = useMemo(() => selectable(data ?? []), [data])

  return (
    <select
      value={value ?? ''}
      onChange={(e) => onChange(options.find((o) => o.id === e.target.value) ?? null)}
      className="min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:border-primary-400 focus:outline-none"
    >
      <option value="">{placeholder}</option>
      {options.map((o) => (
        <option key={o.id} value={o.id}>{trail(o)}</option>
      ))}
    </select>
  )
}
