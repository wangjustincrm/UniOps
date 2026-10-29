import { useMemo, useState, type ReactNode } from 'react'
import {
  flexRender, getCoreRowModel, useReactTable,
  type ColumnDef, type SortingState, type Updater,
} from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'
import { Input, Pagination, SkeletonRow } from '@uniops/shell'
import { clampPageSize, getFilter, setFilter, type FilterOp, type QuerySpec } from '../lib/querySpec'
import { cn } from '../lib/cn'
import { EmptyState, ErrorState } from '../states/States'
import { specFromSorting } from './sorting'

export type { ColumnDef } from '@tanstack/react-table'

/** A filter shown above the table: a select when `options` are given (op
 *  `eq`), otherwise a text box (op `contains`, applied on Enter). */
export interface FilterControl {
  field: string
  label: string
  options?: { value: string; label: string }[]
  op?: FilterOp
}

export interface DataTableProps<T> {
  columns: ColumnDef<T, unknown>[]
  rows: T[]
  total: number
  /** Server-side paging / sorting / search: the table only edits the spec. */
  spec: QuerySpec
  onSpecChange: (spec: QuerySpec) => void
  /** Column ids the back end accepts as sort keys. Others are not sortable. */
  sortable?: string[]
  searchable?: boolean
  loading?: boolean
  error?: unknown
  onRetry?: () => void
  rowKey: (row: T) => string | number
  onRowClick?: (row: T) => void
  emptyTitle?: string
  toolbar?: ReactNode
  filters?: FilterControl[]
}

/** The back-end adapter: TanStack sorting ↔ spec.sort (one key), paging ↔
 *  spec.page / pageSize (clamped to MAX_PAGE_SIZE), search box ↔ spec.search,
 *  filter controls ↔ spec.filters. Every change resets to page 1 except paging
 *  itself. The search box is controlled: it shows what the spec says (also
 *  when the spec changes from outside) and applies on Enter. */
export function DataTable<T>({
  columns, rows, total, spec, onSpecChange, sortable = [], searchable, loading, error, onRetry,
  rowKey, onRowClick, emptyTitle = 'Nothing here yet', toolbar, filters = [],
}: DataTableProps<T>) {
  const [searchDraft, setSearchDraft] = useSynced(spec.search ?? '')
  const pageSize = clampPageSize(spec.pageSize)
  const sorting: SortingState = useMemo(
    () => (spec.sort ?? []).map((s) => ({ id: s.field, desc: !!s.desc })),
    [spec.sort],
  )
  const sortableSet = useMemo(() => new Set(sortable), [sortable])

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    manualSorting: true,
    manualPagination: true,
    enableMultiSort: false,
    rowCount: total,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => String(rowKey(row)),
    onSortingChange: (u: Updater<SortingState>) => {
      const next = typeof u === 'function' ? u(sorting) : u
      onSpecChange(specFromSorting(spec, next))
    },
  })

  return (
    <div className="flex flex-col gap-3">
      {(searchable || toolbar || filters.length > 0) && (
        <div className="flex flex-wrap items-center gap-2">
          {searchable && (
            <Input
              aria-label="Search"
              placeholder="Search…"
              value={searchDraft}
              className="max-w-xs"
              onChange={(e) => setSearchDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onSpecChange({ ...spec, page: 1, search: searchDraft.trim() || undefined })
              }}
            />
          )}
          {filters.map((fc) => (
            <FilterInput key={fc.field} control={fc} spec={spec} onSpecChange={onSpecChange} />
          ))}
          <div className="ml-auto flex gap-2">{toolbar}</div>
        </div>
      )}

      {error ? (
        <ErrorState error={error} onRetry={onRetry} />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((h) => {
                    const canSort = sortableSet.has(h.column.id)
                    const dir = h.column.getIsSorted()
                    return (
                      <th key={h.id} className="px-3 py-2 font-medium" aria-sort={dir ? (dir === 'desc' ? 'descending' : 'ascending') : undefined}>
                        {h.isPlaceholder ? null : canSort ? (
                          <button
                            type="button"
                            className="inline-flex items-center gap-1 uppercase hover:text-neutral-800"
                            onClick={() => onSpecChange(specFromSorting(spec, [{ id: h.column.id, desc: dir === 'asc' }]))}
                          >
                            {flexRender(h.column.columnDef.header, h.getContext())}
                            {dir === 'asc' ? <ArrowUp className="h-3 w-3" /> : dir === 'desc' ? <ArrowDown className="h-3 w-3" /> : <ArrowUpDown className="h-3 w-3 opacity-40" />}
                          </button>
                        ) : (
                          flexRender(h.column.columnDef.header, h.getContext())
                        )}
                      </th>
                    )
                  })}
                </tr>
              ))}
            </thead>
            <tbody>
              {loading && rows.length === 0
                ? Array.from({ length: 5 }, (_, i) => <SkeletonRow key={i} cols={columns.length} />)
                : table.getRowModel().rows.map((r) => (
                  <tr
                    key={r.id}
                    onClick={onRowClick ? () => onRowClick(r.original) : undefined}
                    className={cn('border-t border-neutral-100', onRowClick && 'cursor-pointer hover:bg-neutral-50')}
                  >
                    {r.getVisibleCells().map((c) => (
                      <td key={c.id} className="px-3 py-2 text-neutral-700">
                        {flexRender(c.column.columnDef.cell, c.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
            </tbody>
          </table>
          {!loading && rows.length === 0 && <EmptyState title={emptyTitle} className="border-0" />}
        </div>
      )}

      {total > 0 && (
        <Pagination
          page={spec.page}
          pageSize={pageSize}
          total={total}
          onPageChange={(page) => onSpecChange({ ...spec, page })}
          onPageSizeChange={(size) => onSpecChange({ ...spec, page: 1, pageSize: clampPageSize(size) })}
        />
      )}
    </div>
  )
}

function FilterInput({ control, spec, onSpecChange }: {
  control: FilterControl
  spec: QuerySpec
  onSpecChange: (spec: QuerySpec) => void
}) {
  const current = getFilter(spec, control.field)
  const value = typeof current?.value === 'string' ? current.value : ''
  const [draft, setDraft] = useSynced(value)
  if (control.options) {
    return (
      <select
        aria-label={control.label}
        value={value}
        className="h-10 rounded-lg border border-neutral-200 bg-white px-3 text-sm"
        onChange={(e) => onSpecChange(setFilter(spec, control.field, control.op ?? 'eq', e.target.value))}
      >
        <option value="">{control.label}: all</option>
        {control.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    )
  }
  return (
    <Input
      aria-label={control.label}
      placeholder={control.label}
      value={draft}
      className="max-w-[12rem]"
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onSpecChange(setFilter(spec, control.field, control.op ?? 'contains', draft.trim()))
      }}
    />
  )
}

/** A draft that follows `value` whenever `value` changes from outside (React's
 *  "adjust state while rendering" pattern — no effect, no extra render pass). */
function useSynced(value: string): [string, (v: string) => void] {
  const [draft, setDraft] = useState(value)
  const [seen, setSeen] = useState(value)
  if (seen !== value) {
    setSeen(value)
    setDraft(value)
  }
  return [draft, setDraft]
}
