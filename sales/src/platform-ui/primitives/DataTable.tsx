import { useMemo, type ReactNode } from 'react'
import {
  flexRender, getCoreRowModel, useReactTable,
  type ColumnDef, type SortingState, type Updater,
} from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'
import { Input, Pagination, SkeletonRow } from './shell'
import { cn } from '../lib/cn'
import { EmptyState, ErrorState } from './States'
import { useSynced } from './useSynced'

export type { ColumnDef } from '@tanstack/react-table'

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
  /** One sort key at a time; only the `sortable` column ids get a header button. */
  sort?: SortValue | null
  sortable?: string[]
  onSortChange?: (sort: SortValue) => void
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
  sort, sortable = [], onSortChange, search, filters, toolbar, pagination,
}: DataTableProps<T>) {
  const [searchDraft, setSearchDraft] = useSynced(search?.value ?? '')
  const sorting: SortingState = useMemo(() => (sort ? [sort] : []), [sort])
  const sortableSet = useMemo(() => new Set(sortable), [sortable])

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    manualSorting: true,
    manualPagination: true,
    enableMultiSort: false,
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
      {(search || toolbar || filters) && (
        <div className="flex flex-wrap items-center gap-2">
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

      {errorMessage ? (
        <ErrorState message={errorMessage} onRetry={onRetry} />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((h) => {
                    const canSort = sortableSet.has(h.column.id) && !!onSortChange
                    const dir = h.column.getIsSorted()
                    return (
                      <th key={h.id} className="px-3 py-2 font-medium" aria-sort={dir ? (dir === 'desc' ? 'descending' : 'ascending') : undefined}>
                        {h.isPlaceholder ? null : canSort ? (
                          <button
                            type="button"
                            className="inline-flex items-center gap-1 uppercase hover:text-neutral-800"
                            onClick={() => onSortChange?.({ id: h.column.id, desc: dir === 'asc' })}
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
