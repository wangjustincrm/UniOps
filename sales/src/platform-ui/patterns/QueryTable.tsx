import { useMemo, useState, type ReactNode } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { DataTable, type ColumnDef, type Grouping } from '../primitives/DataTable'
import { groupKey } from '../primitives/groupKey'
import { Button } from '../primitives/shell'
import { useListMeta, type FieldMeta, type ListMeta } from '../resource/listMeta'
import {
  MAX_GROUP_KEYS, clampPageSize, groupBy, sortBy, type Filter, type GroupCount, type QuerySpec,
} from '../resource/querySpec'
import { viewStateOf, type Layout, type ViewState } from '../resource/viewState'
import { errorText } from '../resource/businessError'
import { ColumnChooser } from './ColumnChooser'
import { ColumnMenu } from './ColumnMenu'
import { FilterChips } from './FilterChips'
import { SearchPanel } from './SearchPanel'
import { specFromSorting } from './sorting'

export interface QueryTableProps<T> {
  columns: ColumnDef<T, unknown>[]
  rows: T[]
  total: number
  /** The list response's `groups` / `groups_truncated` (when spec.group is set). */
  groups?: GroupCount[]
  groupsTruncated?: boolean
  /** Server-side paging / sorting / search / filters: the table only edits the spec. */
  spec: QuerySpec
  onSpecChange: (spec: QuerySpec) => void
  /** The resource whose `/meta` drives the column menus, chips and search
   *  panel (a ResourceClient). Without it the table has the page's columns only. */
  resource?: { basePath: string; meta: () => Promise<ListMeta> }
  /** Fields the page filters on itself (not shown in the panel, not removable). */
  locked?: string[]
  /** Column ids the back end accepts as sort keys, beyond what /meta says. */
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
  /** Everything the view is, on every change (for saved views later). */
  onViewChange?: (view: ViewState) => void
}

const columnId = <T,>(c: ColumnDef<T, unknown>): string =>
  c.id ?? ((c as { accessorKey?: string }).accessorKey ?? '')

const headerText = <T,>(c: ColumnDef<T, unknown>, field?: FieldMeta): string =>
  typeof c.header === 'string' ? c.header : field?.label ?? columnId(c)

function formatValue(field: FieldMeta, v: unknown): ReactNode {
  if (v === null || v === undefined || v === '') return ''
  if (field.type === 'bool') return v ? 'Yes' : 'No'
  if (field.type === 'datetime') {
    const d = new Date(String(v))
    return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString()
  }
  return String(v)
}

/** The DataTable view on the back end's list query contract (design §13.19):
 *  column menus (search · sort · hide), chips for what is active, a search
 *  panel over every filterable field, a column chooser, multi-sort, paging
 *  (clamped to MAX_PAGE_SIZE) and errors as their BusinessError text. The
 *  fields come from the resource's /meta; the page's columns keep their own
 *  titles and cells, and every other field can be shown from the chooser.
 *  Every change resets to page 1 except paging itself. */
export function QueryTable<T>(props: QueryTableProps<T>) {
  const { resource, ...rest } = props
  return resource ? <WithMeta {...rest} resource={resource} /> : <QueryTableView {...rest} meta={undefined} />
}

type ViewProps<T> = Omit<QueryTableProps<T>, 'resource'>

function WithMeta<T>({ resource, ...props }: ViewProps<T> & { resource: NonNullable<QueryTableProps<T>['resource']> }) {
  // A failed /meta leaves the plain table (the page's columns, header sort, search).
  return <QueryTableView {...props} meta={useListMeta(resource).data} />
}

function QueryTableView<T>({
  columns, spec, onSpecChange, meta, locked = [], sortable = [], total, groups, groupsTruncated, searchable, error,
  errorMessages, toolbar, onViewChange, ...view
}: ViewProps<T> & { meta: ListMeta | undefined }) {
  const fields = useMemo(() => new Map((meta?.fields ?? []).map((f) => [f.name, f])), [meta])
  const [hiddenOverride, setHiddenOverride] = useState<Record<string, boolean>>({})
  const [panel, setPanel] = useState<{ open: boolean; focus: string | null }>({ open: false, focus: null })

  // The page's columns, then every other field /meta describes (hidden at first).
  const pageIds = useMemo(() => new Set(columns.map(columnId)), [columns])
  const allColumns = useMemo<ColumnDef<T, unknown>[]>(() => [
    ...columns,
    ...(meta?.fields ?? []).filter((f) => !pageIds.has(f.name)).map((f): ColumnDef<T, unknown> => ({
      id: f.name, header: f.label, cell: ({ row }) => formatValue(f, (row.original as Record<string, unknown>)[f.name]),
    })),
  ], [columns, meta, pageIds])
  const isHidden = (id: string) => hiddenOverride[id] ?? !pageIds.has(id)
  const hidden = allColumns.map(columnId).filter(isHidden)
  const layout: Layout = { columns: allColumns.map(columnId), hidden }

  const change = (next: QuerySpec, nextLayout: Layout = layout) => {
    onSpecChange(next)
    onViewChange?.(viewStateOf(next, nextLayout))
  }
  const setHidden = (ids: string[]) => {
    const next = Object.fromEntries(allColumns.map((c) => [columnId(c), ids.includes(columnId(c))]))
    setHiddenOverride(next)
    onViewChange?.(viewStateOf(spec, { ...layout, hidden: ids }))
  }

  const sortableIds = [...sortable, ...(meta?.fields ?? []).filter((f) => f.sortable).map((f) => f.name)]
  const panelFields = (meta?.fields ?? []).filter((f) => f.filterable && !locked.includes(f.name))
  const visible = allColumns.filter((c) => !isHidden(columnId(c)))

  // Counts per group prefix (level 0…n), from the whole-result group list.
  const grouping = useMemo<Grouping<T> | undefined>(() => {
    const levels = spec.group ?? []
    if (levels.length === 0 || !groups) return undefined
    const counts = new Map<string, number>()
    for (const g of groups) {
      for (let i = 0; i < levels.length; i++) {
        const k = groupKey(g.key.slice(0, i + 1))
        counts.set(k, (counts.get(k) ?? 0) + g.count)
      }
    }
    return {
      levels: levels.map((g) => ({ id: g.field, label: fields.get(g.field)?.label ?? g.field })),
      value: (row, id) => (row as Record<string, unknown>)[id],
      counts,
      truncated: groupsTruncated,
      format: (id, v) => {
        const f = fields.get(id)
        if (v === null || v === undefined || v === '') return '(empty)'
        return f ? formatValue(f, v) : String(v)
      },
    }
  }, [spec.group, groups, groupsTruncated, fields])

  return (
    <>
      <DataTable<T>
        {...view}
        columns={visible}
        grouping={grouping}
        errorMessage={error ? errorText(error, errorMessages) : null}
        sort={(spec.sort ?? []).map((s) => ({ id: s.field, desc: !!s.desc }))}
        sortable={sortableIds}
        onSortChange={(s) => change(specFromSorting(spec, [s]))}
        headerAddon={(id) => {
          const f = fields.get(id)
          return f ? (
            <ColumnMenu
              field={f}
              onSearch={() => setPanel({ open: true, focus: id })}
              onSort={(desc) => change(sortBy(spec, id, desc))}
              onGroup={(desc) => change(groupBy(spec, id, desc))}
              canGroup={(spec.group ?? []).length < MAX_GROUP_KEYS || (spec.group ?? []).some((g) => g.field === id)}
              onHide={() => setHidden([...hidden, id])}
            />
          ) : null
        }}
        leadingHeader={meta ? (
          <ColumnChooser
            columns={allColumns.map((c) => ({ id: columnId(c), label: headerText(c, fields.get(columnId(c))) }))}
            hidden={hidden}
            onChange={setHidden}
          />
        ) : undefined}
        chips={meta ? (
          <FilterChips spec={spec} onSpecChange={change} fields={fields} locked={locked}
            labelOf={(f) => { const c = columns.find((x) => columnId(x) === f); return c ? headerText(c) : f }} />
        ) : undefined}
        search={searchable ? {
          value: spec.search ?? '',
          onSubmit: (v) => change({ ...spec, page: 1, search: v || undefined }),
        } : undefined}
        toolbar={(panelFields.length > 0 || toolbar) ? (
          <>
            {panelFields.length > 0 && (
              <Button variant="secondary" size="sm" onClick={() => setPanel({ open: true, focus: null })}>
                <SlidersHorizontal className="mr-1 h-3.5 w-3.5" />Search
              </Button>
            )}
            {toolbar}
          </>
        ) : undefined}
        pagination={{
          page: spec.page,
          pageSize: clampPageSize(spec.pageSize),
          total,
          onPageChange: (page) => onSpecChange({ ...spec, page }),
          onPageSizeChange: (size) => change({ ...spec, page: 1, pageSize: clampPageSize(size) }),
        }}
      />
      <SearchPanel
        open={panel.open}
        onOpenChange={(open) => setPanel((p) => ({ ...p, open }))}
        fields={panelFields}
        filters={(spec.filters ?? []).filter((f) => !locked.includes(f.field))}
        focusField={panel.focus}
        onApply={(next: Filter[]) => {
          const keep = (spec.filters ?? []).filter((f) => locked.includes(f.field) || !panelFields.some((p) => p.name === f.field))
          change({ ...spec, page: 1, filters: [...keep, ...next] })
        }}
      />
    </>
  )
}
