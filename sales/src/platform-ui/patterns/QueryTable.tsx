import { BooleanCell, DateCell, MoneyCell, NumberCell, StackedDateTimeCell, StatusCell } from '../primitives/cells'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Button } from '../primitives/shell'
import { TourMark } from '../help'
import { DataTable, type ColumnDef, type Grouping } from '../primitives/DataTable'
import { groupKey } from '../primitives/groupKey'
import { formatMoney } from '../primitives/format'
import { columnMismatch, useListMeta, type FieldMeta, type ListMeta } from '../resource/listMeta'
import {
  MAX_GROUP_KEYS, clampPageSize, groupBy, setFieldFilters, sortBy, type Filter, type GroupCount, type ListTotal, type QuerySpec,
} from '../resource/querySpec'
import { MAX_LOCKED, normalizeView, viewStateOf, type Layout, type ViewState } from '../resource/viewState'
import { errorText } from '../resource/businessError'
import { ColumnChooser } from '../primitives/ColumnChooser'
import { columnId, startsHidden } from '../primitives/columnMeta'
import { ViewActions, ViewPicker } from './ViewPicker'
import type { ViewPickerModel } from './views'
import { ColumnMenu } from './ColumnMenu'
import { FilterChips } from './FilterChips'
import { SearchPanel } from './SearchPanel'
import { useNotices } from './notices'
import { cn } from '../lib/cn'
import type { PanelDraft } from './searchDraft'
import { FieldSearch } from './FieldSearch'
import { RefLink } from './RefLink'
import { filterText } from './listView'
import { Filter as FilterIcon } from 'lucide-react'
import { specFromSorting } from './sorting'

export interface QueryTableProps<T> {
  /** The table's accessible name. */
  ariaLabel?: string
  columns: ColumnDef<T, unknown>[]
  /** Page columns shown only where a view names them, with those declared `startHidden`. Only for a choice that depends on the page (e.g. a channel's own columns). */
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
  resource?: { basePath: string; meta: () => Promise<ListMeta>; suggest?: (field: string, query: string) => Promise<string[]>
    count?: (spec: QuerySpec) => Promise<{ total: number }> }
  /** A record's sheet is open over the list: the search panel closes and keeps its draft. */
  recordOpen?: boolean
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
  /** A row's detail under it, opened with its ▸ (DataTable `expand`); null: nothing to open. */
  expand?: (row: T) => ReactNode | null
  emptyTitle?: string
  emptyText?: string
  toolbar?: ReactNode
  /** Everything the view is, on every change. */
  onViewChange?: (view: ViewState) => void
  /** Controlled layout (column order and visibility), e.g. from useListView. */
  layout?: Layout
  onLayoutChange?: (layout: Layout) => void
  /** The list's saved views (useListView(...).picker): shown left of the toolbar. */
  picker?: ViewPickerModel
  /** First on the left, before the views and Search (a scoped list's lock). */
  lead?: ReactNode
  /** How many first columns stay in place while the table scrolls sideways when the view does not say
   *  (default 1; 0 none). The user changes it from a column's menu; it saves with the view. */
  defaultLocked?: number
  /** The visible columns an export can write, in their order: those /meta says each row carries and the page's own
   *  columns the server computes; and the names of those it cannot. Told on every change. */
  onExportColumns?: (columns: ExportColumns) => void
}

export interface ExportColumns { ids: string[]; left: string[] }
/** Columns of controls, not of data (checkbox, ⋯ menu, row buttons). */
const isControl = (id: string) => id.startsWith('__') || id === 'actions'

/** The chooser's two groups: the page's own columns, then every other field of the row. */
const COLUMN_GROUPS = [{ id: 'page', label: 'On this list' }, { id: 'more', label: 'More fields' }]

const headerText = <T,>(c: ColumnDef<T, unknown>, field?: FieldMeta): string =>
  typeof c.header === 'string' ? c.header : field?.label ?? columnId(c)

function formatValue(field: FieldMeta, v: unknown, row?: Record<string, unknown>): ReactNode {
  if (v === null || v === undefined || v === '') return ''
  // A field added from the chooser looks like the same kind of value in any list.
  if (field.money) return <MoneyCell amount={v as string | number} currency={field.currency_field && row ? row[field.currency_field] as string | null : undefined} />
  if (field.type === 'enum') return <StatusCell value={String(v)} />
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

/** A column whose field names a record (/meta `ref`): its cell, as it is, inside a link to that record — for those
 *  who may read it (/meta `refs`); the others see the cell only. The column opts out with `meta.ref: false`. */
function linked<T>(c: ColumnDef<T, unknown>, fields: Map<string, FieldMeta>, meta: ListMeta | undefined): ColumnDef<T, unknown> {
  const ref = fields.get(columnId(c))?.ref
  if (!ref || c.meta?.ref === false || !meta?.refs?.[ref.type]?.readable) return c
  const inner = c.cell
  return {
    ...c,
    cell: (ctx) => {
      const content = (typeof inner === 'function' ? inner(ctx) : inner ?? ctx.renderValue()) as ReactNode
      const key = (ctx.row.original as Record<string, unknown>)[ref.key]
      if (key == null || key === '') return content
      return <RefLink record={{ type: ref.type, key: key as string | number }}>{content}</RefLink>
    },
  }
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
  return <QueryTableView {...props} meta={useListMeta(resource).data} suggest={resource.suggest} count={resource.count} />
}

function QueryTableView<T>({
  columns, defaultHidden, spec, onSpecChange, meta, locked = [], sortable = [], total, groups, groupsTruncated, totals, searchable, error,
  errorMessages, toolbar, onViewChange, layout: layoutProp, onLayoutChange, picker, lead, suggest, onExportColumns, defaultLocked = 1, count, recordOpen, ...view
}: ViewProps<T> & { meta: ListMeta | undefined; suggest?: (field: string, query: string) => Promise<string[]>
  count?: (spec: QuerySpec) => Promise<{ total: number }> }) {
  const fields = useMemo(() => new Map((meta?.fields ?? []).map((f) => [f.name, f])), [meta])
  const [ownLayout, setOwnLayout] = useState<Layout>({ columns: [], hidden: [] })
  const layout = layoutProp ?? ownLayout
  const [panel, setPanel] = useState<{ open: boolean; focus: string | null }>({ open: false, focus: null })
  // One field's search, at its column or at one of its Search chips (#32 a / c): which field, and where.
  const [fieldSearch, setFieldSearch] = useState<{ field: string; at: string } | null>(null)
  // The panel's draft while a record sheet has it closed (#32 b): kept for the next open, dropped by Search or Esc.
  const [panelDraft, setPanelDraft] = useState<PanelDraft | null>(null)
  const kept = useRef<PanelDraft | null>(null)
  useEffect(() => {
    if (recordOpen && panel.open) { setPanelDraft(kept.current); setPanel({ open: false, focus: null }) }
  }, [recordOpen])  // eslint-disable-line react-hooks/exhaustive-deps -- only when a sheet opens
  // What Search found, said once its rows are here (#32 E): in the list's status row (a live region), else in one of its own.
  const notices = useNotices()
  const [said, setSaid] = useState('')
  const saying = useRef(false)
  const say = (n: number) => {
    const text = `${n} ${n === 1 ? 'row matches' : 'rows match'} the search.`
    if (notices.inScope) notices.notify({ tone: 'info', text, key: 'search' })
    else setSaid(text)
  }
  useEffect(() => {
    if (!saying.current) return
    saying.current = false
    say(total)
  }, [view.rows])  // eslint-disable-line react-hooks/exhaustive-deps -- the rows of the new search
  const [dragging, setDragging] = useState<string | null>(null)  // a header's column being dragged (grouping by drop)

  // The page's columns, then every other field /meta describes (hidden at first).
  const pageIds = useMemo(() => new Set(columns.map(columnId)), [columns])
  // In development: say when the page and its server's views disagree on a column id (a typo there
  // used to do nothing at all). Tests assert columnMismatch directly.
  useEffect(() => {
    if (!import.meta.env.DEV || !meta) return
    for (const m of columnMismatch(meta, [...pageIds])) console.warn(`[QueryTable ${view.ariaLabel ?? ''}] ${m}`)
  }, [meta, pageIds])  // eslint-disable-line react-hooks/exhaustive-deps
  const allColumns = useMemo<ColumnDef<T, unknown>[]>(() => [
    ...columns.map((c) => linked(c, fields, meta)),
    // Only what each row carries (`in_row`): a key the list only filters or sorts on would be an empty column.
    ...(meta?.fields ?? []).filter((f) => !pageIds.has(f.name) && f.in_row !== false).map((f): ColumnDef<T, unknown> => ({
      id: f.name, header: f.label,
      cell: ({ row }) => formatValue(f, (row.original as Record<string, unknown>)[f.name], row.original as Record<string, unknown>),
    })).map((c) => linked(c, fields, meta)),
  ], [columns, meta, pageIds, fields])
  // Layout (resource/viewState.ts): no `columns` = the page's order, its own
  // columns shown, the extra ones hidden; otherwise `columns` is the whole
  // order, and a column it does not name (added later) shows if it is the page's.
  const allIds = allColumns.map(columnId)
  const order = layout.columns.length
    ? [...layout.columns.filter((id) => allIds.includes(id)), ...allIds.filter((id) => !layout.columns.includes(id))]
    : allIds
  const startHidden = useMemo(() => new Set(startsHidden(columns, defaultHidden)), [columns, defaultHidden])
  const shownByDefault = (id: string) => pageIds.has(id) && !startHidden.has(id)
  const isHidden = (id: string) =>
    layout.hidden.includes(id) || (!shownByDefault(id) && !(layout.columns.length > 0 && layout.columns.includes(id)))
  const hidden = order.filter(isHidden)

  const setLayout = (next: Layout) => {
    if (onLayoutChange) onLayoutChange(next)
    else setOwnLayout(next)
    onViewChange?.(viewStateOf(spec, next))
  }
  // The spec as last asked for: two changes in one event (the pager sends the page size, then page 1)
  // build on each other, not both on the spec of this render — else the second undoes the first.
  const latest = useRef(spec)
  useEffect(() => { latest.current = spec }, [spec])
  const change = (next: QuerySpec) => {
    latest.current = next
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
  // A field the user added (not one the page shows by default): marked, so a phone card always shows it.
  // The same object each render (a new column definition would remount its cells).
  const asChosen = useMemo(() => new Map(allColumns.map((c) => [columnId(c), { ...c, meta: { ...c.meta, chosen: true } }])), [allColumns])
  const visible = order.filter((id) => !isHidden(id)).map((id) => (shownByDefault(id) ? byId.get(id)! : asChosen.get(id)!))

  const computed = new Set(meta?.capabilities?.export?.computed.map((c) => c.id))
  const canExport = (id: string) => !!fields.get(id)?.in_row || computed.has(id)
  const exportIds = visible.map(columnId).filter(canExport).join(',')
  const leftOut = visible.filter((c) => !canExport(columnId(c)) && !isControl(columnId(c))).map((c) => headerText(c, fields.get(columnId(c)))).join('\n')
  useEffect(() => {
    onExportColumns?.({ ids: exportIds ? exportIds.split(',') : [], left: leftOut ? leftOut.split('\n') : [] })
  }, [exportIds, leftOut])  // eslint-disable-line react-hooks/exhaustive-deps

  const visibleIds = visible.map(columnId)
  const lockedCount = Math.min(layout.locked ?? defaultLocked, MAX_LOCKED, visibleIds.length)
  const lockedIds = visibleIds.slice(0, lockedCount)
  const [frozen, setFrozen] = useState(lockedCount)  // how many DataTable could freeze (fewer on a narrow window)
  /** Lock: the column joins the end of the locked group; Unlock: it leaves to the first place after the group.
   *  The others keep their place and order; `locked` stays the count of the first visible columns. */
  const toggleLock = (id: string) => {
    const isLocked = lockedIds.includes(id)
    if (!isLocked && lockedCount >= MAX_LOCKED) return
    const keep = lockedIds.filter((x) => x !== id)                 // the locked group without this column
    const rest = order.filter((x) => x !== id)
    const after = keep.length ? rest.indexOf(keep[keep.length - 1]) + 1 : 0
    rest.splice(after, 0, id)
    setLayout({ ...layout, columns: rest, hidden, locked: isLocked ? keep.length : keep.length + 1 })
  }
  const shownKey = visibleIds.join(',')
  const shownIds = shownKey.split(',')

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
    // A group's header: the list's declared sums, and any other amount the user shows as a column.
    const inHeader = summed.filter((f) => fields.get(f)?.summable || shownIds.includes(f))
    const label = (f: string) => inHeader.length > 1 ? `${fields.get(f)?.label ?? f} ` : 'Total '
    const summary = new Map<string, ReactNode>()
    for (const [k, lists] of sums) {
      const merged = mergeTotals(lists)
      summary.set(k, inHeader.map((f) => `${label(f)}${sumText(merged, f)}`).join(' · '))
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
  }, [spec.group, groups, groupsTruncated, fields, summed, shownKey])  // eslint-disable-line react-hooks/exhaustive-deps

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
      return i === 0 ? <span className="text-neutral-600">Total · {rowsCounted} {rowsCounted === 1 ? 'row' : 'rows'}</span> : null
    })
    : undefined

  // Empty because of a filter, a search or a view's filters (the page's own fixed ones don't count): the
  // list's "why it is empty" would mislead; say what narrows it and offer to clear it.
  const own = (spec.filters ?? []).filter((f) => !locked.includes(f.field))
  const narrowed = !!spec.search || own.length > 0
  // ...unless all that narrows it is the active view's own filters and search (e.g. Open, On hold): then
  // the view is simply empty, and "Clear filters" would only mean leaving it (the user's choice, 2026-11-17).
  const viewState = picker?.current
    ? (picker.current.kind === 'system' ? picker.system.find((v) => v.key === (picker.current as { key: string }).key)
      : picker.mine.find((v) => v.id === (picker.current as { id: number }).id))?.state
    : undefined
  const narrowing = (v: { filters?: Filter[]; search?: string }) => {  // in the views' one shape (normalizeView, as `dirty` compares)
    const n = normalizeView({ filters: (v.filters ?? []).filter((f) => !locked.includes(f.field)), search: v.search })
    return JSON.stringify([n.filters, n.search ?? ''])
  }
  const sameAsView = !!viewState && narrowing({ filters: spec.filters, search: spec.search }) === narrowing(viewState)
  const empty = narrowed && sameAsView ? { emptyTitle: 'Nothing in this view', emptyText: undefined, emptyAction: undefined } : narrowed ? {
    emptyTitle: 'No rows match the current filters',
    emptyText: undefined,
    emptyAction: (
      <Button variant="secondary" size="sm" onClick={() => change({ ...spec, page: 1, search: undefined, filters: (spec.filters ?? []).filter((f) => locked.includes(f.field)) })}>
        Clear filters
      </Button>
    ),
  } : {}
  return (
    <>
      <DataTable<T>
        {...view}
        {...empty}
        columns={visible}
        locked={lockedCount}
        onFrozenChange={setFrozen}
        grouping={grouping}
        footer={footer}
        errorMessage={error ? errorText(error, errorMessages) : null}
        sort={(spec.sort ?? []).map((s) => ({ id: s.field, desc: !!s.desc }))}
        sortable={sortableIds}
        onSortChange={(s) => change(specFromSorting(spec, [s]))}
        onColumnMove={meta ? (id, beforeId) => move(id, beforeId === null ? order.length : order.filter((x) => x !== id).indexOf(beforeId)) : undefined}
        onColumnDrag={meta ? setDragging : undefined}
        leading={lead}
        headerAddon={(id) => {
          const f = fields.get(id)
          const searched = (spec.filters ?? []).filter((x) => x.field === id)
          const menu = f ? (
            <ColumnMenu
              field={f}
              onSearch={() => (panelFields.some((p) => p.name === id) ? setFieldSearch({ field: id, at: 'column' }) : setPanel({ open: true, focus: id }))}
              onSort={(desc) => change(sortBy(spec, id, desc))}
              onGroup={(desc) => change(groupBy(spec, id, desc))}
              canGroup={(spec.group ?? []).length < MAX_GROUP_KEYS || (spec.group ?? []).some((g) => g.field === id)}
              onHide={() => setHidden([...hidden, id])}
              lock={{ locked: lockedIds.includes(id), full: lockedCount >= MAX_LOCKED, max: MAX_LOCKED, onToggle: () => toggleLock(id) }}
            />
          ) : null
          if (!f || !panelFields.some((p) => p.name === id)) return menu
          // The header's filter icon opens the column's own search (user 2026-11-26). Searched: always there, solid, named by
          // its search (never a colour alone, #32 A). Not searched: faint, shown on the header's hover or keyboard focus (always
          // on a touch screen). It never sorts or drags the column: it is a button of its own, and its clicks stop here.
          const words = searched.map((x) => filterText(x, f)).join('; ')
          const mark = (
            <button type="button" data-column-search={id} aria-label={searched.length ? `Edit search: ${words}` : `Search ${f.label}`}
              title={searched.length ? words : `Search ${f.label}`}
              onClick={(e) => { e.stopPropagation(); e.currentTarget.focus(); setFieldSearch({ field: id, at: 'column' }) }}  // focused: it gets it back (Safari does not focus a clicked button)
              onKeyDown={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()} draggable={false}
              className={cn('ml-1 inline-flex h-6 w-6 items-center justify-center rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600',
                searched.length ? 'text-primary-700'
                  : 'text-neutral-400 opacity-0 hover:text-neutral-700 group-hover/th:opacity-100 group-focus-within/th:opacity-100 pointer-coarse:opacity-100')}>
              <FilterIcon aria-hidden className="h-3 w-3" fill={searched.length ? 'currentColor' : 'none'} />
            </button>
          )
          return (
            <FieldSearch field={f} filters={searched} suggest={suggest}
              open={fieldSearch?.field === id && fieldSearch.at === 'column'}
              onOpenChange={(open) => setFieldSearch(open ? { field: id, at: 'column' } : null)}
              onApply={(next) => change(setFieldFilters(spec, id, next))}
              onAllFields={() => { setFieldSearch(null); setPanel({ open: true, focus: id }) }}>
              {menu}{mark}
            </FieldSearch>
          )
        }}
        leadingHeader={meta ? (
          <ColumnChooser
            columns={order.map((id) => ({ id, label: headerText(byId.get(id)!, fields.get(id)), group: pageIds.has(id) ? 'page' : 'more',
              locked: lockedIds.includes(id) }))}
            locking={{ onToggle: toggleLock, full: lockedCount >= MAX_LOCKED, max: MAX_LOCKED,
              note: frozen < lockedCount ? `${frozen} of ${lockedCount} locked columns frozen: the window is too narrow` : null }}
            groups={COLUMN_GROUPS}
            hidden={hidden}
            onChange={setHidden}
            onMove={(id, delta) => move(id, order.indexOf(id) + delta)}
          />
        ) : undefined}
        chips={meta ? (
          <FilterChips spec={spec} onSpecChange={change} fields={fields} locked={locked}
            editor={(field, at, chip) => {
              const f = fields.get(field)
              if (!f || !panelFields.some((p) => p.name === field)) return chip
              return (
                <FieldSearch key={at} field={f} filters={(spec.filters ?? []).filter((x) => x.field === field)} suggest={suggest}
                  open={fieldSearch?.field === field && fieldSearch.at === at}
                  onOpenChange={(open) => setFieldSearch(open ? { field, at } : null)}
                  onApply={(next) => change(setFieldFilters(spec, field, next))}
                  onAllFields={() => { setFieldSearch(null); setPanel({ open: true, focus: field }) }}>
                  {chip}
                </FieldSearch>
              )
            }}
            onEdit={(field, at) => setFieldSearch({ field, at })}
            groupDrop={{
              dragging,
              // The same rules as the column menu's Group by (MAX_GROUP_KEYS, the field's own groupable).
              refuse: (id) => {
                const f = fields.get(id)
                if (!f?.groupable) return `${f?.label ?? headerText(byId.get(id) ?? { id }, f)} cannot be grouped`
                if ((spec.group ?? []).some((g) => g.field === id)) return `Already grouped by ${f.label}`
                if ((spec.group ?? []).length >= MAX_GROUP_KEYS) return `At most ${MAX_GROUP_KEYS} groupings: remove one first`
                return null
              },
              onGroup: (id) => { setDragging(null); change(groupBy(spec, id, false)) },
            }}
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
                onOpenChange={(open, how) => {
                  if (!open && how !== 'kept') setPanelDraft(null)  // Esc, ✕ or the icon: the draft goes
                  setPanel((p) => ({ open, focus: open ? p.focus : null }))
                }}
                fields={panelFields}
                filters={(spec.filters ?? []).filter((f) => !locked.includes(f.field))}
                draft={panelDraft}
                onDraftChange={(dr) => { kept.current = dr }}
                focusField={panel.focus}
                suggest={suggest}
                groups={meta?.groups}
                shown={order.filter((id) => !hidden.includes(id))}
                viewFilters={viewState ? (viewState.filters ?? []).filter((f) => !locked.includes(f.field)) : null}
                count={count ? (next) => count({ ...spec, page: 1, filters: [...(spec.filters ?? []).filter((f) => locked.includes(f.field)
                  || !panelFields.some((p) => p.name === f.field)), ...next] }).then((r) => r.total) : undefined}
                onApply={(next: Filter[]) => {
                  const keep = (spec.filters ?? []).filter((f) => locked.includes(f.field) || !panelFields.some((p) => p.name === f.field))
                  setPanelDraft(null)
                  const filters = [...keep, ...next]
                  if (JSON.stringify(filters) === JSON.stringify(spec.filters ?? [])) say(total)  // nothing changes: no new rows to wait for
                  else saying.current = true
                  change({ ...spec, page: 1, filters })
                }}
              />
            )}
            {/* The view, right after Search and its filters, then what its changes can become (the user, 2026-11-27). */}
            {picker && <TourMark help="list.views" slot="list.views" className="inline-flex"><ViewPicker model={picker} /></TourMark>}
            {picker && <span data-help="list.saveView" className="inline-flex empty:hidden"><ViewActions model={picker} /></span>}
          </>
        ) : undefined}
        toolbar={toolbar}
        pagination={{
          page: spec.page,
          pageSize: clampPageSize(spec.pageSize),
          total,
          onPageChange: (page) => change({ ...latest.current, page }),
          onPageSizeChange: (size) => change({ ...latest.current, page: 1, pageSize: clampPageSize(size) }),
        }}
      />
      {!notices.inScope && said && <div role="status" className="sr-only">{said}</div>}
    </>
  )
}
