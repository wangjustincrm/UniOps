import { useMemo, type ReactNode } from 'react'
import {
  flexRender, getCoreRowModel, useReactTable,
  type ColumnDef, type SortingState, type Updater,
} from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'
import { Input, Pagination, SkeletonRow } from '@uniops/shell'
import type { QuerySpec } from '../lib/querySpec'
import { cn } from '../lib/cn'
import { EmptyState, ErrorState } from '../states/States'
import { specFromSorting } from './sorting'

export type { ColumnDef } from '@tanstack/react-table'

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
}

/** The back-end adapter: TanStack sorting ↔ spec.sort (one key), paging ↔
 *  spec.page / pageSize, search box ↔ spec.search. Every change resets to page 1
 *  except paging itself. */
export function DataTable<T>({
  columns, rows, total, spec, onSpecChange, sortable = [], searchable, loading, error, onRetry,
  rowKey, onRowClick, emptyTitle = 'Nothing here yet', toolbar,
}: DataTableProps<T>) {
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
      {(searchable || toolbar) && (
        <div className="flex flex-wrap items-center gap-2">
          {searchable && (
            <Input
              aria-label="Search"
              placeholder="Search…"
              defaultValue={spec.search ?? ''}
              className="max-w-xs"
              onKeyDown={(e) => {
                if (e.key === 'Enter') onSpecChange({ ...spec, page: 1, search: e.currentTarget.value })
              }}
            />
          )}
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
          pageSize={spec.pageSize}
          total={total}
          onPageChange={(page) => onSpecChange({ ...spec, page })}
          onPageSizeChange={(pageSize) => onSpecChange({ ...spec, page: 1, pageSize })}
        />
      )}
    </div>
  )
}
