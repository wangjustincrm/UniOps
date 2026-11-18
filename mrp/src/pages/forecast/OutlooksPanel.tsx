// The Outlooks panel on the Sales Forecast page — the list of confirmed
// ForecastVersion snapshots, with a read-only viewer and a delete per row.
//
// Extracted from SalesForecastPage once the list needed paging, search and
// delete of its own: three pieces of state and two mutations that have
// nothing to do with the forecast grid, in a file that was already 1,400
// lines. Everything the panel needs it fetches itself.
//
// ★ Its query key is NOT the shared ['forecast-versions'] key. That key is
// read by ProductionPlanPage's forecast-version picker, which needs the
// whole list; this panel asks for one page of ten, filtered to `confirmed`
// and possibly searched. Serving both from one cache entry would hand the
// picker whatever page this panel happened to be looking at. The two are
// invalidated TOGETHER instead — see `invalidateOutlookLists`, which
// SalesForecastPage also calls after generating an outlook.
import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronLeft, ChevronRight, Eye, Search, Trash2, X } from 'lucide-react'
import { Button, SortTh, sortParams, useSort, Input, TBody, THead, Table, Td, Th, Tr, Card } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { forecastApi, type ForecastVersion, type ListVersionsOptions } from './forecastApi'

const PAGE_SIZE = 10

/** Refresh both views of the outlook list: this panel's paged query and
 *  ProductionPlanPage's ['forecast-versions'] picker. Production Plan lives
 *  in the keep-alive multi-tab shell — its component and query stay mounted
 *  indefinitely once visited, so without an explicit invalidation a newly
 *  generated (or deleted) outlook never reaches its picker until a hard
 *  reload. */
export async function invalidateOutlookLists(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['outlooks'] }),
    queryClient.invalidateQueries({ queryKey: ['forecast-versions'] }),
  ])
}

// The "Created" column is a full local date+time (formatDateTime), since two
// outlooks frozen the same day (a redo, say) are otherwise indistinguishable.

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

export function OutlooksPanel({
  canWrite, onView, notifySuccess, notifyError,
}: {
  /** mrp.demand.write — the permission that freezes an outlook, and so the
   *  one that may discard it. */
  canWrite: boolean
  onView: (version: ForecastVersion) => void
  notifySuccess: (message: string) => void
  notifyError: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<ForecastVersion | null>(null)
  // Sorted by the API (the list is paged); a new sort starts at page 1.
  const [sort, onSort] = useSort<NonNullable<ListVersionsOptions['sort']>>(null, () => setPage(1))

  // Debounced: the box filters a server-side query, and one request per
  // keystroke would have the list flickering through half-typed answers.
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput)
      setPage(1) // a page 4 that exists unfiltered usually does not exist filtered
    }, 300)
    return () => clearTimeout(timer)
  }, [searchInput])

  const listQuery = useQuery({
    queryKey: ['outlooks', page, search, sort],
    queryFn: () => forecastApi.listVersions(page, PAGE_SIZE, { status: 'confirmed', search, ...sortParams(sort) }),
    // Keeps the previous page on screen while the next one loads, instead of
    // collapsing the panel to "Loading…" and shifting everything below it.
    placeholderData: keepPreviousData,
  })

  const items = useMemo(() => listQuery.data?.items ?? [], [listQuery.data])
  const total = listQuery.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  // Deleting the last row of the last page leaves you on a page that no
  // longer exists, which renders as an empty list and reads as "everything
  // is gone".
  useEffect(() => {
    if (!listQuery.isFetching && page > 1 && items.length === 0 && total > 0) {
      setPage((p) => Math.min(p - 1, pageCount))
    }
  }, [listQuery.isFetching, page, items.length, total, pageCount])

  const deleteMutation = useMutation({
    mutationFn: (version: ForecastVersion) => forecastApi.deleteVersion(version.id),
    onSuccess: async (_result, version) => {
      await invalidateOutlookLists(queryClient)
      notifySuccess(`Outlook ${version.version_no} deleted.`)
      setDeleteTarget(null)
    },
    // 409 = a production plan was generated from it, and the message names
    // the plan. Passing it through beats "could not delete", which leaves
    // the planner with nothing to act on.
    onError: (err) => notifyError(errMsg(err, 'Could not delete this outlook.')),
  })

  const from = (page - 1) * PAGE_SIZE + 1
  const to = Math.min(page * PAGE_SIZE, total)

  return (
    <Card inset className="p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-fg">Outlooks</h2>
        <div className="flex items-center gap-2">
          <div className="flex h-8 items-center gap-1.5 rounded-md border border-line-strong px-2">
            <Search aria-hidden className="h-3.5 w-3.5 shrink-0 text-fg-muted" />
            <Input inputSize="sm"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search version, anchor or note…"
              aria-label="Search outlooks"
              className="w-48"
            />
            {searchInput && (
              <Button variant="subtle" size="icon-xs" type="button" onClick={() => setSearchInput('')} aria-label="Clear search"
                className="shrink-0">
                <X className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
          {!listQuery.isLoading && (
            <span className="text-2xs text-fg-muted">
              {search ? `${total} match` : `${total} confirmed`}
            </span>
          )}
        </div>
      </div>

      {listQuery.isLoading ? (
        <p role="status" className="py-4 text-center text-xs text-fg-muted">Loading outlooks…</p>
      ) : listQuery.isError ? (
        <p role="alert" className="rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-xs text-danger-fg">
          {errMsg(listQuery.error, 'Could not load outlooks.')}
        </p>
      ) : items.length === 0 ? (
        <p className="py-4 text-center text-xs text-fg-muted">
          {search
            // An empty search result and an empty list look identical, and
            // reading the second as the first is how somebody concludes
            // their outlooks were lost.
            ? `No outlook matches "${search}".`
            : 'No outlooks generated yet — use Generate Outlook above to freeze one for Production Plan.'}
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <Table density="compact" className="min-w-full">
              <THead>
                <Tr className="text-left text-2xs font-semibold text-fg-muted">
                  <SortTh label="Version" sortKey="version_no" sort={sort} onSort={onSort} className="px-2 py-1.5" />
                  <SortTh label="Anchor" sortKey="source_anchor_month" sort={sort} onSort={onSort} className="px-2 py-1.5" />
                  <SortTh label="Horizon" sortKey="horizon_months" sort={sort} onSort={onSort} className="px-2 py-1.5" />
                  <SortTh label="Created" sortKey="created_at" sort={sort} onSort={onSort} className="px-2 py-1.5" />
                  <Th />
                </Tr>
              </THead>
              <TBody>
                {items.map((v) => (
                  <Tr key={v.id}>
                    <Td className="font-medium text-fg">{v.version_no}</Td>
                    <Td className="text-fg-secondary">{v.source_anchor_month ?? '—'}</Td>
                    <Td className="text-fg-secondary">{v.horizon_months} mo</Td>
                    <Td className="text-fg-muted">{formatDateTime(v.created_at)}</Td>
                    <Td>
                      <div className="flex items-center justify-end gap-1">
                        <Button type="button" size="sm" variant="secondary" onClick={() => onView(v)}>
                          <Eye className="h-3.5 w-3.5" /> View
                        </Button>
                        {canWrite && (
                          <Button variant="danger-ghost" size="icon-sm"
                            type="button"
                            onClick={() => setDeleteTarget(v)}
                            aria-label={`Delete outlook ${v.version_no}`}
                            title="Delete"
                           
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        )}
                      </div>
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>

          {/* The pager stays visible on a single page too, because it also
              says WHICH rows these are: "1–10 of 17" is the only thing that
              distinguishes a short list from a first page. */}
          <div className="mt-2 flex items-center justify-between gap-2 border-t border-line-subtle pt-2">
            <span className="text-2xs text-fg-muted">
              {from}–{to} of {total}
            </span>
            <div className="flex items-center gap-1">
              <Button variant="subtle" size="icon-sm"
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || listQuery.isFetching}
                aria-label="Previous page"
               
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="text-2xs text-fg-muted">Page {page} of {pageCount}</span>
              <Button variant="subtle" size="icon-sm"
                type="button"
                onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                disabled={page >= pageCount || listQuery.isFetching}
                aria-label="Next page"
               
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </>
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="Delete this outlook?"
          confirmLabel="Delete"
          danger
          busy={deleteMutation.isPending}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => deleteMutation.mutate(deleteTarget)}
        >
          <p>
            <strong>{deleteTarget.version_no}</strong> (anchor{' '}
            {deleteTarget.source_anchor_month ?? '—'}, frozen{' '}
            {formatDateTime(deleteTarget.created_at)}) and its frozen quantities will be
            removed. This cannot be undone.
          </p>
          <p className="flex items-start gap-1.5 text-xs text-fg-muted">
            <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              The forecast itself is not affected — an outlook is a frozen copy of it. An
              outlook a production plan was generated from cannot be deleted; you will be told
              which plan.
            </span>
          </p>
        </ConfirmDialog>
      )}
    </Card>
  )
}
