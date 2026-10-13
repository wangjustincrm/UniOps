/**
 * The incident register, ordered by what is running out rather than by date.
 *
 * Serves two audiences from one page, because the endpoint does: with
 * `ehs.incident.read` it returns every incident, and without it, only the ones
 * you filed. The heading and the empty state say which list you are looking at
 * — "no incidents at the plant" and "you have not filed one" are very
 * different statements to put in front of a worker.
 */
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { usePermissions } from '@/hooks/usePermissions'
import { formatDateTime } from '@/lib/formatDate'
import type { IncidentListItem } from '@/lib/types'
import { InjuryBadge, MolBadge, StatusBadge } from '@/components/StatusBadge'
import { Pagination, SearchBox, useListView, DateRangeFilter, useDateFilter } from '@/components/ListView'

const KIND_LABELS: Record<string, string> = {
  medical: 'Injury',
  equipment: 'Equipment',
  near_miss: 'Near miss',
}

/** What a dashboard link may narrow this list by, in the server's own words. */
const DRILL_KEYS = [
  'injury_class', 'injured_only', 'recordable_only', 'form_kind',
  'department_id', 'no_department', 'body_part_label', 'location_id',
] as const

export default function IncidentListPage() {
  const { can } = usePermissions()
  const isRegister = can('ehs.incident.read')
  // ★ Above the early returns. A hook below one runs on some renders and not
  // others, which is what took the dashboard down.
  //
  // ★ The date range goes to the server, not to a filter over what arrived.
  // This endpoint answers with the fifty most recent incidents; a range
  // applied here would be a range over those fifty, so "March 2025" would come
  // back empty and read as a March with no incidents. This register is what an
  // inspector is shown.
  // ★ A figure on the dashboard opens this page with the filter it was counted
  // from. The parameters are passed to the server untouched rather than
  // re-derived here: the drill-down is only worth anything if the rows are the
  // same ones the number counted, and two places deciding what "recordable"
  // means is how they stop agreeing.
  const [search] = useSearchParams()
  const drill = DRILL_KEYS
    .flatMap((k) => search.getAll(k).map((v) => `${k}=${encodeURIComponent(v)}`))
  const cameFrom = search.get('via')
  // A figure counts a whole year; asked for the default fifty the list would
  // show the newest of them and the two would not agree.
  const limit = drill.length ? 200 : 50

  const dateFilter = useDateFilter(undefined, {
    from: search.get('occurred_from'), to: search.get('occurred_to'),
  })
  const { from, to } = dateFilter.range
  // ★ Round 6, box 1: "allow the incident register list to be sorted by
  // status." Asked of the server, so it orders the register rather than the
  // fifty rows that happened to arrive.
  const [order, setOrder] = useState<'newest' | 'oldest' | 'status'>('newest')
  const params = [
    ...drill,
    from ? `occurred_from=${from}` : '',
    to ? `occurred_to=${to}` : '',
    `limit=${limit}`,
    order !== 'newest' ? `order=${order}` : '',
  ].filter(Boolean).join('&')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['incidents', params],
    queryFn: () => api.get<IncidentListItem[]>(
      `/api/v1/incidents${params ? `?${params}` : ''}`),
    // The previous range's rows stay on screen while the new ones are fetched.
    // Without this the page falls back to "Loading…", taking the date inputs
    // with it — which closes the picker halfway through typing a year.
    placeholderData: keepPreviousData,
  })

  const list = useListView(data, (i) => [
    i.incident_no, i.title, i.location_path, i.status, KIND_LABELS[i.form_kind],
  ], { resetKey: params })

  // Only the first load takes the whole page; a range change keeps what is
  // already on screen (see `keepPreviousData` above).
  if (isLoading && !data) {
    return <p className="p-4 text-sm text-neutral-500">Loading incidents…</p>
  }
  if (isError) return <p className="p-4 text-sm text-danger-700">{(error as Error).message}</p>

  const incidents = data ?? []
  const heading = isRegister ? 'Incidents' : 'My Reports'
  // Say when the list came back full, against what was actually asked for —
  // a note that names fifty while two hundred were requested is worse than
  // none, and it would fire on every drill-down of more than fifty records.
  const capped = incidents.length >= limit

  // ★ The empty state keeps the filter bar. It used to return early, which was
  // harmless when nothing could narrow the list — now a range that matches
  // nothing would take away the control needed to widen it again.
  const filtering = dateFilter.preset !== 'all'

  return (
    <div className="p-4">
      <h1 className="mb-3 text-base font-semibold text-neutral-900">{heading}</h1>

      {/* What this list has been narrowed to, and the way back out. A filter
          that is doing something but says nothing is how somebody reads a
          drilled-down register as the whole register. */}
      {drill.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg bg-primary-50 px-3 py-2">
          <p className="min-w-0 flex-1 text-xs text-primary-900">
            Showing {cameFrom ?? 'a selection from the dashboard'} — {incidents.length}
            {incidents.length === 1 ? ' record' : ' records'}.
          </p>
          <Link to="/incidents"
                className="shrink-0 text-xs font-semibold text-primary-700 underline">
            Show the whole register
          </Link>
        </div>
      )}

      <SearchBox value={list.query} onChange={list.setQuery}
                 placeholder="Search by number, title, area or status">
        <DateRangeFilter {...dateFilter} />
        <select aria-label="Sort" value={order}
                onChange={(e) => setOrder(e.target.value as typeof order)}
                className="h-10 rounded-lg border border-neutral-200 bg-white px-2 text-sm">
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="status">By status — open first</option>
        </select>
      </SearchBox>

      {incidents.length === 0 && (
        <p className="p-10 text-center text-sm text-neutral-500">
          {drill.length > 0
            ? `Nothing matches ${cameFrom ?? 'that selection'}.`
            : filtering
            ? `No incidents in this date range — showing ${dateFilter.label.toLowerCase().replace('…', '')}.`
            : isRegister
              ? 'No incidents recorded yet.'
              : 'You have not reported an incident yet.'}
        </p>
      )}

      <div className="flex flex-col gap-2">
      {list.pageRows.map((i) => (
        <Link
          key={i.id}
          to={`/incidents/${i.id}`}
          className="block rounded-xl border border-neutral-200 bg-white p-3.5 transition-colors hover:border-primary-300 hover:bg-primary-50/40"
        >
          <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold text-neutral-900">{i.title}</h2>
              <p className="mt-0.5 text-xs text-neutral-500">
                {i.incident_no} · {KIND_LABELS[i.form_kind] ?? i.form_kind}
                {i.location_path ? ` · ${i.location_path}` : ''}
                {i.occurred_at ? ` · ${formatDateTime(i.occurred_at)}` : ''}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <InjuryBadge injuryClass={i.injury_class} />
              <MolBadge reportable={i.mol_reportable} />
              <StatusBadge status={i.status} />
            </div>
          </div>
        </Link>
      ))}
      </div>

      <Pagination page={list.page} pageCount={list.pageCount} total={list.total}
                  onPage={list.setPage} capped={capped}
                  loaded={incidents.length} noun="incidents" />
    </div>
  )
}
