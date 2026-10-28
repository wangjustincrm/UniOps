/**
 * Choosing from one of HSE's reference lists.
 *
 * As many levels as the list has. Round 10: "Allow for a master hazard to be
 * created, and a sub hazard(s) to be added under it … Under Sanitization
 * process, add Needs sanitization, Sanitization out of date, Not followed.
 * Rewrite the incident pickers to use this new format." That example is three
 * deep, so the picker walks the tree one level at a time rather than assuming
 * two. Picking the group first is both how the list is organised and how
 * somebody thinks about it: you know it was a fall before you know whether it
 * was from height or on the level. An earlier flat `Group › item` list put
 * eighty-five hazards in an order that looked random.
 *
 * Only entries with nothing under them can be chosen; a group is a heading.
 * Search cuts across the levels and shows the whole trail, so a result is
 * unambiguous when two groups use the same word.
 *
 * ★ What a caller gets back as `label` is the entry's trail ("Quality ›
 * Sanitization process › Not followed"), which the server computes from the
 * parent chain — that is what a record snapshots, because "Not followed" on its
 * own says nothing. For a flat list the trail is the label. Never rebuild it
 * from `path`: that is '/'-joined and labels contain '/'.
 *
 * ★ A saved value is resolved against retired entries too, and against entries
 * that have since had others added under them. Either way it is shown, marked,
 * instead of vanishing from the field while the record still says it.
 *
 * A flat list renders as one level. The shape of the data decides, not a prop.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Search, X } from 'lucide-react'
import { api, ApiError } from '@/lib/api'

export interface VocabItem {
  id: string
  code: string
  label: string
  parent_id: string | null
  path: string | null
  trail?: string | null
  sort_order?: number
  is_active?: boolean
}

interface Tree {
  /** Every entry, retired ones included, for resolving saved ids. */
  byId: Map<string, VocabItem>
  /** Live children of a live entry; '' is the top. */
  kids: Map<string, VocabItem[]>
  /** Live entries with nothing live under them — the choosable ones. */
  leaves: VocabItem[]
  /** Choosable entries under each group, for the count beside it. */
  under: Map<string, number>
}

function useVocabulary(code: string) {
  return useQuery({
    queryKey: ['vocabulary', code, 'all'],
    queryFn: () => api.get<VocabItem[]>(
      `/api/v1/settings/vocabularies/${code}/items?include_inactive=true`),
    staleTime: 10 * 60_000,
  })
}

const bySort = (a: VocabItem, b: VocabItem) =>
  (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.label.localeCompare(b.label)

const live = (i: VocabItem) => i.is_active !== false

/** Sorted within each parent, never across the whole list — sorting across it
 *  interleaves the children of different groups. */
function build(items: VocabItem[]): Tree {
  const byId = new Map(items.map((i) => [i.id, i]))
  const kids = new Map<string, VocabItem[]>()
  for (const i of items.filter(live)) {
    const parent = i.parent_id ? byId.get(i.parent_id) : undefined
    // A live entry under a retired one (older data) sits at the top rather
    // than becoming unreachable except by search.
    const key = parent && live(parent) ? parent.id : ''
    kids.set(key, [...(kids.get(key) ?? []), i])
  }
  for (const list of kids.values()) list.sort(bySort)
  const leaves = items.filter((i) => live(i) && !kids.has(i.id))
  const under = new Map<string, number>()
  const count = (id: string): number => {
    const k = kids.get(id)
    if (!k) return 1
    const n = k.reduce((s, c) => s + count(c.id), 0)
    under.set(id, n)
    return n
  }
  for (const top of kids.get('') ?? []) count(top.id)
  return { byId, kids, leaves, under }
}

export function trailOf(item: VocabItem): string {
  return item.trail ?? item.label
}

/** The item as a record should keep it: the label is the trail. */
const snapshot = (item: VocabItem): VocabItem => ({ ...item, label: trailOf(item) })

/** Live groups from the top down to the entry, the entry excluded. */
function groupsAbove(tree: Tree, item: VocabItem | undefined): string[] {
  const out: string[] = []
  let cur = item?.parent_id ? tree.byId.get(item.parent_id) : undefined
  const seen = new Set<string>()
  while (cur && live(cur) && !seen.has(cur.id)) {
    seen.add(cur.id)
    out.unshift(cur.id)
    cur = cur.parent_id ? tree.byId.get(cur.parent_id) : undefined
  }
  return out
}

/** Why a saved entry cannot be picked again, or null if it can. */
function stale(tree: Tree, id: string): string | null {
  const i = tree.byId.get(id)
  if (!i) return null
  if (!live(i)) return 'retired'
  if (tree.kids.has(i.id)) return 'now a heading — pick an entry under it'
  return null
}

const rowCls =
  'flex min-h-[44px] w-full items-center justify-between gap-2 px-3 text-left text-sm ' +
  'text-neutral-800 hover:bg-neutral-50'

function Missing({ vocabulary, error }: { vocabulary: string; error: unknown }) {
  // ★ Only a 404 means the list is not there. A refused or failed read said
  // the same, and sent somebody to fix a form that was fine (uniops-8b).
  if (!(error instanceof ApiError && error.status === 404)) {
    return (
      <p className="rounded-lg border border-danger-300 bg-danger-50 p-3 text-sm text-danger-700">
        Could not load the list. Reload the page; if it keeps happening, tell HSE.
      </p>
    )
  }
  // A question can point at a list that is not there — a form built when the
  // reference was typed by hand rather than chosen. Silently rendering an
  // empty dropdown is how an inspection becomes impossible to finish with
  // nothing on screen explaining why.
  return (
    <p className="rounded-lg border border-danger-300 bg-danger-50 p-3 text-sm text-danger-700">
      This question points at a list called “{vocabulary}”, and there is no
      such list. Whoever maintains the form needs to point it at one under
      Settings, Forms.
    </p>
  )
}

export function VocabularyMultiSelect({
  vocabulary, value, onChange, placeholder = 'Search…', emptyHint,
}: {
  vocabulary: string
  value: string[]
  /** The ids, and the chosen items themselves with `label` set to the trail —
   *  this module stores a label snapshot beside every vocabulary reference, so
   *  a renamed entry does not rewrite a three-year-old record. Callers that
   *  only need ids ignore it. */
  onChange: (ids: string[], items?: VocabItem[]) => void
  placeholder?: string
  emptyHint?: string
}) {
  const { data, isLoading, isError, error } = useVocabulary(vocabulary)
  const [query, setQuery] = useState('')
  // The groups walked into, top first.
  const [at, setAt] = useState<string[]>([])

  const tree = useMemo(() => build(data ?? []), [data])

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    return tree.leaves.filter((o) => !value.includes(o.id) && trailOf(o).toLowerCase().includes(q))
      .slice(0, 40)
  }, [tree, value, query])

  if (isLoading) return <p className="text-sm text-neutral-500">Loading…</p>
  if (isError) return <Missing vocabulary={vocabulary} error={error} />
  if (tree.leaves.length === 0 && value.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-neutral-300 p-3 text-sm text-neutral-500">
        {emptyHint ?? 'This list has no entries yet. Add them under Settings.'}
      </p>
    )
  }

  const here = at.length ? at[at.length - 1] : ''
  // Chosen entries are not offered twice — but a group stays, even when it is
  // itself a saved value (it became a heading after it was chosen): hiding it
  // would hide everything inside it.
  const rows = (tree.kids.get(here) ?? []).filter((o) => tree.kids.has(o.id) || !value.includes(o.id))
  const heading = here ? tree.byId.get(here) : undefined

  const emit = (ids: string[]) => onChange(
    ids, ids.map((id) => tree.byId.get(id)).filter(Boolean).map((i) => snapshot(i!)))
  const add = (id: string) => { emit([...value, id]); setQuery('') }

  return (
    <div>
      {value.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-1.5">
          {value.map((id) => {
            const c = tree.byId.get(id)
            const why = stale(tree, id)
            return (
              <li key={id}>
                <button type="button"
                        onClick={() => emit(value.filter((v) => v !== id))}
                        className={`flex min-h-[32px] items-center gap-1.5 rounded-full px-2.5 text-xs font-medium ${
                          why ? 'bg-neutral-100 text-neutral-600' : 'bg-primary-50 text-primary-700'}`}>
                  {c ? trailOf(c) : 'An entry no longer on the list'}
                  {why && <span className="font-normal">({why})</span>}
                  <X className="h-3 w-3" aria-hidden />
                  <span className="sr-only">Remove</span>
                </button>
              </li>
            )
          })}
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

      <div className="mt-1.5 max-h-64 overflow-y-auto rounded-lg border border-neutral-200 bg-white">
        {query.trim() ? (
          matches.length === 0 ? (
            <p className="px-3 py-2.5 text-sm text-neutral-500">Nothing matches “{query}”.</p>
          ) : (
            <ul>
              {matches.map((o) => (
                <li key={o.id}>
                  <button type="button" className={rowCls} onClick={() => add(o.id)}>
                    <span className="min-w-0 truncate">{trailOf(o)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )
        ) : (
          <>
            {heading && (
              <button type="button" onClick={() => setAt(at.slice(0, -1))}
                      className="flex min-h-[40px] w-full items-center gap-1.5 border-b border-neutral-200 bg-neutral-50 px-2.5 text-left text-xs font-semibold text-neutral-700">
                <ChevronLeft className="h-4 w-4 shrink-0" aria-hidden />
                <span className="min-w-0 truncate">{trailOf(heading)}</span>
              </button>
            )}
            <ul>
              {rows.map((o) => tree.kids.has(o.id) ? (
                <li key={o.id}>
                  <button type="button" className={rowCls} onClick={() => setAt([...at, o.id])}>
                    <span className="min-w-0 truncate font-medium">{o.label}</span>
                    <span className="flex shrink-0 items-center gap-0.5 text-xs text-neutral-400">
                      {tree.under.get(o.id)}
                      <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                    </span>
                  </button>
                </li>
              ) : (
                <li key={o.id}>
                  <button type="button" className={rowCls} onClick={() => add(o.id)}>
                    <span className="min-w-0 truncate">{o.label}</span>
                  </button>
                </li>
              ))}
              {rows.length === 0 && (
                <li className="px-3 py-2.5 text-sm text-neutral-500">Everything here is chosen.</li>
              )}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}

/**
 * One choice, as one select per level: the group, then what is under it, and
 * so on until an entry with nothing under it. A flat list is a single select.
 */
export function VocabularySelect({
  vocabulary, value, onChange, placeholder = 'Select…', currentLabel,
}: {
  vocabulary: string
  value: string | null
  /** The chosen item with `label` set to its trail, or null. */
  onChange: (item: VocabItem | null) => void
  placeholder?: string
  /** The label saved with `value`, shown if the entry is not in the list at
   *  all. ★ Review (uniops-8b): a retired entry read "Not set" while the
   *  record still said otherwise. */
  currentLabel?: string | null
}) {
  const { data, isError, error } = useVocabulary(vocabulary)
  const tree = useMemo(() => build(data ?? []), [data])
  // Groups opened by hand; null until the person touches the field, so it
  // opens where the saved value sits.
  const [opened, setOpened] = useState<string[] | null>(null)

  if (isError) return <Missing vocabulary={vocabulary} error={error} />

  const selected = value ? tree.byId.get(value) : undefined
  const why = value && data ? (selected ? stale(tree, value) : 'retired') : null
  const chain = opened ?? (why ? [] : groupsAbove(tree, selected))

  const selectCls =
    'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
    'focus:border-primary-400 focus:outline-none'

  // One select per level: the top, then under each opened group.
  const levels = ['', ...chain]
  // ★ Opening a group changes nothing saved (uniops-8b): looking inside one
  // used to clear the value, and saving then cleared the record's wording
  // with it. The value changes only when an entry is picked, or when the top
  // select is set back to empty — a deliberate clear.
  const pick = (depth: number, id: string) => {
    const item = id ? tree.byId.get(id) : undefined
    const kept = chain.slice(0, depth)
    if (item && tree.kids.has(item.id)) {
      setOpened([...kept, item.id])
    } else if (item) {
      setOpened(kept)
      onChange(snapshot(item))
    } else {
      setOpened(kept)
      if (depth === 0) onChange(null)
    }
  }
  const home = groupsAbove(tree, selected)
  // Somewhere other than where the saved entry sits, with nothing picked yet.
  const elsewhere = Boolean(value && data && opened &&
    !(opened.length === home.length && opened.every((g, i) => g === home[i])))
  const savedAs = selected ? trailOf(selected) : (currentLabel ?? 'an entry no longer on the list')

  return (
    <div className="flex flex-col gap-1.5">
      {(why || elsewhere) && (
        <p className="text-xs text-neutral-500">
          {elsewhere ? 'Still saved as' : 'Saved as'} {savedAs}{why ? ` (${why})` : ''}
          {elsewhere ? ' until you pick an entry under it.' : '.'}
        </p>
      )}
      {levels.map((parent, depth) => {
        const options = tree.kids.get(parent) ?? []
        const next = levels[depth + 1] ?? (why ? '' : (value ?? ''))
        return (
          <select key={`${depth}-${parent}`} className={selectCls}
                  aria-label={depth === 0 ? undefined : `Under ${tree.byId.get(parent)?.label ?? ''}`}
                  value={options.some((o) => o.id === next) ? next : ''}
                  onChange={(e) => pick(depth, e.target.value)}>
            <option value="">{depth === 0 ? placeholder : 'Select…'}</option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}{tree.kids.has(o.id) ? ' ›' : ''}
              </option>
            ))}
          </select>
        )
      })}
    </div>
  )
}
