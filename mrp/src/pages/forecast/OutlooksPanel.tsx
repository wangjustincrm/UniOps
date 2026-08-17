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
import { Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { forecastApi, type ForecastVersion } from './forecastApi'

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

/** The "Created" column — a full local date+time, since two outlooks frozen
 *  the same day (a redo, say) are otherwise indistinguishable. `created_at`
 *  is a real timestamp, so `new Date` is right here; the date-ONLY columns
 *  elsewhere in MRP must not go through it (see formatDate's UTC note). An
 *  unparseable value falls back to the raw string rather than rendering
 *  "Invalid Date". */
function formatDateTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString('en-US', {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      })
}

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
    queryKey: ['outlooks', page, search],
    queryFn: () => forecastApi.listVersions(page, PAGE_SIZE, { status: 'confirmed', search }),
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
    <div className="rounded-lg border border-neutral-200 bg-white p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-neutral-900">Outlooks</h2>
        <div className="flex items-center gap-2">
          <div className="flex h-8 items-center gap-1.5 rounded-md border border-neutral-300 px-2">
            <Search aria-hidden className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search version, anchor or note…"
              aria-label="Search outlooks"
              className="w-48 text-xs focus:outline-none"
            />
            {searchInput && (
              <button type="button" onClick={() => setSearchInput('')} aria-label="Clear search"
                className="shrink-0 text-neutral-400 hover:text-neutral-700">
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          {!listQuery.isLoading && (
            <span className="text-[11px] text-neutral-400">
              {search ? `${total} match` : `${total} confirmed`}
            </span>
          )}
        </div>
      </div>

      {listQuery.isLoading ? (
        <p role="status" className="py-4 text-center text-xs text-neutral-400">Loading outlooks…</p>
      ) : listQuery.isError ? (
        <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          {errMsg(listQuery.error, 'Could not load outlooks.')}
        </p>
      ) : items.length === 0 ? (
        <p className="py-4 text-center text-xs text-neutral-400">
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
            <table className="min-w-full text-xs">
              <thead>
                <tr className="border-b border-neutral-100 text-left text-[11px] font-semibold text-neutral-500">
                  <th className="px-2 py-1.5">Version</th>
                  <th className="px-2 py-1.5">Anchor</th>
                  <th className="px-2 py-1.5">Horizon</th>
                  <th className="px-2 py-1.5">Created</th>
                  <th className="px-2 py-1.5" />
                </tr>
              </thead>
              <tbody>
                {items.map((v) => (
                  <tr key={v.id} className="border-b border-neutral-50 last:border-0">
                    <td className="px-2 py-1.5 font-medium text-neutral-800">{v.version_no}</td>
                    <td className="px-2 py-1.5 text-neutral-600">{v.source_anchor_month ?? '—'}</td>
                    <td className="px-2 py-1.5 text-neutral-600">{v.horizon_months} mo</td>
                    <td className="px-2 py-1.5 text-neutral-500">{formatDateTime(v.created_at)}</td>
                    <td className="px-2 py-1.5">
                      <div className="flex items-center justify-end gap-1">
                        <Button type="button" size="sm" variant="secondary" onClick={() => onView(v)}>
                          <Eye className="h-3.5 w-3.5" /> View
                        </Button>
                        {canWrite && (
                          <button
                            type="button"
                            onClick={() => setDeleteTarget(v)}
                            aria-label={`Delete outlook ${v.version_no}`}
                            title="Delete"
                            className="rounded p-1.5 text-neutral-400 hover:bg-danger-50 hover:text-danger-600"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* The pager stays visible on a single page too, because it also
              says WHICH rows these are: "1–10 of 17" is the only thing that
              distinguishes a short list from a first page. */}
          <div className="mt-2 flex items-center justify-between gap-2 border-t border-neutral-100 pt-2">
            <span className="text-[11px] text-neutral-500">
              {from}–{to} of {total}
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || listQuery.isFetching}
                aria-label="Previous page"
                className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <span className="text-[11px] text-neutral-500">Page {page} of {pageCount}</span>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                disabled={page >= pageCount || listQuery.isFetching}
                aria-label="Next page"
                className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
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
          <p className="flex items-start gap-1.5 text-xs text-neutral-500">
            <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              The forecast itself is not affected — an outlook is a frozen copy of it. An
              outlook a production plan was generated from cannot be deleted; you will be told
              which plan.
            </span>
          </p>
        </ConfirmDialog>
      )}
    </div>
  )
}
