import { Fragment, createElement, isValidElement, useMemo, useRef, useState, type ComponentType, type ReactNode } from 'react'
import {
  getCoreRowModel, useReactTable,
  type ColumnDef, type SortingState, type Updater,
} from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronLeft, ChevronRight, GripVertical } from 'lucide-react'
import { Button, Input, Pagination, Skeleton, SkeletonRow } from './shell'
import { DropdownMenu } from './DropdownMenu'
import { useElementWidth } from './useResponsive'
import { cn } from '../lib/cn'
import { EmptyState, ErrorState } from './States'
import { useSynced } from './useSynced'
import { groupKey } from './groupKey'
import { TableCard } from './TableCard'
import { useDelayed } from './useDelayed'
import { clickOpensRow } from './rowClick'

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
  /** Right after the search box (e.g. the search panel, then the view's Save / Reset). */
  afterSearch?: ReactNode
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
  /** Under this width (px, the table's own box) each row is a card: its title, its badges and
   *  "label: value" lines (column meta `card`), and a Sort menu instead of the headers. Default
   *  640; false keeps the table at every width. */
  cardsBelow?: number | false
  /** In a card, at most this many lines when a row opens (the rest is in the record); every line otherwise. */
  cardLines?: number
}

interface GroupHead { key: string; level: number; id: string; label: string; value: unknown; count?: number; summary?: ReactNode; open: boolean }

export type RowAttrs = { className?: string } & { [data: `data-${string}`]: string | boolean | undefined }

/** The table view: rows, a sortable header, a search box, paging and the
 *  empty / loading / error states. It knows nothing about the API or the
 *  query contract — QueryTable (patterns) adapts it to a QuerySpec. */
export function DataTable<T>({
  columns, rows, rowKey, onRowClick, loading, fetching, stale, busyNow, highlight, fit, statusBar, errorMessage, onRetry, emptyTitle = 'Nothing here yet',
  sort, sortable = [], onSortChange, headerAddon, leadingHeader, chips, grouping, leading, afterSearch, onColumnMove, search, filters,
  toolbar, pagination, ariaLabel, density = 'normal', footer, rowAttrs, expand, cardsBelow = 640, cardLines = 6,
}: DataTableProps<T>) {
  const rootRef = useRef<HTMLDivElement>(null)
  const width = useElementWidth(rootRef)
  const cards = cardsBelow !== false && width !== null && width > 0 && width < cardsBelow
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

  // Group headers that start at row i (each level whose value changes there), and whether
  // the row itself is under a collapsed group. Rows (and deeper headers) under a collapsed group are left out.
  const groupsAt = (i: number, all: { original: T }[]): { heads: GroupHead[]; rowHidden: boolean } => {
    if (!grouping) return { heads: [], rowHidden: false }
    const values = grouping.levels.map((l) => grouping.value(all[i].original, l.id))
    const prev = i > 0 ? grouping.levels.map((l) => grouping.value(all[i - 1].original, l.id)) : null
    const first = prev === null ? 0 : values.findIndex((v, j) => String(v ?? null) !== String(prev[j] ?? null))
    const heads: GroupHead[] = []
    if (first !== -1) {
      for (let level = first; level < values.length; level++) {
        const key = groupKey(values.slice(0, level + 1))
        const hiddenByParent = values.slice(0, level).some((_, j) => collapsed.has(groupKey(values.slice(0, j + 1))))
        if (hiddenByParent) break
        const { id, label } = grouping.levels[level]
        heads.push({ key, level, id, label, value: values[level], count: grouping.counts.get(key), summary: grouping.summary?.get(key), open: !collapsed.has(key) })
      }
    }
    return { heads, rowHidden: values.some((_, j) => collapsed.has(groupKey(values.slice(0, j + 1)))) }
  }
  const groupButton = (g: GroupHead) => (
    <button type="button" aria-expanded={g.open} onClick={() => toggle(g.key)}
      className="inline-flex items-center gap-1.5 text-sm font-medium text-neutral-800 hover:text-neutral-950">
      {g.open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
      <span className="text-xs uppercase tracking-wide text-neutral-500">{g.label}:</span>
      {grouping?.format ? grouping.format(g.id, g.value) : String(g.value ?? '(empty)')}
      {g.count !== undefined && <span className="rounded-full bg-neutral-200 px-1.5 text-xs text-neutral-600">{g.count}</span>}
    </button>
  )

  const pager = pagination && pagination.total > 0 ? (cards ? <CompactPager {...pagination} /> : (
    <Pagination
      page={pagination.page}
      pageSize={pagination.pageSize}
      total={pagination.total}
      onPageChange={pagination.onPageChange}
      onPageSizeChange={pagination.onPageSizeChange}
    />
  )) : null
  const headerById = new Map((table.getHeaderGroups()[0]?.headers ?? []).map((h) => [h.column.id, h]))
  const headerIds = (table.getHeaderGroups()[0]?.headers ?? []).map((h) => h.column.id)
  const headerLabel = (id: string) => {
    const def = headerById.get(id)?.column.columnDef.header
    return typeof def === 'string' ? def : id
  }
  const sortMenu = cards && onSortChange && sortable.some((id) => headerById.has(id)) ? (
    <DropdownMenu label="Sort by" align="start"
      items={sortable.filter((id) => headerById.has(id)).map((id) => {
        const cur = sorting[0]?.id === id ? sorting[0] : null
        return { label: `${headerLabel(id)}${cur ? (cur.desc ? ' ↓' : ' ↑') : ''}`, onSelect: () => onSortChange({ id, desc: cur ? !cur.desc : false }) }
      })}
      trigger={<Button variant="secondary" size="sm" aria-label="Sort by"><ArrowUpDown className="h-3.5 w-3.5" aria-hidden />Sort</Button>} />
  ) : null
  const cardLead = cards && leadingHeader !== undefined ? leadingHeader : null

  // The card view: one card per row (title, badges, "label: value" lines), group headers between
  // them, the totals under them. Same rows, clicks, keys, highlight and details as the table.
  const renderCards = () => {
    const label = (id: string) => {
      const h = headerById.get(id)
      return h ? <Render def={h.column.columnDef.header} ctx={h.getContext()} /> : id
    }
    const all = table.getRowModel().rows
    return (
      <>
        <ul aria-label={ariaLabel} aria-busy={busy || (loading && rows.length === 0) || undefined} data-stale={dim || undefined}
          className={cn('divide-y divide-neutral-100 transition-opacity', density === 'compact' ? 'text-xs' : 'text-sm', dim && 'opacity-60')}>
          {loading && rows.length === 0
            ? Array.from({ length: 4 }, (_, i) => <li key={i} className="px-3 py-3"><Skeleton className="h-12" /></li>)
            : all.map((r, i) => {
              const { className: rowClass, ...rowData } = rowAttrs?.(r.original) ?? {}
              const flash = !!highlight?.has(rowKey(r.original))
              const detail = expand?.(r.original) ?? null
              const isOpen = detail !== null && expanded.has(r.id)
              const cells = r.getVisibleCells()
              const role = (c: (typeof cells)[number]) => c.column.columnDef.meta?.card
              const title = cells.find((c) => role(c) === 'title') ?? cells.find((c) => !role(c))
              const badges = cells.filter((c) => role(c) === 'badge')
              const lines = cells.filter((c) => c !== title && !role(c))
              const shownLines = onRowClick ? lines.slice(0, cardLines) : lines
              const { heads, rowHidden } = groupsAt(i, all)
              return (
                <Fragment key={r.id}>
                  {heads.map((g) => (
                    <li key={`g:${g.key}`} data-group-head className="bg-neutral-50/70 py-1.5 pr-3" style={{ paddingLeft: `${0.75 + g.level * 1}rem` }}>
                      {groupButton(g)}
                      {g.summary && <div data-group-summary className="mt-0.5 text-xs font-medium text-neutral-600">{g.summary}</div>}
                    </li>
                  ))}
                  {!rowHidden && (
                    <li {...rowData} data-row-card data-highlight={flash || undefined}
                      onClick={onRowClick ? (e) => { if (clickOpensRow(e)) onRowClick(r.original) } : undefined}
                      tabIndex={onRowClick ? 0 : undefined}
                      onKeyDown={onRowClick ? (e) => {
                        if (e.target === e.currentTarget && e.key === 'Enter') { e.preventDefault(); onRowClick(r.original) }
                      } : undefined}
                      className={cn('flex min-w-0 flex-col gap-1.5 px-3 py-2.5',
                        onRowClick && 'cursor-pointer hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary-500',
                        rowClass, flash && 'bg-primary-50 animate-[platform-flash_2s_ease-out_forwards] motion-reduce:animate-none')}>
                      <div className="flex min-w-0 items-start justify-between gap-2">
                        <div data-card-title className="min-w-0 break-words font-medium text-neutral-900">
                          {title && <Render def={title.column.columnDef.cell} ctx={title.getContext()} />}
                        </div>
                        {badges.length > 0 && (
                          <div className="flex shrink-0 flex-wrap justify-end gap-1">
                            {badges.map((c) => <Fragment key={c.id}><Render def={c.column.columnDef.cell} ctx={c.getContext()} /></Fragment>)}
                          </div>
                        )}
                      </div>
                      {shownLines.length > 0 && (
                        <dl className="grid grid-cols-[minmax(4.5rem,max-content)_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
                          {shownLines.map((c) => (
                            <div key={c.id} className="contents">
                              <dt className="text-neutral-500">{label(c.column.id)}</dt>
                              <dd className="min-w-0 break-words text-neutral-700"><Render def={c.column.columnDef.cell} ctx={c.getContext()} /></dd>
                            </div>
                          ))}
                        </dl>
                      )}
                      {detail !== null && (
                        <div>
                          <button type="button" aria-expanded={isOpen} onClick={() => toggleRow(r.id)}
                            className="inline-flex items-center gap-1 rounded py-0.5 text-xs text-neutral-600 hover:text-neutral-900">
                            {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                            {isOpen ? 'Hide details' : 'Show details'}
                          </button>
                          {isOpen && <div data-row-detail className="mt-1 rounded bg-neutral-50 p-2">{detail}</div>}
                        </div>
                      )}
                    </li>
                  )}
                </Fragment>
              )
            })}
        </ul>
        {footer && rows.length > 0 && (
          <div data-card-totals data-stale={dim || undefined}
            className={cn('sticky bottom-0 z-[1] border-t border-neutral-200 bg-neutral-50 px-3 py-2 text-xs font-medium text-neutral-800', dim && 'opacity-60')}>
            {footer[0] && <div className="mb-0.5">{footer[0]}</div>}
            <dl className="grid grid-cols-[minmax(4.5rem,max-content)_minmax(0,1fr)] gap-x-3 gap-y-0.5">
              {footer.map((f, i) => (i === 0 || f === null || f === undefined ? null : (
                <div key={i} className="contents">
                  <dt className="text-neutral-500">{headerIds[i] ? label(headerIds[i]) : null}</dt>
                  <dd>{f}</dd>
                </div>
              )))}
            </dl>
          </div>
        )}
      </>
    )
  }

  return (
    <div ref={rootRef} data-cards={cards || undefined} className="flex min-w-0 flex-col gap-3">
      {(search || toolbar || filters || leading || afterSearch || cardLead || sortMenu) && (
        <div className="flex flex-wrap items-center gap-2">
          {leading}
          {cardLead}
          {sortMenu}
          {search && (
            <Input
              aria-label="Search"
              placeholder="Search…"
              value={searchDraft}
              className="min-w-0 flex-1 basis-40 sm:max-w-xs"
              onChange={(e) => setSearchDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') search.onSubmit(searchDraft.trim())
              }}
            />
          )}
          {afterSearch}
          {filters}
          <div className="ml-auto flex flex-wrap gap-2">{toolbar}</div>
        </div>
      )}

      {chips}

      {errorMessage ? (
        <ErrorState message={errorMessage} onRetry={onRetry} />
      ) : (
        <TableCard busy={busy} fit={fit} highlight={highlight} bar={statusBar?.(pager)}>
          {cards ? renderCards() : (
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
                      onClick={onRowClick ? (e) => { if (clickOpensRow(e)) onRowClick(r.original) } : undefined}
                      tabIndex={onRowClick ? 0 : undefined}
                      onKeyDown={onRowClick ? (e) => {
                        if (e.target === e.currentTarget && e.key === 'Enter') { e.preventDefault(); onRowClick(r.original) }
                      } : undefined}
                      className={cn('border-t border-neutral-100', onRowClick && 'cursor-pointer hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary-500', rowClass,
                        flash && 'bg-primary-50 animate-[platform-flash_2s_ease-out_forwards] motion-reduce:animate-none')}
                    >
                      {leadCell && (
                        <td className="w-8 px-2">
                          {detail !== null && (
                            <button type="button" aria-expanded={isOpen} aria-label={isOpen ? 'Hide details' : 'Show details'}
                              onClick={() => toggleRow(r.id)}
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
                  const { heads, rowHidden } = groupsAt(i, all)
                  const span = columns.length + (leadCell ? 1 : 0)
                  const headers = heads.map((g) => (
                    <tr key={`g:${g.key}`} className="border-t border-neutral-200 bg-neutral-50/70">
                      <td colSpan={span} className="py-1.5 pr-3" style={{ paddingLeft: `${0.75 + g.level * 1.25}rem` }}>
                        {groupButton(g)}
                        {g.summary && <span data-group-summary className="ml-3 text-xs font-medium text-neutral-600">{g.summary}</span>}
                      </td>
                    </tr>
                  ))
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
          )}
          {!loading && rows.length === 0 && <EmptyState title={emptyTitle} className="border-0" />}
          {grouping?.truncated && (
            <p className="border-t border-neutral-100 px-3 py-2 text-xs text-neutral-500">
              Too many groups to count them all: counts are shown for the first 500.
            </p>
          )}
        </TableCard>
      )}

      {!statusBar && pager}
    </div>
  )
}

/** The pages in a narrow (card) view: ‹ 1–50 of 116 › on one short line; the page size stays as chosen. */
function CompactPager({ page, pageSize, total, onPageChange }: { page: number; pageSize: number; total: number; onPageChange: (page: number) => void }) {
  const last = Math.max(1, Math.ceil(total / pageSize))
  const from = (page - 1) * pageSize + 1
  const to = Math.min(page * pageSize, total)
  const btn = 'inline-flex h-8 w-8 items-center justify-center rounded-md text-neutral-600 hover:bg-neutral-100 disabled:opacity-30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600'
  return (
    <nav aria-label="Pages" data-compact-pager className="flex items-center gap-1 text-xs tabular-nums text-neutral-600">
      <button type="button" aria-label="Previous page" disabled={page <= 1} onClick={() => onPageChange(page - 1)} className={btn}>
        <ChevronLeft className="h-4 w-4" aria-hidden />
      </button>
      <span>{from}–{to} of {total}</span>
      <button type="button" aria-label="Next page" disabled={page >= last} onClick={() => onPageChange(page + 1)} className={btn}>
        <ChevronRight className="h-4 w-4" aria-hidden />
      </button>
    </nav>
  )
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
