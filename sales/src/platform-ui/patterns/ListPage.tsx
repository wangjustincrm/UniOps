import { useState, type ReactNode } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnDef } from '../primitives/DataTable'
import { PageHeader } from '../primitives/PageHeader'
import { Sheet } from '../primitives/Sheet'
import type { ListMeta } from '../resource/listMeta'
import { queryKeys } from '../resource/queryKeys'
import type { Filter, Page, QuerySpec } from '../resource/querySpec'
import { QueryTable } from './QueryTable'
import { useListView } from './views'

/** What a list needs from its resource client. */
export interface ListResource<T> {
  basePath: string
  list: (spec: QuerySpec) => Promise<Page<T>>
  meta: () => Promise<ListMeta>
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
  /** The open record — always as the list now has it — in a sheet. */
  renderDetail?: (row: T, close: () => void) => ReactNode
  detailTitle?: (row: T) => string
  detailClassName?: string
  /** Opening a row: by default the detail sheet (when renderDetail is given). */
  onRowClick?: (row: T) => void
  /** Per-row buttons in a last column (a click there never opens the row). */
  rowActions?: (row: T) => ReactNode
  /** Filters the page sets itself (a record's own list): kept, not removable; no views. */
  fixed?: Filter[]
  emptyTitle?: string
  searchable?: boolean
  errorMessages?: Record<string, string>
  toolbar?: (ctx: ListContext<T>) => ReactNode
  /** Between the header and the table (e.g. a page-level error). */
  notice?: ReactNode
  /** Poll while something is running, e.g. rows => rows.some(running) ? 3000 : false. */
  refetchInterval?: (rows: T[]) => number | false
  /** Extra parts that need the list (dialogs, a second sheet). */
  children?: (ctx: ListContext<T> & { open: T | null; setOpen: (row: T | null) => void }) => ReactNode
}

/** A list page on the platform's list contract (design §13.15 / §13.19): its
 *  query (keyed by the resource), saved views, the column menus and search,
 *  the open row taken from the refreshed list, and the detail sheet. A new
 *  read-only list is this component plus its columns. */
export function ListPage<T extends { id: number }>(props: ListPageProps<T>) {
  const { resource, fixed = [], columns, rowActions, renderDetail } = props
  const qc = useQueryClient()
  const view = useListView(resource, { fixed })
  const { spec } = view
  const [open, setOpen] = useState<T | null>(null)
  const list = useQuery({
    queryKey: [...queryKeys.list(resource), spec],
    queryFn: () => resource.list(spec),
    placeholderData: keepPreviousData,
    enabled: view.ready,
    refetchInterval: props.refetchInterval ? (q) => props.refetchInterval!(q.state.data?.items ?? []) : undefined,
  })
  const rows = list.data?.items ?? []
  // The open record as the list now has it: an action, a re-check or another
  // user may have changed it since it was clicked.
  const current = open ? (rows.find((r) => r.id === open.id) ?? open) : null
  const ctx: ListContext<T> = {
    spec, rows, refresh: () => qc.invalidateQueries({ queryKey: queryKeys.list(resource) }),
  }
  const allColumns: ColumnDef<T, unknown>[] = rowActions
    ? [...columns, {
        id: 'actions', header: 'Actions',
        cell: ({ row }) => <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>{rowActions(row.original)}</div>,
      }]
    : columns
  const close = () => setOpen(null)

  return (
    <div className="flex flex-col gap-4">
      {props.title && <PageHeader title={props.title} description={props.description} />}
      {props.notice}
      <QueryTable<T>
        columns={allColumns}
        resource={resource}
        locked={fixed.map((f) => f.field)}
        rows={rows}
        total={list.data?.total ?? 0}
        groups={list.data?.groups}
        groupsTruncated={list.data?.groups_truncated}
        {...view.tableProps}
        searchable={props.searchable ?? true}
        loading={list.isLoading || !view.ready}
        error={list.error}
        errorMessages={props.errorMessages}
        onRetry={() => list.refetch()}
        rowKey={(r) => r.id}
        onRowClick={props.onRowClick ?? (renderDetail ? setOpen : undefined)}
        emptyTitle={props.emptyTitle}
        toolbar={props.toolbar?.(ctx)}
      />
      {renderDetail && (
        <Sheet open={current !== null} onOpenChange={(o) => { if (!o) close() }}
          title={current ? (props.detailTitle?.(current) ?? `#${current.id}`) : ''} className={props.detailClassName}>
          {current && renderDetail(current, close)}
        </Sheet>
      )}
      {props.children?.({ ...ctx, open: current, setOpen })}
    </div>
  )
}
