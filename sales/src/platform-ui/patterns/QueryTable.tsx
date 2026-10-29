import type { ReactNode } from 'react'
import { DataTable, type ColumnDef } from '../primitives/DataTable'
import { Input } from '../primitives/shell'
import { useSynced } from '../primitives/useSynced'
import { clampPageSize, getFilter, setFilter, type FilterOp, type QuerySpec } from '../resource/querySpec'
import { errorText } from '../resource/businessError'
import { specFromSorting } from './sorting'

/** A filter shown above the table: a select when `options` are given (op
 *  `eq`), otherwise a text box (op `contains`, applied on Enter). */
export interface FilterControl {
  field: string
  label: string
  options?: { value: string; label: string }[]
  op?: FilterOp
}

export interface QueryTableProps<T> {
  columns: ColumnDef<T, unknown>[]
  rows: T[]
  total: number
  /** Server-side paging / sorting / search / filters: the table only edits the spec. */
  spec: QuerySpec
  onSpecChange: (spec: QuerySpec) => void
  /** Column ids the back end accepts as sort keys. Others are not sortable. */
  sortable?: string[]
  searchable?: boolean
  loading?: boolean
  error?: unknown
  errorMessages?: Record<string, string>
  onRetry?: () => void
  rowKey: (row: T) => string | number
  onRowClick?: (row: T) => void
  emptyTitle?: string
  toolbar?: ReactNode
  filters?: FilterControl[]
}

/** The DataTable view adapted to the back end's list contract: sort ↔
 *  spec.sort (one key), paging ↔ spec.page / pageSize (clamped to
 *  MAX_PAGE_SIZE), search ↔ spec.search, filter controls ↔ spec.filters,
 *  errors ↔ their BusinessError text. Every change resets to page 1 except
 *  paging itself. */
export function QueryTable<T>({
  spec, onSpecChange, total, searchable, error, errorMessages, filters = [], ...view
}: QueryTableProps<T>) {
  const first = spec.sort?.[0]
  return (
    <DataTable<T>
      {...view}
      errorMessage={error ? errorText(error, errorMessages) : null}
      sort={first ? { id: first.field, desc: !!first.desc } : null}
      onSortChange={(s) => onSpecChange(specFromSorting(spec, [s]))}
      search={searchable ? {
        value: spec.search ?? '',
        onSubmit: (v) => onSpecChange({ ...spec, page: 1, search: v || undefined }),
      } : undefined}
      filters={filters.length > 0
        ? filters.map((fc) => <FilterInput key={fc.field} control={fc} spec={spec} onSpecChange={onSpecChange} />)
        : undefined}
      pagination={{
        page: spec.page,
        pageSize: clampPageSize(spec.pageSize),
        total,
        onPageChange: (page) => onSpecChange({ ...spec, page }),
        onPageSizeChange: (size) => onSpecChange({ ...spec, page: 1, pageSize: clampPageSize(size) }),
      }}
    />
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
