/**
 * Choosing from one of HSE's reference lists.
 *
 * Two levels, because the lists are two levels. An earlier version flattened
 * them into one list of `Group › item` strings, which put eighty-five hazards in
 * front of someone in an order that looked random — group and child carry their
 * own independent sort_order, so a flat sort interleaves the children of
 * different groups. Picking the group first is both how the list is organised
 * and how somebody thinks about it: you know it was a fall before you know
 * whether it was from height or on the level.
 *
 * Search stays, and cuts across the groups: eighty-five entries is more than
 * anyone wants to navigate when they already know the word. Searching shows the
 * full path so a result is unambiguous.
 *
 * A flat list (immediate causes, body parts) renders as one level. The shape of
 * the data decides, not a prop — a caller should not have to know.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, Search, X } from 'lucide-react'
import { api } from '@/lib/api'

export interface VocabItem {
  id: string
  code: string
  label: string
  parent_id: string | null
  path: string | null
  sort_order?: number
}

interface Grouped {
  /** Null when the list has no groups at all. */
  group: VocabItem | null
  items: VocabItem[]
}

function useVocabulary(code: string) {
  return useQuery({
    queryKey: ['vocabulary', code],
    queryFn: () => api.get<VocabItem[]>(`/api/v1/settings/vocabularies/${code}/items`),
    staleTime: 10 * 60_000,
  })
}

const bySort = (a: VocabItem, b: VocabItem) =>
  (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.label.localeCompare(b.label)

/**
 * Split a flat list into groups and their children.
 *
 * Sorting happens within each group rather than across the whole list, which is
 * the fix for the interleaving: a group's children are ordered among themselves
 * and never against another group's.
 */
function group(items: VocabItem[]): Grouped[] {
  const parents = new Set(items.map((i) => i.parent_id).filter(Boolean) as string[])
  const tops = items.filter((i) => !i.parent_id).sort(bySort)

  // No item has a parent → the whole list is one level.
  if (parents.size === 0) return [{ group: null, items: tops }]

  const out: Grouped[] = []
  for (const top of tops) {
    const children = items.filter((i) => i.parent_id === top.id).sort(bySort)
    // A top-level entry with no children is selectable in its own right —
    // immediate causes mix both shapes in one list.
    out.push(children.length ? { group: top, items: children } : { group: null, items: [top] })
  }
  return out
}

/** Every selectable entry, for search and for resolving ids to labels. */
function leaves(items: VocabItem[]): VocabItem[] {
  const parents = new Set(items.map((i) => i.parent_id).filter(Boolean) as string[])
  return items.filter((i) => !parents.has(i.id))
}

function trail(item: VocabItem): string {
  return (item.path ?? item.label).split('/').join(' › ')
}

const rowCls =
  'flex min-h-[44px] w-full items-center justify-between gap-2 px-3 text-left text-sm ' +
  'text-neutral-800 hover:bg-neutral-50'

export function VocabularyMultiSelect({
  vocabulary, value, onChange, placeholder = 'Search…', emptyHint,
}: {
  vocabulary: string
  value: string[]
  /** The ids, and the chosen items themselves.
   *
   *  The second argument exists because this module stores a label snapshot
   *  beside every vocabulary reference — a renamed entry must not rewrite a
   *  three-year-old record. Callers that only need ids ignore it. */
  onChange: (ids: string[], items?: VocabItem[]) => void
  placeholder?: string
  emptyHint?: string
}) {
  const { data, isLoading, isError } = useVocabulary(vocabulary)
  const [query, setQuery] = useState('')
  const [openGroup, setOpenGroup] = useState<string | null>(null)

  const all = useMemo(() => leaves(data ?? []), [data])
  const groups = useMemo(() => group(data ?? []), [data])
  const chosen = useMemo(() => all.filter((o) => value.includes(o.id)), [all, value])

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    return all.filter((o) => !value.includes(o.id) && trail(o).toLowerCase().includes(q))
      .slice(0, 40)
  }, [all, value, query])

  if (isLoading) return <p className="text-sm text-neutral-500">Loading…</p>
  if (all.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-neutral-300 p-3 text-sm text-neutral-500">
        {emptyHint ?? 'This list has no entries yet. Add them under Settings.'}
      </p>
    )
  }

  const current = groups.find((g) => g.group?.id === openGroup)
  // A question can point at a list that is not there — a form built when the
  // reference was typed by hand rather than chosen. Silently rendering an
  // empty dropdown is how an inspection becomes impossible to finish with
  // nothing on screen explaining why.
  if (isError) {
    return (
      <p className="rounded-lg border border-danger-300 bg-danger-50 p-3 text-sm text-danger-700">
        This question points at a list called “{vocabulary}”, and there is no
        such list. Whoever maintains the form needs to point it at one under
        Settings, Forms.
      </p>
    )
  }

  const flat = groups.length === 1 && groups[0].group === null

  const chosenFor = (ids: string[]) => all.filter((o) => ids.includes(o.id))
  const emit = (ids: string[]) => onChange(ids, chosenFor(ids))
  const add = (id: string) => { emit([...value, id]); setQuery('') }

  return (
    <div>
      {chosen.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-1.5">
          {chosen.map((c) => (
            <li key={c.id}>
              <button type="button"
                      onClick={() => emit(value.filter((v) => v !== c.id))}
                      className="flex min-h-[32px] items-center gap-1.5 rounded-full bg-primary-50 px-2.5 text-xs font-medium text-primary-700">
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
          onChange={(e) => { setQuery(e.target.value); setOpenGroup(null) }}
          placeholder={placeholder}
          className="min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white pl-8 pr-3 text-sm focus:border-primary-400 focus:outline-none"
        />
      </div>

      <div className="mt-1.5 max-h-64 overflow-y-auto rounded-lg border border-neutral-200 bg-white">
        {/* Searching cuts across the groups — the full path keeps a result
            unambiguous when two groups use the same word. */}
        {query.trim() ? (
          matches.length === 0 ? (
            <p className="px-3 py-2.5 text-sm text-neutral-500">Nothing matches “{query}”.</p>
          ) : (
            <ul>
              {matches.map((o) => (
                <li key={o.id}>
                  <button type="button" className={rowCls} onClick={() => add(o.id)}>
                    <span className="min-w-0 truncate">{trail(o)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )
        ) : flat ? (
          <ul>
            {groups[0].items.filter((o) => !value.includes(o.id)).map((o) => (
              <li key={o.id}>
                <button type="button" className={rowCls} onClick={() => add(o.id)}>
                  <span className="min-w-0 truncate">{o.label}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : current ? (
          <>
            <button type="button" onClick={() => setOpenGroup(null)}
                    className="flex min-h-[40px] w-full items-center gap-1.5 border-b border-neutral-200 bg-neutral-50 px-2.5 text-left text-xs font-semibold text-neutral-700">
              <ChevronLeft className="h-4 w-4" aria-hidden />
              {current.group?.label}
            </button>
            <ul>
              {current.items.filter((o) => !value.includes(o.id)).map((o) => (
                <li key={o.id}>
                  <button type="button" className={rowCls} onClick={() => add(o.id)}>
                    <span className="min-w-0 truncate">{o.label}</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <ul>
            {groups.map((g) => g.group ? (
              <li key={g.group.id}>
                <button type="button" className={rowCls}
                        onClick={() => setOpenGroup(g.group!.id)}>
                  <span className="min-w-0 truncate font-medium">{g.group.label}</span>
                  <span className="shrink-0 text-xs text-neutral-400">{g.items.length}</span>
                </button>
              </li>
            ) : (
              // Top-level entries with no children sit alongside the groups.
              g.items.filter((o) => !value.includes(o.id)).map((o) => (
                <li key={o.id}>
                  <button type="button" className={rowCls} onClick={() => add(o.id)}>
                    <span className="min-w-0 truncate">{o.label}</span>
                  </button>
                </li>
              ))
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

/**
 * One choice, as two selects: the group, then the entry within it.
 *
 * Two selects rather than one long list for the same reason as above. A flat
 * vocabulary collapses to a single select, so a caller does not have to know
 * which shape it is getting.
 */
export function VocabularySelect({
  vocabulary, value, onChange, placeholder = 'Select…',
}: {
  vocabulary: string
  value: string | null
  onChange: (item: VocabItem | null) => void
  placeholder?: string
}) {
  const { data, isError } = useVocabulary(vocabulary)
  const groups = useMemo(() => group(data ?? []), [data])
  const all = useMemo(() => leaves(data ?? []), [data])

  const selected = all.find((o) => o.id === value) ?? null
  // The group of whatever is selected, so reopening the field lands where the
  // value came from rather than back at the top.
  const groupOf = (item: VocabItem | null) =>
    item ? (groups.find((g) => g.items.some((i) => i.id === item.id))?.group?.id ?? '') : ''

  const [openGroup, setOpenGroup] = useState<string>('')
  const activeGroup = openGroup || groupOf(selected)

  if (isError) {
    return (
      <p className="rounded-lg border border-danger-300 bg-danger-50 p-3 text-sm text-danger-700">
        This question points at a list called “{vocabulary}”, and there is no
        such list. Whoever maintains the form needs to point it at one under
        Settings, Forms.
      </p>
    )
  }

  const flat = groups.length === 1 && groups[0].group === null
  const selectCls =
    'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
    'focus:border-primary-400 focus:outline-none'

  if (flat) {
    return (
      <select className={selectCls} value={value ?? ''}
              onChange={(e) => onChange(all.find((o) => o.id === e.target.value) ?? null)}>
        <option value="">{placeholder}</option>
        {groups[0].items.map((o) => (
          <option key={o.id} value={o.id}>{o.label}</option>
        ))}
      </select>
    )
  }

  // Ungrouped top-level entries are offered alongside the groups, since a list
  // can hold both — immediate causes does.
  const loose = groups.filter((g) => !g.group).flatMap((g) => g.items)
  const withGroups = groups.filter((g) => g.group)
  const current = withGroups.find((g) => g.group!.id === activeGroup)

  return (
    <div className="flex flex-col gap-1.5">
      <select
        className={selectCls}
        value={activeGroup || (selected && !groupOf(selected) ? '__loose__' : '')}
        onChange={(e) => {
          setOpenGroup(e.target.value === '__loose__' ? '' : e.target.value)
          onChange(null)
        }}
      >
        <option value="">{placeholder}</option>
        {withGroups.map((g) => (
          <option key={g.group!.id} value={g.group!.id}>{g.group!.label}</option>
        ))}
        {loose.length > 0 && <option value="__loose__">Other</option>}
      </select>

      {(current || (loose.length > 0 && !activeGroup && selected && !groupOf(selected))) && (
        <select className={selectCls} value={value ?? ''}
                onChange={(e) => onChange(all.find((o) => o.id === e.target.value) ?? null)}>
          <option value="">Select…</option>
          {(current ? current.items : loose).map((o) => (
            <option key={o.id} value={o.id}>{o.label}</option>
          ))}
        </select>
      )}
    </div>
  )
}
