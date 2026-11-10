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
  // Round 8, TC-08: the register's own figures open the incidents behind them.
  'status', 'has_root_causes', 'has_actions', 'actions_state',
  // Round 12, TC-05: investigations not signed off.
  'investigation_outstanding',
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
  // ★ Round 8: "only the 50 most recent were loaded; older incidents are not in
  // this list. how do we see all incidents?" The whole register is read, a page
  // of the server's at a time, so the search, the sort and the pages here work
  // over every incident rather than the newest fifty. A plant's register is a
  // few hundred a year; MAX_ROWS is a backstop, said on screen if ever reached.
  const PAGE = 200
  const MAX_ROWS = 5000

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
    order !== 'newest' ? `order=${order}` : '',
  ].filter(Boolean).join('&')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['incidents', params],
    queryFn: async () => {
      const rows: IncidentListItem[] = []
      for (let offset = 0; offset < MAX_ROWS; offset += PAGE) {
        const page = await api.get<IncidentListItem[]>(
          `/api/v1/incidents?${[params, `limit=${PAGE}`, `offset=${offset}`].filter(Boolean).join('&')}`)
        rows.push(...page)
        if (page.length < PAGE) break
      }
      return rows
    },
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
  const capped = incidents.length >= MAX_ROWS

  // ★ The empty state keeps the filter bar. It used to return early, which was
  // harmless when nothing could narrow the list — now a range that matches
  // nothing would take away the control needed to widen it again.
  const filtering = dateFilter.preset !== 'all'

  return (
    <div className="p-4">
      <h1 className="mb-3 text-base font-semibold text-neutral-900">{heading}</h1>

      {drill.length === 0 && !filtering && <RegisterFigures />}

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


interface Summary {
  open: number; under_investigation: number; closed: number
  investigations_outstanding?: number
  root_causes: number; corrective_actions: number
  actions_completed?: number; actions_outstanding?: number
}

/**
 * The figures above the register.
 *
 * Round 7, TC-10: "create a dashboard on the incident registry with the
 * following boxes, Open Incidents, number of closed incidents, Incidents under
 * investigation, number of Root causes identified, number of CAPA's recorded."
 *
 * Shown over the whole register only: under a filter they would disagree with
 * the rows beneath them. ★ Never a 0 that is not one — while loading the boxes
 * show a dash, and a failure says so rather than reading as "no incidents".
 */
function RegisterFigures() {
  const summary = useQuery({
    queryKey: ['incident-summary'],
    queryFn: () => api.get<Summary>('/api/v1/incidents/summary'),
  })
  if (summary.isError) {
    return (
      <p className="mb-3 rounded-lg bg-warning-50 px-3 py-2 text-xs text-warning-800">
        The register figures could not be loaded. The list below is unaffected.
      </p>
    )
  }
  const d = summary.data
  // ★ Round 8, TC-08: "make the number in the boxes clickable so that the
  // user, when clicking on the number, is shown the incidents that make up that
  // number." Each opens the register filtered as the box counts — drafts never
  // among them. The two cause and action boxes count those, and open the
  // incidents that have them.
  const SENT = ['submitted', 'under_investigation', 'pending_closure', 'closed']
  const q = (statuses: string[], extra = '') =>
    statuses.map((st) => `status=${st}`).join('&') + extra
  const tiles: [string, number | undefined, string][] = [
    ['Open incidents', d?.open, q(['submitted', 'under_investigation', 'pending_closure'])],
    // ★ Round 12, TC-05 and Q6: "An investigation is outstanding until it is
    // signed off." The status Under investigation is written by nothing on
    // the way in, so that box always read 0.
    ['Investigations not signed off', d?.investigations_outstanding,
     q(['submitted', 'under_investigation', 'pending_closure'], '&investigation_outstanding=true')],
    ['Closed incidents', d?.closed, q(['closed'])],
    ['Root causes identified', d?.root_causes, q(SENT, '&has_root_causes=true')],
    // ★ Round 10, TC-10: "a capa counter tracking all the capa created,
    // completed and outstanding" — each opens the incidents with such an action.
    ['Corrective actions raised', d?.corrective_actions, q(SENT, '&has_actions=true')],
    ['Corrective actions completed', d?.actions_completed, q(SENT, '&actions_state=completed')],
    ['Corrective actions outstanding', d?.actions_outstanding, q(SENT, '&actions_state=outstanding')],
  ]
  return (
    <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7" aria-label="Register figures">
      {tiles.map(([label, n, filter]) => (
        <Link key={label}
              to={`/incidents?${filter}&via=${encodeURIComponent(
                label.startsWith('Root') ? 'incidents with root causes identified'
                  // The box counts actions; the list shows the incidents they
                  // were raised on, which can be fewer — so the line says both.
                  : label.startsWith('Corrective')
                    ? `the incidents with the ${n ?? ''} corrective actions ${label.split(' ').pop()}`
                  : label.toLowerCase())}`}
              className="rounded-lg border border-neutral-200 bg-white px-3 py-2 hover:border-primary-300 hover:bg-primary-50">
          <p className="text-xl font-semibold tabular-nums text-primary-700 underline-offset-2 hover:underline">{n ?? '—'}</p>
          <p className="text-xs text-neutral-500">{label}</p>
        </Link>
      ))}
    </div>
  )
}
