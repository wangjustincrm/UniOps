import { Fragment, createElement, isValidElement, useEffect, useMemo, useRef, useState, type ComponentType, type ReactNode } from 'react'
import {
  getCoreRowModel, useReactTable,
  type ColumnDef, type SortingState, type Updater,
} from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronRight, GripVertical } from 'lucide-react'
import { Input, Pagination, SkeletonRow } from './shell'
import { cn } from '../lib/cn'
import { EmptyState, ErrorState } from './States'
import { useSynced } from './useSynced'
import { groupKey } from './groupKey'
import { BusyBar } from './BusyBar'
import { useDelayed } from './useDelayed'

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
  /** What a group header shows after its count (e.g. its totals), by the same key. */
  summary?: Map<string, ReactNode>
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
  /** Rows are being fetched again (a refresh, a new page / sort / filter): a bar
   *  on the card's top edge and aria-busy, after a short delay. */
  fetching?: boolean
  /** The rows shown belong to the previous query (kept while the new one loads): dimmed. */
  stale?: boolean
  /** A user's own Refresh: the bar at once (no delay), for as long as the caller keeps it on. */
  busyNow?: boolean
  /** Rows (by rowKey) a refresh brought or changed: a brief highlight. */
  highlight?: ReadonlySet<string | number>
  /** The card's height: 'viewport' fills what is left of the page's view (the rows scroll inside,
   *  the header and the totals stay; a short list shrinks to its rows), a number caps it in px. */
  fit?: 'viewport' | number
  /** The status row pinned to the card's bottom (TableStatusBar), given the pages to show on its right. */
  statusBar?: (pagination: ReactNode) => ReactNode
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
  /** The table's accessible name. */
  ariaLabel?: string
  /** `compact`: the tighter cells of a panel inside a page (LocalTable). */
  density?: 'normal' | 'compact'
  /** One cell per column under the rows (e.g. totals). */
  footer?: ReactNode[]
  /** Per-row class and data-* attributes (e.g. a highlighted row). */
  rowAttrs?: (row: T) => RowAttrs
  /** A row's detail under it, opened by the ▸ in its first cell; null = nothing to open. */
  expand?: (row: T) => ReactNode | null
}

export type RowAttrs = { className?: string } & { [data: `data-${string}`]: string | boolean | undefined }

/** The table view: rows, a sortable header, a search box, paging and the
 *  empty / loading / error states. It knows nothing about the API or the
 *  query contract — QueryTable (patterns) adapts it to a QuerySpec. */
export function DataTable<T>({
  columns, rows, rowKey, onRowClick, loading, fetching, stale, busyNow, highlight, fit, statusBar, errorMessage, onRetry, emptyTitle = 'Nothing here yet',
  sort, sortable = [], onSortChange, headerAddon, leadingHeader, chips, grouping, leading, onColumnMove, search, filters,
  toolbar, pagination, ariaLabel, density = 'normal', footer, rowAttrs, expand,
}: DataTableProps<T>) {
  const cellPad = density === 'compact' ? 'px-2 py-1' : 'px-3 py-2'
  const [dragging, setDragging] = useState<string | null>(null)
  const [dropOn, setDropOn] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const toggleRow = (k: string) => setExpanded((s) => {
    const next = new Set(s)
    if (next.has(k)) next.delete(k)
    else next.add(k)
    return next
  })
  // The leading cell: the header's (e.g. the column chooser) and each row's ▸.
  const leadCell = leadingHeader !== undefined || !!expand
  const toggle = (k: string) => setCollapsed((s) => {
    const next = new Set(s)
    if (next.has(k)) next.delete(k)
    else next.add(k)
    return next
  })
  const [searchDraft, setSearchDraft] = useSynced(search?.value ?? '')
  const sorting: SortingState = useMemo(() => (Array.isArray(sort) ? sort : sort ? [sort] : []), [sort])
  const sortableSet = useMemo(() => new Set(sortable), [sortable])
  // A refetch over rows already shown: the bar (after 200 ms); the first load keeps its skeleton rows.
  const delayed = useDelayed(!!fetching && !(loading && rows.length === 0) && rows.length > 0)
  const busy = delayed || (!!busyNow && rows.length > 0)
  // Rows of the previous query (another page / sort / filter) while the new one loads: dimmed.
  const dim = busy && !!stale

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

  const pager = pagination && pagination.total > 0 ? (
    <Pagination
      page={pagination.page}
      pageSize={pagination.pageSize}
      total={pagination.total}
      onPageChange={pagination.onPageChange}
      onPageSizeChange={pagination.onPageSizeChange}
    />
  ) : null
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
        <Card busy={busy} fit={fit} highlight={highlight} bar={statusBar?.(pager)}>
          <table aria-label={ariaLabel} aria-busy={busy || (loading && rows.length === 0) || undefined} className={cn('w-full', density === 'compact' ? 'text-xs' : 'text-sm')}>
            <thead data-sticky-head className="sticky top-0 z-[1] bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500 shadow-[inset_0_-1px_0_0_var(--color-neutral-200,#e5e7eb)]">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {leadCell && <th className="w-8 px-2 py-2">{leadingHeader}</th>}
                  {hg.headers.map((h) => {
                    const canSort = sortableSet.has(h.column.id) && !!onSortChange
                    const dir = h.column.getIsSorted()
                    const priority = sorting.length > 1 ? sorting.findIndex((s) => s.id === h.column.id) + 1 : 0
                    return (
                      <th
                        key={h.id}
                        className={cn(cellPad, 'font-medium',
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
                              <Render def={h.column.columnDef.header} ctx={h.getContext()} />
                              {dir === 'asc' ? <ArrowUp className="h-3 w-3" /> : dir === 'desc' ? <ArrowDown className="h-3 w-3" /> : <ArrowUpDown className="h-3 w-3 opacity-40" />}
                              {priority > 0 && <sup className="text-[10px] text-neutral-400">{priority}</sup>}
                            </button>
                          ) : (
                            <Render def={h.column.columnDef.header} ctx={h.getContext()} />
                          )}
                          {headerAddon?.(h.column.id)}
                        </span>
                      </th>
                    )
                  })}
                </tr>
              ))}
            </thead>
            <tbody data-stale={dim || undefined} className={cn('transition-opacity', dim && 'opacity-60')}>
              {loading && rows.length === 0
                ? Array.from({ length: 5 }, (_, i) => <SkeletonRow key={i} cols={columns.length + (leadCell ? 1 : 0)} />)
                : table.getRowModel().rows.map((r, i, all) => {
                  const { className: rowClass, ...rowData } = rowAttrs?.(r.original) ?? {}
                  const flash = !!highlight?.has(rowKey(r.original))
                  const detail = expand?.(r.original) ?? null
                  const isOpen = detail !== null && expanded.has(r.id)
                  const row = (
                    <Fragment key={r.id}>
                    <tr
                      key={r.id}
                      {...rowData}
                      data-highlight={flash || undefined}
                      onClick={onRowClick ? () => onRowClick(r.original) : undefined}
                      className={cn('border-t border-neutral-100', onRowClick && 'cursor-pointer hover:bg-neutral-50', rowClass,
                        flash && 'bg-primary-50 animate-[platform-flash_2s_ease-out_forwards] motion-reduce:animate-none')}
                    >
                      {leadCell && (
                        <td className="w-8 px-2">
                          {detail !== null && (
                            <button type="button" aria-expanded={isOpen} aria-label={isOpen ? 'Hide details' : 'Show details'}
                              onClick={(e) => { e.stopPropagation(); toggleRow(r.id) }}
                              className="rounded p-0.5 text-neutral-500 hover:bg-neutral-200 hover:text-neutral-800">
                              {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                            </button>
                          )}
                        </td>
                      )}
                      {r.getVisibleCells().map((c) => (
                        <td key={c.id} className={cn(cellPad, 'text-neutral-700')}>
                          <Render def={c.column.columnDef.cell} ctx={c.getContext()} />
                        </td>
                      ))}
                    </tr>
                    {isOpen && (
                      <tr data-row-detail className="bg-neutral-50/60">
                        <td colSpan={columns.length + 1} className="px-3 py-2 pl-10">{detail}</td>
                      </tr>
                    )}
                    </Fragment>
                  )
                  if (!grouping) return row
                  // A header for each level whose value changes at this row;
                  // rows (and deeper headers) under a collapsed group are left out.
                  const values = grouping.levels.map((l) => grouping.value(r.original, l.id))
                  const prev = i > 0 ? grouping.levels.map((l) => grouping.value(all[i - 1].original, l.id)) : null
                  const first = prev === null ? 0 : values.findIndex((v, j) => String(v ?? null) !== String(prev[j] ?? null))
                  const span = columns.length + (leadCell ? 1 : 0)
                  const headers: ReactNode[] = []
                  if (first !== -1) {
                    for (let level = first; level < values.length; level++) {
                      const key = groupKey(values.slice(0, level + 1))
                      const hiddenByParent = values.slice(0, level).some((_, j) => collapsed.has(groupKey(values.slice(0, j + 1))))
                      if (hiddenByParent) break
                      const count = grouping.counts.get(key)
                      const summary = grouping.summary?.get(key)
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
                            {summary && <span data-group-summary className="ml-3 text-xs font-medium text-neutral-600">{summary}</span>}
                          </td>
                        </tr>,
                      )
                    }
                  }
                  const rowHidden = values.some((_, j) => collapsed.has(groupKey(values.slice(0, j + 1))))
                  return <Fragment key={r.id}>{headers}{!rowHidden && row}</Fragment>
                })}
            </tbody>
            {footer && rows.length > 0 && (
              <tfoot data-stale={dim || undefined} className={cn('sticky bottom-0 z-[1] bg-neutral-50 font-medium shadow-[inset_0_1px_0_0_var(--color-neutral-200,#e5e7eb)] transition-opacity', dim && 'opacity-60')}>
                <tr>
                  {leadCell && <td />}
                  {footer.map((f, i) => <td key={i} className={cn(cellPad, 'text-neutral-800')}>{f}</td>)}
                </tr>
              </tfoot>
            )}
          </table>
          {!loading && rows.length === 0 && <EmptyState title={emptyTitle} className="border-0" />}
          {grouping?.truncated && (
            <p className="border-t border-neutral-100 px-3 py-2 text-xs text-neutral-500">
              Too many groups to count them all: counts are shown for the first 500.
            </p>
          )}
        </Card>
      )}

      {!statusBar && pager}
    </div>
  )
}

/** The table's card (the user, 2026-11-03: "the footer always needs scrolling
 *  to reach"): the rows scroll inside it — sideways too — under a sticky header
 *  and above sticky totals; the status row is pinned to its bottom, outside the
 *  scroll, and stays on screen (sticky to the page's bottom when the page itself
 *  scrolls, on a phone). With `fit`, the card fills what is left of the view, or
 *  less: a short list shrinks to its rows. Columns that do not fit: a fade and
 *  "More columns →" over the scroll area (no height of their own). */
function Card({ children, busy, fit, highlight, bar }: {
  children: ReactNode; busy?: boolean; fit?: 'viewport' | number; highlight?: ReadonlySet<string | number>; bar?: ReactNode
}) {
  const card = useRef<HTMLDivElement>(null)
  const ref = useRef<HTMLDivElement>(null)
  const [more, setMore] = useState(false)
  const [fadeH, setFadeH] = useState(0)
  const maxHeight = useFitHeight(card, ref, fit)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const check = () => { setMore(el.scrollWidth - el.clientWidth - el.scrollLeft > 4); setFadeH(el.clientHeight) }
    const first = requestAnimationFrame(check)
    el.addEventListener('scroll', check, { passive: true })
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(check)
    ro?.observe(el)
    if (el.firstElementChild) ro?.observe(el.firstElementChild)
    return () => { cancelAnimationFrame(first); el.removeEventListener('scroll', check); ro?.disconnect() }
  }, [])
  // A refresh's new rows: brought into view, but only for a user still at the top.
  useEffect(() => {
    const el = ref.current
    if (!el || !highlight?.size || el.scrollTop > 8) return
    const row = el.querySelector('tr[data-highlight]') as HTMLElement | null
    if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' })
  }, [highlight])
  return (
    <div ref={card} data-table-card className="relative flex flex-col rounded-lg border border-neutral-200 bg-white">
      <div ref={ref} data-table-scroll className={cn('overflow-auto', bar ? 'rounded-t-lg' : 'rounded-lg')}
        style={maxHeight ? { maxHeight } : undefined}>{children}</div>
      <BusyBar active={!!busy} className="rounded-t-lg" />
      {more && (
        <>
          <div aria-hidden className="pointer-events-none absolute right-0 top-0 w-10 bg-gradient-to-l from-white to-transparent"
            style={{ height: fadeH }} />
          <button type="button" onClick={() => ref.current?.scrollBy({ left: ref.current.clientWidth * 0.8, behavior: 'smooth' })}
            className="absolute right-1.5 top-1.5 z-[2] rounded-full border border-neutral-200 bg-white/95 px-2 py-0.5 text-xs text-neutral-600 shadow-sm hover:text-neutral-900">
            More columns →
          </button>
        </>
      )}
      {bar && <div className="sticky bottom-0 z-[3] rounded-b-lg bg-white">{bar}</div>}
    </div>
  )
}

/** The rows' height so the card ends at the bottom of what scrolls the page
 *  (a keep-alive tab pane, a sheet's body; else the window), measured as if
 *  scrolled to the top: on a desktop the whole card is in view. On a short
 *  view (a phone) it keeps at least 60 % of the view and the page scrolls with
 *  the bar pinned to its bottom. */
function useFitHeight(card: React.RefObject<HTMLDivElement | null>, scroll: React.RefObject<HTMLDivElement | null>,
  fit: 'viewport' | number | undefined): number | undefined {
  const [h, setH] = useState<number | undefined>(undefined)
  useEffect(() => {
    if (fit !== 'viewport') return
    const el = card.current
    if (!el) return
    const parent = scrollParent(el)
    const measure = () => {
      const view = parent ? parent.clientHeight : window.innerHeight
      const parentTop = parent ? parent.getBoundingClientRect().top - parent.scrollTop : 0
      const top = (scroll.current ?? el).getBoundingClientRect().top - parentTop
      const below = el.getBoundingClientRect().bottom - (scroll.current ?? el).getBoundingClientRect().bottom  // the status row
      const pad = parent ? parseFloat(getComputedStyle(parent.firstElementChild ?? parent).paddingBottom) || 24 : 24
      setH(Math.max(Math.round(view - top - below - pad), Math.round(view * 0.6), 240))
    }
    const first = requestAnimationFrame(measure)
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    if (parent) ro?.observe(parent)
    if (el.parentElement) ro?.observe(el.parentElement)
    window.addEventListener('resize', measure)
    return () => { cancelAnimationFrame(first); ro?.disconnect(); window.removeEventListener('resize', measure) }
  }, [fit, card, scroll])
  return typeof fit === 'number' ? fit : fit === 'viewport' ? h : undefined
}

function scrollParent(el: HTMLElement): HTMLElement | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY
    if ((o === 'auto' || o === 'scroll') && p !== document.body) return p
  }
  return null
}

/** A column's header or cell. TanStack's flexRender makes a function a
 *  component type, and pages build their columns on every render: each render
 *  was a new type, so the cell remounted and lost its state (an open
 *  Autocomplete closed when the list behind it refetched). Here the function
 *  is called inside one stable component instead: same cell, same state. */
function Render<C>({ def, ctx }: { def: unknown; ctx: C }) {
  if (def === null || def === undefined) return null
  if (typeof def === 'function') {
    const proto = (def as { prototype?: { isReactComponent?: unknown } }).prototype
    if (proto?.isReactComponent) return createElement(def as ComponentType<C & object>, ctx as C & object)  // a class component
    return <>{(def as (c: C) => ReactNode)(ctx)}</>
  }
  if (typeof def === 'object' && !isValidElement(def) && '$$typeof' in (def as object)) {
    return createElement(def as ComponentType<C & object>, ctx as C & object)  // memo / forwardRef
  }
  return <>{def as ReactNode}</>
}
