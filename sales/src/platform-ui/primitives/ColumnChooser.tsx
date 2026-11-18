import { useState } from 'react'
import { ArrowDown, ArrowUp, Eye, Lock } from 'lucide-react'
import { Checkbox } from './Checkbox'
import { Popover } from './Popover'
import { Input } from './shell'
import { TourMark } from '../help'

export interface ColumnChoice {
  id: string
  label: string
  /** The group it is listed under (a ColumnGroup id); none: one plain list. */
  group?: string
  /** Locked: it stays in place while the table scrolls sideways (a 🔒 beside it). */
  locked?: boolean
}

export interface ColumnGroup {
  id: string
  label: string
}

/** Above this many columns the chooser has a box to find one by its name. */
const FIND_FROM = 8

type Tri = boolean | 'indeterminate'
const triOf = (ids: string[], hidden: string[]): Tri => {
  const shown = ids.filter((id) => !hidden.includes(id)).length
  return shown === 0 ? false : shown === ids.length ? true : 'indeterminate'
}

/** The eye at the left of the header: show or hide any column, and move it
 *  up or down (the keyboard way to reorder; the header can also be dragged).
 *  A hidden column can still be searched and sorted on. With `groups`, the
 *  columns are listed under them ("On this list", "More fields"); a box at the
 *  top and one per group shows or hides them all at once (a dash: some shown),
 *  only the ones a find matches. The last column shown always stays. */
export function ColumnChooser({ columns, hidden, onChange, onMove, groups }: {
  /** In display order. */
  columns: ColumnChoice[]
  hidden: string[]
  onChange: (hidden: string[]) => void
  onMove?: (id: string, delta: -1 | 1) => void
  groups?: ColumnGroup[]
}) {
  const [find, setFind] = useState('')
  const q = find.trim().toLowerCase()
  const matching = q ? columns.filter((c) => c.label.toLowerCase().includes(q) || c.id.toLowerCase().includes(q)) : columns
  const visibleCount = columns.filter((c) => !hidden.includes(c.id)).length

  /** Show or hide these at once; hiding never leaves the table with no column (the first shown stays). */
  const setAll = (ids: string[], show: boolean) => {
    if (show) return onChange(hidden.filter((h) => !ids.includes(h)))
    const next = [...new Set([...hidden, ...ids])]
    if (next.length >= columns.length) {
      const keep = columns.find((c) => !hidden.includes(c.id)) ?? columns[0]
      return onChange(next.filter((h) => h !== keep.id))
    }
    onChange(next)
  }
  const allBox = (ids: string[], label: string, aria: string) => {
    const state = triOf(ids, hidden)
    return (
      <Checkbox label={label} aria-label={aria} checked={state} disabled={ids.length === 0}
        onCheckedChange={() => setAll(ids, state !== true)} className="min-w-0 flex-1 font-medium" />
    )
  }
  const row = (c: ColumnChoice) => {
    const shown = !hidden.includes(c.id)
    const i = columns.indexOf(c)
    return (
      <li key={c.id} className="flex items-center gap-1 rounded px-1 py-1 hover:bg-neutral-50">
        <Checkbox
          label={c.locked ? <span className="inline-flex items-center gap-1">{c.label}<Lock aria-label="locked" className="h-3 w-3 text-neutral-400" /></span> : c.label}
          aria-label={c.label}
          className="min-w-0 flex-1"
          checked={shown}
          disabled={shown && visibleCount === 1}  /* never hide the last column */
          onCheckedChange={() => onChange(shown ? [...hidden, c.id] : hidden.filter((h) => h !== c.id))}
        />
        {onMove && !q && (
          <>
            <button type="button" aria-label={`Move ${c.label} up`} disabled={i === 0} onClick={() => onMove(c.id, -1)}
              className="rounded p-0.5 text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700 disabled:opacity-30">
              <ArrowUp className="h-3.5 w-3.5" />
            </button>
            <button type="button" aria-label={`Move ${c.label} down`} disabled={i === columns.length - 1} onClick={() => onMove(c.id, 1)}
              className="rounded p-0.5 text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700 disabled:opacity-30">
              <ArrowDown className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </li>
    )
  }
  const sections = groups?.length
    ? groups.map((g) => ({ g, items: matching.filter((c) => c.group === g.id) })).filter((x) => x.items.length > 0)
    : [{ g: null, items: matching }]
  return (
    <TourMark help="list.columns" slot="list.columns" className="inline-flex">
    <Popover
      align="start"
      className="max-h-96 w-72 overflow-y-auto"
      trigger={
        <button type="button" aria-label="Show, hide or move columns" className="rounded p-0.5 text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700">
          <Eye className="h-3.5 w-3.5" />
        </button>
      }
    >
      <p className="px-1 pb-1 text-xs font-medium normal-case tracking-normal text-neutral-500">Columns</p>
      {columns.length > FIND_FROM && (
        <Input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a field…" aria-label="Find a field"
          className="mb-1 h-8 text-sm normal-case tracking-normal" />
      )}
      <div className="flex flex-col text-sm normal-case tracking-normal text-neutral-700">
        <div className="flex items-center border-b border-neutral-100 px-1 py-1">
          {allBox(matching.map((c) => c.id), q ? `All matching (${matching.length})` : 'All columns', 'Show or hide all columns')}
        </div>
        {matching.length === 0 && <p className="px-1 py-2 text-xs text-neutral-500">No field matches.</p>}
        {sections.map(({ g, items }) => (
          <div key={g?.id ?? 'all'} role="group" aria-label={g?.label ?? 'Columns'}>
            {g && (
              <div className="flex items-center px-1 pt-2 text-xs uppercase tracking-wide text-neutral-500">
                {allBox(items.map((c) => c.id), `${g.label} (${items.length})`, `Show or hide all of ${g.label}`)}
              </div>
            )}
            <ul aria-label={g?.label ?? 'Columns'} className={g ? 'pl-3' : undefined}>{items.map(row)}</ul>
          </div>
        ))}
      </div>
    </Popover>
    </TourMark>
  )
}
