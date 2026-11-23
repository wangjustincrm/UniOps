import { useState } from 'react'
import type { ColumnDef } from '../primitives/DataTable'
import { Checkbox } from '../primitives/Checkbox'
import { toSearchParams, type QuerySpec } from '../resource/querySpec'
import { RowActionMenu } from './RecordActions'
import { SelectionBar } from './SelectionBar'
import { useActionsFor, type ActionHost, type RecordAction } from './recordActions'

const MAX_SELECT_ALL = 500  // "Select all N matching": the server's MAX_RECORDS

type Row = { id: number; version: number }

/** Row selection for a list's group actions (user 2026-11-23). Only when the person holds a group action:
 *  otherwise no checkbox column and no bar. Cleared when the filter, the search, the view or the grouping changes and
 *  after an action ran; kept across pages, sorting and refreshes (by id; the row as the list now has it). */
export function useRowSelection<T extends Row>({ actions, rows, spec, total, host }: {
  actions?: RecordAction<T>[]; rows: T[]; spec: QuerySpec; total: number; host: ActionHost<T>
}) {
  const bulk = useActionsFor(actions, 'bulk')
  const rowMenu = useActionsFor(actions, 'row')
  // The selection belongs to the filter, search and grouping it was made in: another one starts empty.
  const scope = JSON.stringify([spec.filters, spec.search, spec.group])
  const [state, setState] = useState<{ scope: string; picked: Map<number, T>; all: boolean }>({ scope, picked: new Map(), all: false })
  const live = state.scope === scope ? state : { scope, picked: new Map<number, T>(), all: false }
  const { picked, all: allMatching } = live
  const clear = () => setState({ scope, picked: new Map(), all: false })
  const setAllMatching = (all: boolean) => setState({ ...live, all })
  const setPicked = (f: (m: Map<number, T>) => Map<number, T>) => setState({ scope, picked: f(live.picked), all: false })
  const current = new Map(rows.map((r) => [r.id, r]))
  const selected = [...picked.values()].map((r) => current.get(r.id) ?? r)
  const onPage = rows.filter((r) => picked.has(r.id)).length
  const toggle = (list: T[], on: boolean) => {
    setPicked((m) => { const n = new Map(m); for (const r of list) { if (on) n.set(r.id, r); else n.delete(r.id) } return n })
  }
  const columns: ColumnDef<T, unknown>[] = []
  if (bulk.length) {
    columns.push({
      id: '__select', enableSorting: false, enableHiding: false, meta: { card: 'select' },
      header: () => (
        <Checkbox aria-label="Select all on this page" checked={onPage === 0 ? false : onPage === rows.length ? true : 'indeterminate'}
          onCheckedChange={(on) => toggle(rows, on)} />
      ),
      cell: ({ row }) => (
        <Checkbox aria-label={`Select ${host.name(row.original)}`} checked={picked.has(row.original.id)}
          onCheckedChange={(on) => toggle([row.original], on)} />
      ),
    } as ColumnDef<T, unknown>)
  }
  const menuColumn: ColumnDef<T, unknown> | null = rowMenu.length && actions ? {
    id: '__actions', enableSorting: false, enableHiding: false, header: () => <span className="sr-only">Actions</span>,
    meta: { card: 'badge' }, cell: ({ row }) => <RowActionMenu row={row.original} actions={actions} />,
  } as ColumnDef<T, unknown> : null
  const count = allMatching ? total : picked.size
  const bar = count > 0 ? (
    <SelectionBar<T> count={count} actions={bulk} rows={selected} host={host} onClear={clear}
      target={allMatching ? { query: queryOf(spec), expected: total } : { rows: selected }}
      selectAll={!allMatching && onPage === rows.length && total > rows.length && total <= MAX_SELECT_ALL
        ? { total, run: () => setAllMatching(true) } : undefined} />
  ) : null
  return { lead: columns, menuColumn, bar, clear, active: count > 0 }
}

function queryOf(spec: QuerySpec): string {
  const p = toSearchParams(spec)
  p.delete('page'); p.delete('page_size'); p.delete('group')
  return p.toString()
}
