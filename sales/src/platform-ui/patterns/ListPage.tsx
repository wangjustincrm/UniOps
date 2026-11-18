import { UnsavedBoundary, useUnsavedScope, whenDiscarded } from '../primitives/unsaved'
import { Fragment, useEffect, useState, type ReactNode } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnDef } from '../primitives/DataTable'
import { PageHeader } from '../primitives/PageHeader'
import type { HelpId } from '../help'
import { listTools } from './listHelp'
import { TourMark, TourScope } from '../help'
import { useListMeta } from '../resource/listMeta'
import { Sheet } from '../primitives/Sheet'
import type { ListMeta } from '../resource/listMeta'
import { queryKeys } from '../resource/queryKeys'
import { takeOpenRequest, useOpenRequest } from '../resource/viewRequests'
import type { Filter, Page, QuerySpec } from '../resource/querySpec'
import { Download } from 'lucide-react'
import { DropdownMenu } from '../primitives/DropdownMenu'
import { ActionButton } from './ActionButton'
import { RefreshControl } from './RefreshControl'
import { compareRows, useRefresh } from './useRefresh'
import { NoticesContext, createNoticeStore, type NoticeStore } from './notices'
import { TableStatusBar } from './TableStatusBar'
import { errorText } from '../resource/businessError'
import { storeAutoRefresh, storedAutoRefresh } from './autoRefresh'
import { QueryTable } from './QueryTable'
import { RecordSheetHostContext, type RecordSheetHost, type RecordSheetSize } from './recordSheetHost'
import { useListView } from './views'

/** What a list needs from its resource client. */
export interface ListResource<T> {
  basePath: string
  list: (spec: QuerySpec) => Promise<Page<T>>
  meta: () => Promise<ListMeta>
  /** One record by id: a record asked for from elsewhere opens with its data. */
  get?: (id: number) => Promise<T>
  /** The CSV of every row a query selects (the server's export.csv): needed for `onExport`. `columns`:
   *  only those, in that order (the visible columns); none: the list's full export. */
  exportPath?: (spec: QuerySpec, file?: string, columns?: string[]) => string
}

export interface ListContext<T> {
  spec: QuerySpec
  rows: T[]
  /** Fetch the list again (e.g. after an action). */
  refresh: () => Promise<void>
}

export interface ListPageProps<T extends { id: number }> {
  resource: ListResource<T>
  /** With a title the page has its header; without, the list is a part of a page. */
  title?: string
  description?: ReactNode
  columns: ColumnDef<T, unknown>[]
  /** Columns shown only where a view names them (see QueryTable). */
  defaultHidden?: string[]
  /** The open record — always as the list now has it — as a RecordSheet (the
   *  list is its host: open / close, previous / next, the tab, refresh). */
  renderDetail?: (row: T, close: () => void) => ReactNode
  /** The detail's default width (default lg). */
  detailSize?: RecordSheetSize
  /** Opening a row: by default the detail sheet (when renderDetail is given). */
  onRowClick?: (row: T) => void
  /** A row's detail under it, opened with its ▸ in the list (e.g. an item's lots). */
  expand?: (row: T) => ReactNode | null
  /** Per-row buttons in a last column (a click there never opens the row). */
  rowActions?: (row: T) => ReactNode
  /** Filters the page sets itself (a record's own list): kept, not removable; no views. */
  fixed?: Filter[]
  /** Whether a record asked for from elsewhere (openRecord, a Task Inbox link) opens here. Default:
   *  only a list without `fixed` filters, the resource's own page; one inside another page (e.g. the
   *  orders waiting for delivery) shares the resource and must not take the order page's requests. */
  takesOpenRequests?: boolean
  emptyTitle?: string
  /** Under the empty title: why the list is empty and what to do. */
  emptyText?: string
  /** The page's help (its ? beside the title opens the help panel). */
  help?: HelpId
  searchable?: boolean
  errorMessages?: Record<string, string>
  toolbar?: (ctx: ListContext<T>) => ReactNode
  /** How many first columns stay in place while the table scrolls sideways when the view does not say
   *  (default 1; 0 none). The user changes it from a column's menu ("Lock columns up to here"). */
  defaultLocked?: number
  /** Export CSV in the toolbar: every row of the list as it is filtered and sorted now (not only
   *  this page). Gets the resource's export path; downloads it (the page names the file). */
  onExport?: (path: string) => Promise<unknown> | void
  /** Between the header and the table (e.g. a page-level error). */
  notice?: ReactNode
  /** Poll while something is running, e.g. rows => rows.some(running) ? 3000 : false. */
  refetchInterval?: (rows: T[]) => number | false
  /** The table card's height: 'viewport' (default) fills what is left of the page's view, the status
   *  row always on screen; a number caps it (a list inside another page). */
  fit?: 'viewport' | number
  /** The list's notice scope, when the page around it reports too (CrudPage's import / export); else its own. */
  notices?: NoticeStore
  /** Extra parts that need the list (dialogs, a second sheet). */
  children?: (ctx: ListContext<T> & { open: T | null; setOpen: (row: T | null) => void }) => ReactNode
  /** The one way to make a record (the user's rule, 2026-11-02): the New button
   *  at the end of the toolbar, the form in the standard sheet. `render` gets
   *  `done(created)` — the sheet closes, the list refreshes and the new record's
   *  detail opens — and `cancel`. Never a form on the page itself. */
  create?: ListCreate<T>
}

export interface ListCreate<T> {
  label: string
  permission: string
  /** The sheet's title (default: the label). */
  title?: string
  className?: string
  render: (done: (created?: T) => void, cancel: () => void) => ReactNode
}

/** A list page on the platform's list contract (design §13.15 / §13.19): its
 *  query (keyed by the resource), saved views, the column menus and search,
 *  the open row taken from the refreshed list, and the detail sheet. A new
 *  read-only list is this component plus its columns. */
export function ListPage<T extends { id: number }>(props: ListPageProps<T>) {
  const { resource, fixed = [], columns, rowActions, renderDetail } = props
  const qc = useQueryClient()
  const detailScope = useUnsavedScope()  // the open record's unsaved input: prev / next asks about it
  const view = useListView(resource, { fixed })
  // Search on any field shows when the list has a filterable field the page didn't fix (as QueryTable decides).
  const hasPanel = (useListMeta(resource).data?.fields ?? []).some((f) => f.filterable && !fixed.some((x) => x.field === f.name))
  const { spec } = view
  const [open, setOpenRaw] = useState<T | null>(null)
  // The detail's tab: kept from record to record; a linked one replaces it.
  const [tab, setTab] = useState<string | undefined>(undefined)
  const [opening, setOpening] = useState(0)  // a new open request remounts the sheet
  const [creating, setCreating] = useState(false)
  // Auto-refresh: the view's (saved with it), else what this user chose for the list.
  const [storedAuto, setStoredAuto] = useState(() => storedAutoRefresh(resource.basePath))
  const auto = view.layout.refresh ?? storedAuto
  const setAuto = (seconds: number) => {
    storeAutoRefresh(resource.basePath, seconds)
    setStoredAuto(seconds)
    view.setLayout({ ...view.layout, refresh: seconds || undefined })
  }
  const listKey = [...queryKeys.list(resource), spec]
  const list = useQuery({
    queryKey: listKey,
    queryFn: () => resource.list(spec),
    placeholderData: keepPreviousData,
    enabled: view.ready,
    // Only the page's own polling (something running) is TanStack's interval — an
    // incidental fetch, quiet unless something changed. Auto-refresh is useRefresh's.
    refetchInterval: (q) => props.refetchInterval?.(q.state.data?.items ?? []) || false,
    refetchIntervalInBackground: false,
    // Back to the window: refetch only rows older than 30 s (or a failed load).
    refetchOnWindowFocus: (q) => q.state.status === 'error' || Date.now() - q.state.dataUpdatedAt > 30_000,
  })
  // THE list's notices (its status row) and THE refresh: the button, the auto ticks, what changed, the highlight.
  const [exportCols, setExportCols] = useState<string[]>([])  // the visible columns an export can write (QueryTable)
  const [ownNotices] = useState(createNoticeStore)
  const notices = props.notices ?? ownNotices
  const refresh = useRefresh(list, { queryKey: listKey, compare: compareRows<T>((r) => r.id), auto, setAuto, notices })
  // A new page / sort / filter / view on its way, and a load that failed over rows already shown: in the status row.
  const stale = list.isPlaceholderData && list.isFetching
  useEffect(() => {
    if (stale) notices.notify({ key: 'refresh', tone: 'info', text: 'Updating…', sticky: true })
    else if (!refresh.busy) notices.clear('refresh')
  }, [stale])  // eslint-disable-line react-hooks/exhaustive-deps
  const loadError = list.error && list.data ? list.error : null
  useEffect(() => {
    // The same key as a refresh's: one failure shown, and the next load that works clears it.
    if (loadError) notices.notify({ key: 'refresh', tone: 'error', text: `Could not load the list: ${errorText(loadError, props.errorMessages)}`,
      action: { label: 'Try again', onClick: () => { void refresh.refresh() } } })
  }, [loadError])  // eslint-disable-line react-hooks/exhaustive-deps
  const rows = list.data?.items ?? []
  // A record asked for from elsewhere (a Task Inbox link): fetched by its id
  // when the client can, then opened like a clicked row.
  const asked = useOpenRequest(resource.basePath)
  const takes = props.takesOpenRequests ?? fixed.length === 0
  useEffect(() => {
    if (asked === undefined || !renderDetail || !takes) return
    const req = takeOpenRequest(resource.basePath)
    if (req === undefined) return
    const stub = { id: req.id } as T
    const show = (row: T) => {
      setTab(req.tab)
      setOpening((n) => n + 1)
      setOpenRaw(row)
    }
    ;(resource.get ? resource.get(req.id) : Promise.resolve(stub)).then(show, () => show(stub))
  }, [asked, renderDetail, resource, takes])
  const setOpen = (row: T | null) => setOpenRaw(row)
  // The open record as the list now has it: an action, a re-check or another
  // user may have changed it since it was clicked.
  const current = open ? (rows.find((r) => r.id === open.id) ?? open) : null
  const ctx: ListContext<T> = {
    spec, rows, refresh: () => qc.invalidateQueries({ queryKey: queryKeys.list(resource) }),
  }
  const allColumns: ColumnDef<T, unknown>[] = rowActions
    ? [...columns, {
        id: 'actions', header: 'Actions',
        cell: ({ row }) => <div className="flex gap-1">{rowActions(row.original)}</div>,
      }]
    : columns
  const close = () => setOpen(null)
  const at = current ? rows.findIndex((r) => r.id === current.id) : -1
  const host: RecordSheetHost = {
    open: current !== null, close, tab, onTabChange: setTab, size: props.detailSize,
    prev: at > 0 ? () => whenDiscarded(detailScope, () => setOpen(rows[at - 1])) : undefined,
    next: at >= 0 && at < rows.length - 1 ? () => whenDiscarded(detailScope, () => setOpen(rows[at + 1])) : undefined,
    position: at >= 0 ? `${(spec.page - 1) * spec.pageSize + at + 1} of ${list.data?.total ?? rows.length}` : undefined,
    refresh, auto, autoTick: refresh.tick,
  }
  const created = async (row?: T) => {
    setCreating(false)
    await ctx.refresh()
    if (row && renderDetail) setOpen(row)
  }
  const create = props.create
  const exportCsv = (columns: string[] | undefined) => {
    Promise.resolve(props.onExport!(resource.exportPath!(spec, undefined, columns))).then(() => notices.notify({ key: 'csv', tone: 'success', text: 'CSV exported' }),
      (e: unknown) => notices.notify({ key: 'csv', tone: 'error', text: `Export failed: ${errorText(e, props.errorMessages)}` }))
  }
  const toolbar = (
    <>
      <TourMark help="list.refresh" slot="list.refresh" className="inline-flex"><RefreshControl state={refresh} /></TourMark>
      {props.onExport && resource.exportPath && (
        // The visible columns as the list shows them (the default), or every field of the list's own export.
        <TourMark help="list.export" slot="list.export" className="inline-flex">
          <DropdownMenu label="Export CSV" align="end"
            items={[
              { label: `Visible columns (${exportCols.length})`, onSelect: () => exportCsv(exportCols), disabled: exportCols.length === 0,
                disabledReason: 'No column shown can be exported' },
              { label: 'All fields', onSelect: () => exportCsv(undefined) },
            ]}
            trigger={
              <button type="button" aria-label="Export CSV" className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-neutral-200 bg-white text-neutral-600 hover:text-neutral-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                <Download aria-hidden className="h-4 w-4" />
              </button>
            } />
        </TourMark>
      )}
      {props.toolbar?.(ctx)}
      {create && <ActionButton size="sm" permission={create.permission} onAction={() => setCreating(true)}>{create.label}</ActionButton>}
    </>
  )

  return (
    <NoticesContext.Provider value={notices}>
    <TourScope id={props.title ?? 'list'}>
    <div className="flex flex-col gap-4">
      {props.title && <PageHeader title={props.title} description={props.description} help={props.help}
        helpSections={[{ title: 'Using lists', ids: listTools({ search: props.searchable ?? true, panel: hasPanel, views: !!view.tableProps.picker, export: !!(props.onExport && resource.exportPath) }) }]} />}
      {props.notice}
      <QueryTable<T>
        ariaLabel={props.title}
        columns={allColumns}
        defaultHidden={props.defaultHidden}
        resource={resource}
        locked={fixed.map((f) => f.field)}
        rows={rows}
        total={list.data?.total ?? 0}
        groups={list.data?.groups}
        groupsTruncated={list.data?.groups_truncated}
        totals={list.data?.totals}
        onExportColumns={setExportCols}
        defaultLocked={props.defaultLocked}
        {...view.tableProps}
        searchable={props.searchable ?? true}
        loading={list.isLoading || !view.ready}
        fetching={list.isFetching}
        stale={list.isPlaceholderData}
        busyNow={refresh.busy}
        highlight={refresh.highlight}
        error={list.data ? undefined : list.error}  // the first load: its error in place of the rows; later: in the status row
        errorMessages={props.errorMessages}
        onRetry={() => { void refresh.refresh() }}
        fit={props.fit ?? 'viewport'}
        statusBar={(pager) => <TableStatusBar store={notices} right={pager} />}
        rowKey={(r) => r.id}
        onRowClick={props.onRowClick ?? (renderDetail ? setOpen : undefined)}
        expand={props.expand}
        emptyTitle={props.emptyTitle}
        emptyText={props.emptyText}
        toolbar={toolbar}
      />
      {renderDetail && current && (
        <RecordSheetHostContext.Provider value={host}>
          <Fragment key={`${opening}:${current.id}`}><UnsavedBoundary scope={detailScope}>{renderDetail(current, close)}</UnsavedBoundary></Fragment>
        </RecordSheetHostContext.Provider>
      )}
      {create && (
        <Sheet open={creating} onOpenChange={setCreating} title={create.title ?? create.label} className={create.className}>
          {creating && create.render(created, () => setCreating(false))}
        </Sheet>
      )}
      {props.children?.({ ...ctx, open: current, setOpen })}
    </div>
    </TourScope>
    </NoticesContext.Provider>
  )
}
