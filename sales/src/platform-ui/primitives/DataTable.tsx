import { Fragment, useMemo, useState, type ReactNode } from 'react'
import {
  flexRender, getCoreRowModel, useReactTable,
  type ColumnDef, type SortingState, type Updater,
} from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronRight, GripVertical } from 'lucide-react'
import { Input, Pagination, SkeletonRow } from './shell'
import { cn } from '../lib/cn'
import { EmptyState, ErrorState } from './States'
import { useSynced } from './useSynced'
import { groupKey } from './groupKey'

export type { ColumnDef } from '@tanstack/react-table'

/** Rows arrive ordered by the group fields; this says which they are and how
 *  many rows each group has in the whole result (not just this page). */
export interface Grouping<T> {
  /** One per level, outermost first. */
  levels: { id: string; label: string }[]
  value: (row: T, id: string) => unknown
  /** Count per group: key = the values of levels 0…n as JSON (see groupKey). */
  counts: Map<string, number>
  format?: (id: string, value: unknown) => ReactNode
  /** More groups exist than the counts cover. */
  truncated?: boolean
}

export interface SortValue {
  id: string
  desc: boolean
}

export interface DataTableProps<T> {
  columns: ColumnDef<T, unknown>[]
  rows: T[]
  rowKey: (row: T) => string | number
  onRowClick?: (row: T) => void
  loading?: boolean
  /** Shown instead of the table when set. */
  errorMessage?: string | null
  onRetry?: () => void
  emptyTitle?: string
  /** The sort keys in priority order (one or several); only the `sortable`
   *  column ids get a header button, which sorts by that column alone. */
  sort?: SortValue | SortValue[] | null
  sortable?: string[]
  onSortChange?: (sort: SortValue) => void
  /** Rendered after a column's title (e.g. its menu). */
  headerAddon?: (columnId: string) => ReactNode
  /** A narrow first header cell (e.g. the column chooser); its body cells stay empty. */
  leadingHeader?: ReactNode
  /** A row between the toolbar and the table (e.g. the active sort / filter chips). */
  chips?: ReactNode
  /** Nested, collapsible group header rows with their counts. */
  grouping?: Grouping<T>
  /** First in the toolbar row (e.g. the view picker). */
  leading?: ReactNode
  /** Columns can be dragged to a new place: `id` goes before `beforeId` (null = last).
   *  The keyboard way is the column chooser's ↑ ↓. */
  onColumnMove?: (id: string, beforeId: string | null) => void
  /** A controlled search box, applied on Enter. */
  search?: { value: string; onSubmit: (value: string) => void }
  /** Controls placed after the search box (e.g. filters), and actions on the right. */
  filters?: ReactNode
  toolbar?: ReactNode
  pagination?: {
    page: number
    pageSize: number
    total: number
    onPageChange: (page: number) => void
    onPageSizeChange: (size: number) => void
  }
}

/** The table view: rows, a sortable header, a search box, paging and the
 *  empty / loading / error states. It knows nothing about the API or the
 *  query contract — QueryTable (patterns) adapts it to a QuerySpec. */
export function DataTable<T>({
  columns, rows, rowKey, onRowClick, loading, errorMessage, onRetry, emptyTitle = 'Nothing here yet',
  sort, sortable = [], onSortChange, headerAddon, leadingHeader, chips, grouping, leading, onColumnMove, search, filters,
  toolbar, pagination,
}: DataTableProps<T>) {
  const [dragging, setDragging] = useState<string | null>(null)
  const [dropOn, setDropOn] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const toggle = (k: string) => setCollapsed((s) => {
    const next = new Set(s)
    if (next.has(k)) next.delete(k)
    else next.add(k)
    return next
  })
  const [searchDraft, setSearchDraft] = useSynced(search?.value ?? '')
  const sorting: SortingState = useMemo(() => (Array.isArray(sort) ? sort : sort ? [sort] : []), [sort])
  const sortableSet = useMemo(() => new Set(sortable), [sortable])

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    manualSorting: true,
    manualPagination: true,
    enableMultiSort: true,
    rowCount: pagination?.total ?? rows.length,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => String(rowKey(row)),
    onSortingChange: (u: Updater<SortingState>) => {
      const next = typeof u === 'function' ? u(sorting) : u
      if (next[0]) onSortChange?.({ id: next[0].id, desc: next[0].desc })
    },
  })

  return (
    <div className="flex flex-col gap-3">
      {(search || toolbar || filters || leading) && (
        <div className="flex flex-wrap items-center gap-2">
          {leading}
          {search && (
            <Input
              aria-label="Search"
              placeholder="Search…"
              value={searchDraft}
              className="max-w-xs"
              onChange={(e) => setSearchDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') search.onSubmit(searchDraft.trim())
              }}
            />
          )}
          {filters}
          <div className="ml-auto flex gap-2">{toolbar}</div>
        </div>
      )}

      {chips}

      {errorMessage ? (
        <ErrorState message={errorMessage} onRetry={onRetry} />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {leadingHeader !== undefined && <th className="w-8 px-2 py-2">{leadingHeader}</th>}
                  {hg.headers.map((h) => {
                    const canSort = sortableSet.has(h.column.id) && !!onSortChange
                    const dir = h.column.getIsSorted()
                    const priority = sorting.length > 1 ? sorting.findIndex((s) => s.id === h.column.id) + 1 : 0
                    return (
                      <th
                        key={h.id}
                        className={cn('px-3 py-2 font-medium',
                          dragging === h.column.id && 'opacity-40', dropOn === h.column.id && dragging !== h.column.id && 'shadow-[inset_2px_0_0_0_var(--color-primary-600,#0a7c7c)]')}
                        aria-sort={dir ? (dir === 'desc' ? 'descending' : 'ascending') : undefined}
                        onDragOver={onColumnMove ? (e) => { if (dragging) { e.preventDefault(); setDropOn(h.column.id) } } : undefined}
                        onDragLeave={onColumnMove ? () => setDropOn((d) => (d === h.column.id ? null : d)) : undefined}
                        onDrop={onColumnMove ? (e) => {
                          e.preventDefault()
                          const id = e.dataTransfer.getData('text/x-column') || dragging
                          if (id && id !== h.column.id) onColumnMove(id, h.column.id)
                          setDragging(null); setDropOn(null)
                        } : undefined}
                        onDragEnd={onColumnMove ? () => { setDragging(null); setDropOn(null) } : undefined}
                      >
                        <span className="inline-flex items-center gap-1">
                          {onColumnMove && (
                            // The grip is what is dragged (a press on the sort button never starts a drag);
                            // the keyboard way is the column chooser's ↑ ↓.
                            <span
                              draggable
                              aria-hidden
                              data-column-grip={h.column.id}
                              title="Drag to move the column"
                              className="-ml-1 cursor-grab text-neutral-300 hover:text-neutral-500 active:cursor-grabbing"
                              onDragStart={(e) => {
                                e.dataTransfer.setData('text/x-column', h.column.id)
                                e.dataTransfer.effectAllowed = 'move'
                                setDragging(h.column.id)
                              }}
                            >
                              <GripVertical className="h-3 w-3" />
                            </span>
                          )}
                          {h.isPlaceholder ? null : canSort ? (
                            <button
                              type="button"
                              className="inline-flex items-center gap-1 uppercase hover:text-neutral-800"
                              onClick={() => onSortChange?.({ id: h.column.id, desc: dir === 'asc' })}
                            >
                              {flexRender(h.column.columnDef.header, h.getContext())}
                              {dir === 'asc' ? <ArrowUp className="h-3 w-3" /> : dir === 'desc' ? <ArrowDown className="h-3 w-3" /> : <ArrowUpDown className="h-3 w-3 opacity-40" />}
                              {priority > 0 && <sup className="text-[10px] text-neutral-400">{priority}</sup>}
                            </button>
                          ) : (
                            flexRender(h.column.columnDef.header, h.getContext())
                          )}
                          {headerAddon?.(h.column.id)}
                        </span>
                      </th>
                    )
                  })}
                </tr>
              ))}
            </thead>
            <tbody>
              {loading && rows.length === 0
                ? Array.from({ length: 5 }, (_, i) => <SkeletonRow key={i} cols={columns.length + (leadingHeader !== undefined ? 1 : 0)} />)
                : table.getRowModel().rows.map((r, i, all) => {
                  const row = (
                    <tr
                      key={r.id}
                      onClick={onRowClick ? () => onRowClick(r.original) : undefined}
                      className={cn('border-t border-neutral-100', onRowClick && 'cursor-pointer hover:bg-neutral-50')}
                    >
                      {leadingHeader !== undefined && <td />}
                      {r.getVisibleCells().map((c) => (
                        <td key={c.id} className="px-3 py-2 text-neutral-700">
                          {flexRender(c.column.columnDef.cell, c.getContext())}
                        </td>
                      ))}
                    </tr>
                  )
                  if (!grouping) return row
                  // A header for each level whose value changes at this row;
                  // rows (and deeper headers) under a collapsed group are left out.
                  const values = grouping.levels.map((l) => grouping.value(r.original, l.id))
                  const prev = i > 0 ? grouping.levels.map((l) => grouping.value(all[i - 1].original, l.id)) : null
                  const first = prev === null ? 0 : values.findIndex((v, j) => String(v ?? null) !== String(prev[j] ?? null))
                  const span = columns.length + (leadingHeader !== undefined ? 1 : 0)
                  const headers: ReactNode[] = []
                  if (first !== -1) {
                    for (let level = first; level < values.length; level++) {
                      const key = groupKey(values.slice(0, level + 1))
                      const hiddenByParent = values.slice(0, level).some((_, j) => collapsed.has(groupKey(values.slice(0, j + 1))))
                      if (hiddenByParent) break
                      const count = grouping.counts.get(key)
                      const open = !collapsed.has(key)
                      const { id, label } = grouping.levels[level]
                      headers.push(
                        <tr key={`g:${key}`} className="border-t border-neutral-200 bg-neutral-50/70">
                          <td colSpan={span} className="py-1.5 pr-3" style={{ paddingLeft: `${0.75 + level * 1.25}rem` }}>
                            <button type="button" aria-expanded={open} onClick={() => toggle(key)}
                              className="inline-flex items-center gap-1.5 text-sm font-medium text-neutral-800 hover:text-neutral-950">
                              {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                              <span className="text-xs uppercase tracking-wide text-neutral-500">{label}:</span>
                              {grouping.format ? grouping.format(id, values[level]) : String(values[level] ?? '(empty)')}
                              {count !== undefined && <span className="rounded-full bg-neutral-200 px-1.5 text-xs text-neutral-600">{count}</span>}
                            </button>
                          </td>
                        </tr>,
                      )
                    }
                  }
                  const rowHidden = values.some((_, j) => collapsed.has(groupKey(values.slice(0, j + 1))))
                  return <Fragment key={r.id}>{headers}{!rowHidden && row}</Fragment>
                })}
            </tbody>
          </table>
          {!loading && rows.length === 0 && <EmptyState title={emptyTitle} className="border-0" />}
          {grouping?.truncated && (
            <p className="border-t border-neutral-100 px-3 py-2 text-xs text-neutral-500">
              Too many groups to count them all: counts are shown for the first 500.
            </p>
          )}
        </div>
      )}

      {pagination && pagination.total > 0 && (
        <Pagination
          page={pagination.page}
          pageSize={pagination.pageSize}
          total={pagination.total}
          onPageChange={pagination.onPageChange}
          onPageSizeChange={pagination.onPageSizeChange}
        />
      )}
    </div>
  )
}
