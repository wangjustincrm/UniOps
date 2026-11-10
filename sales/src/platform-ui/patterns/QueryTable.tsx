import { BooleanCell, DateCell, NumberCell, StackedDateTimeCell } from '../primitives/cells'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { DataTable, type ColumnDef, type Grouping } from '../primitives/DataTable'
import { groupKey } from '../primitives/groupKey'
import { formatMoney } from '../primitives/format'
import { columnMismatch, useListMeta, type FieldMeta, type ListMeta } from '../resource/listMeta'
import {
  MAX_GROUP_KEYS, clampPageSize, groupBy, sortBy, type Filter, type GroupCount, type ListTotal, type QuerySpec,
} from '../resource/querySpec'
import { viewStateOf, type Layout, type ViewState } from '../resource/viewState'
import { errorText } from '../resource/businessError'
import { ColumnChooser } from '../primitives/ColumnChooser'
import { ViewActions, ViewPicker } from './ViewPicker'
import type { ViewPickerModel } from './views'
import { ColumnMenu } from './ColumnMenu'
import { FilterChips } from './FilterChips'
import { SearchPanel } from './SearchPanel'
import { specFromSorting } from './sorting'

export interface QueryTableProps<T> {
  /** The table's accessible name. */
  ariaLabel?: string
  columns: ColumnDef<T, unknown>[]
  /** Page columns shown only where a view names them (a channel's own columns), like the extra fields. */
  defaultHidden?: string[]
  rows: T[]
  total: number
  /** The list response's `groups` / `groups_truncated` (when spec.group is set). */
  groups?: GroupCount[]
  groupsTruncated?: boolean
  /** The list response's `totals`: a footer row under the visible summable
   *  columns, and each group's totals in its header. */
  totals?: ListTotal[]
  /** Server-side paging / sorting / search / filters: the table only edits the spec. */
  spec: QuerySpec
  onSpecChange: (spec: QuerySpec) => void
  /** The resource whose `/meta` drives the column menus, chips and search
   *  panel (a ResourceClient). Without it the table has the page's columns only. */
  resource?: { basePath: string; meta: () => Promise<ListMeta>; suggest?: (field: string, query: string) => Promise<string[]> }
  /** Fields the page filters on itself (not shown in the panel, not removable). */
  locked?: string[]
  /** Column ids the back end accepts as sort keys, beyond what /meta says. */
  sortable?: string[]
  searchable?: boolean
  loading?: boolean
  /** Fetching again over rows shown, and whether those rows are the previous query's (DataTable). */
  fetching?: boolean
  stale?: boolean
  /** The card's height (DataTable `fit`) and its status row (TableStatusBar, with the pages). */
  fit?: 'viewport' | number
  statusBar?: (pagination: ReactNode) => ReactNode
  /** A user's Refresh: the bar at once. */
  busyNow?: boolean
  /** Rows a refresh brought or changed (by rowKey): highlighted briefly. */
  highlight?: ReadonlySet<string | number>
  error?: unknown
  errorMessages?: Record<string, string>
  onRetry?: () => void
  rowKey: (row: T) => string | number
  onRowClick?: (row: T) => void
  emptyTitle?: string
  toolbar?: ReactNode
  /** Everything the view is, on every change. */
  onViewChange?: (view: ViewState) => void
  /** Controlled layout (column order and visibility), e.g. from useListView. */
  layout?: Layout
  onLayoutChange?: (layout: Layout) => void
  /** The list's saved views (useListView(...).picker): shown left of the toolbar. */
  picker?: ViewPickerModel
}

const columnId = <T,>(c: ColumnDef<T, unknown>): string =>
  c.id ?? ((c as { accessorKey?: string }).accessorKey ?? '')

const headerText = <T,>(c: ColumnDef<T, unknown>, field?: FieldMeta): string =>
  typeof c.header === 'string' ? c.header : field?.label ?? columnId(c)

function formatValue(field: FieldMeta, v: unknown): ReactNode {
  if (v === null || v === undefined || v === '') return ''
  // A field added from the chooser looks like the same kind of value in any list.
  if (field.type === 'bool') return <BooleanCell value={Boolean(v)} />
  if (field.type === 'datetime') return <StackedDateTimeCell value={String(v)} />
  if (field.type === 'date') return <DateCell value={String(v)} />
  if (field.type === 'number') return <NumberCell value={v as string | number} />
  return String(v)
}

/** Totals in several lists of per-currency sums, added per currency. */
function mergeTotals(lists: ListTotal[][]): ListTotal[] {
  const by = new Map<string, ListTotal>()
  for (const t of lists.flat()) {
    const k = t.currency ?? ''
    const cur = by.get(k) ?? { currency: t.currency, count: 0, sums: {} }
    cur.count += t.count
    for (const [f, v] of Object.entries(t.sums)) cur.sums[f] = String(Number(cur.sums[f] ?? 0) + Number(v))
    by.set(k, cur)
  }
  return [...by.values()]
}

/** A sum: money in its currency; without a currency, a plain number (a quantity). */
const formatSum = (v: string, currency: string | null) =>
  currency ? formatMoney(v, currency) : Number(v).toLocaleString('en-CA', { maximumFractionDigits: 4 })

/** One field's sum in every currency: "35,988.00 CAD · 120.00 USD". */
const sumText = (totals: ListTotal[], field: string): string =>
  totals.filter((t) => field in t.sums).map((t) => formatSum(t.sums[field], t.currency)).join(' · ')

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
  return <QueryTableView {...props} meta={useListMeta(resource).data} suggest={resource.suggest} />
}

function QueryTableView<T>({
  columns, defaultHidden = [], spec, onSpecChange, meta, locked = [], sortable = [], total, groups, groupsTruncated, totals, searchable, error,
  errorMessages, toolbar, onViewChange, layout: layoutProp, onLayoutChange, picker, suggest, ...view
}: ViewProps<T> & { meta: ListMeta | undefined; suggest?: (field: string, query: string) => Promise<string[]> }) {
  const fields = useMemo(() => new Map((meta?.fields ?? []).map((f) => [f.name, f])), [meta])
  const [ownLayout, setOwnLayout] = useState<Layout>({ columns: [], hidden: [] })
  const layout = layoutProp ?? ownLayout
  const [panel, setPanel] = useState<{ open: boolean; focus: string | null }>({ open: false, focus: null })

  // The page's columns, then every other field /meta describes (hidden at first).
  const pageIds = useMemo(() => new Set(columns.map(columnId)), [columns])
  // In development: say when the page and its server's views disagree on a column id (a typo there
  // used to do nothing at all). Tests assert columnMismatch directly.
  useEffect(() => {
    if (!import.meta.env.DEV || !meta) return
    for (const m of columnMismatch(meta, [...pageIds])) console.warn(`[QueryTable ${view.ariaLabel ?? ''}] ${m}`)
  }, [meta, pageIds])  // eslint-disable-line react-hooks/exhaustive-deps
  const allColumns = useMemo<ColumnDef<T, unknown>[]>(() => [
    ...columns,
    ...(meta?.fields ?? []).filter((f) => !pageIds.has(f.name)).map((f): ColumnDef<T, unknown> => ({
      id: f.name, header: f.label, cell: ({ row }) => formatValue(f, (row.original as Record<string, unknown>)[f.name]),
    })),
  ], [columns, meta, pageIds])
  // Layout (resource/viewState.ts): no `columns` = the page's order, its own
  // columns shown, the extra ones hidden; otherwise `columns` is the whole
  // order, and a column it does not name (added later) shows if it is the page's.
  const allIds = allColumns.map(columnId)
  const order = layout.columns.length
    ? [...layout.columns.filter((id) => allIds.includes(id)), ...allIds.filter((id) => !layout.columns.includes(id))]
    : allIds
  const shownByDefault = (id: string) => pageIds.has(id) && !defaultHidden.includes(id)
  const isHidden = (id: string) =>
    layout.hidden.includes(id) || (!shownByDefault(id) && !(layout.columns.length > 0 && layout.columns.includes(id)))
  const hidden = order.filter(isHidden)

  const setLayout = (next: Layout) => {
    if (onLayoutChange) onLayoutChange(next)
    else setOwnLayout(next)
    onViewChange?.(viewStateOf(spec, next))
  }
  const change = (next: QuerySpec) => {
    onSpecChange(next)
    onViewChange?.(viewStateOf(next, layout))
  }
  // Showing or hiding writes the whole order down, so an extra column shown
  // stays shown (named in `columns`, not in `hidden`).
  const setHidden = (ids: string[]) => setLayout({ ...layout, columns: order, hidden: ids })
  const move = (id: string, to: number) => {
    const next = order.filter((x) => x !== id)
    next.splice(Math.max(0, Math.min(to, next.length)), 0, id)
    setLayout({ ...layout, columns: next, hidden })
  }

  const sortableIds = [...sortable, ...(meta?.fields ?? []).filter((f) => f.sortable).map((f) => f.name)]
  const panelFields = (meta?.fields ?? []).filter((f) => f.filterable && !locked.includes(f.name))
  const byId = new Map(allColumns.map((c) => [columnId(c), c]))
  const visible = order.filter((id) => !isHidden(id)).map((id) => byId.get(id)!)

  // The summed fields, in the response's order; labelled when there are several.
  const summed = useMemo(() => [...new Set((totals ?? []).flatMap((t) => Object.keys(t.sums)))], [totals])

  // Counts (and totals) per group prefix (level 0…n), from the whole-result group list.
  const grouping = useMemo<Grouping<T> | undefined>(() => {
    const levels = spec.group ?? []
    if (levels.length === 0 || !groups) return undefined
    const counts = new Map<string, number>()
    const sums = new Map<string, ListTotal[][]>()
    for (const g of groups) {
      for (let i = 0; i < levels.length; i++) {
        const k = groupKey(g.key.slice(0, i + 1))
        counts.set(k, (counts.get(k) ?? 0) + g.count)
        if (g.totals) sums.set(k, [...(sums.get(k) ?? []), g.totals])
      }
    }
    const label = (f: string) => summed.length > 1 ? `${fields.get(f)?.label ?? f} ` : 'Total '
    const summary = new Map<string, ReactNode>()
    for (const [k, lists] of sums) {
      const merged = mergeTotals(lists)
      summary.set(k, summed.map((f) => `${label(f)}${sumText(merged, f)}`).join(' · '))
    }
    return {
      summary,
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
  }, [spec.group, groups, groupsTruncated, fields, summed])

  // The footer: each visible summed column's total over the whole filtered
  // result (one line per currency); the first column says what the row is.
  const rowsCounted = (totals ?? []).reduce((n, t) => n + t.count, 0)
  const footer = totals && totals.length > 0 && visible.some((c) => summed.includes(columnId(c)))
    ? visible.map((c, i) => {
      const id = columnId(c)
      if (summed.includes(id)) {
        return <span data-total={id} className="flex flex-col">
          {totals.filter((t) => id in t.sums).map((t) => <span key={t.currency ?? ''}>{formatSum(t.sums[id], t.currency)}</span>)}
        </span>
      }
      return i === 0 ? <span className="text-neutral-500">Total · {rowsCounted} {rowsCounted === 1 ? 'row' : 'rows'}</span> : null
    })
    : undefined

  return (
    <>
      <DataTable<T>
        {...view}
        columns={visible}
        grouping={grouping}
        footer={footer}
        errorMessage={error ? errorText(error, errorMessages) : null}
        sort={(spec.sort ?? []).map((s) => ({ id: s.field, desc: !!s.desc }))}
        sortable={sortableIds}
        onSortChange={(s) => change(specFromSorting(spec, [s]))}
        onColumnMove={meta ? (id, beforeId) => move(id, beforeId === null ? order.length : order.filter((x) => x !== id).indexOf(beforeId)) : undefined}
        leading={picker ? <ViewPicker model={picker} /> : undefined}
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
            columns={order.map((id) => ({ id, label: headerText(byId.get(id)!, fields.get(id)) }))}
            hidden={hidden}
            onChange={setHidden}
            onMove={(id, delta) => move(id, order.indexOf(id) + delta)}
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
        afterSearch={(panelFields.length > 0 || picker) ? (
          <>
            {panelFields.length > 0 && (
              <SearchPanel
                open={panel.open}
                onOpenChange={(open) => setPanel((p) => ({ open, focus: open ? p.focus : null }))}
                fields={panelFields}
                filters={(spec.filters ?? []).filter((f) => !locked.includes(f.field))}
                focusField={panel.focus}
                suggest={suggest}
                onApply={(next: Filter[]) => {
                  const keep = (spec.filters ?? []).filter((f) => locked.includes(f.field) || !panelFields.some((p) => p.name === f.field))
                  change({ ...spec, page: 1, filters: [...keep, ...next] })
                }}
              />
            )}
            {picker && <ViewActions model={picker} />}
          </>
        ) : undefined}
        toolbar={toolbar}
        pagination={{
          page: spec.page,
          pageSize: clampPageSize(spec.pageSize),
          total,
          onPageChange: (page) => onSpecChange({ ...spec, page }),
          onPageSizeChange: (size) => change({ ...spec, page: 1, pageSize: clampPageSize(size) }),
        }}
      />
    </>
  )
}
