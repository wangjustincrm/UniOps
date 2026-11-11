/**
 * ★ Round 12, TBT-01/02 — HSE's month-by-month view of the toolbox talks
 * handed to each person, and a way to chase the ones still outstanding.
 *
 * Kirk: "provide a means for the HSE role to track on a monthly basis the
 * toolbox talks assigned to each person and whether or not they have completed
 * them … for toolbox talks that are not complete or still waiting to be
 * delivered, provide a way to resend them as a reminder." Q15: "each person is
 * the person the talk has been assigned to, not every worker" — so the rows are
 * the people the talks went to, and nothing new is recorded to make them.
 *
 * Sorted and paged on the server, like every list here (the 10-29 rule).
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pagination, SortTh, sortParams, useSort } from '@uniops/shell'
import { api } from '@/lib/api'
import { formatDate, todayForInput } from '@/lib/formatDate'
import { ErrorNote, LoadFailedNote, SectionTitle } from '@/components/ui'
import { SearchBox } from '@/components/ListView'

interface TrackerDelivery {
  id: string; talk_no: string; title: string; department_name: string | null
  status: string; held_at: string | null; last_reminded_on: string | null
}
interface TrackerRow {
  person_id: string | null; person_name: string | null
  assigned: number; delivered: number; outstanding: number
  last_reminded_on: string | null; deliveries: TrackerDelivery[]
}
interface TrackerPage {
  month: string; items: TrackerRow[]; total: number
  assigned: number; delivered: number; outstanding: number
}
type SortKey = 'person_name' | 'assigned' | 'delivered' | 'outstanding' | 'last_reminded_on'

const TH = 'px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500'

/** "2026-11" for the month input, from a plain date — no `new Date` on a
 *  date-only string (it shifts a day west of UTC). */
const monthOf = (ymd: string) => ymd.slice(0, 7)

export default function ToolboxTracker() {
  const queryClient = useQueryClient()
  const [month, setMonth] = useState(() => monthOf(todayForInput()))
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [sort, onSort] = useSort<SortKey>(null, () => setPage(1))
  const [note, setNote] = useState<string | null>(null)
  const day = `${month}-01`

  const tracker = useQuery({
    queryKey: ['tbt-tracker', day, search.trim(), page, pageSize, sort],
    queryFn: () => {
      const qs = new URLSearchParams({ month: day, page: String(page),
                                       page_size: String(pageSize), ...sortParams(sort) })
      if (search.trim()) qs.set('q', search.trim())
      return api.get<TrackerPage>(`/api/v1/toolbox-talks/tracker?${qs}`)
    },
    placeholderData: (prev) => prev,
    enabled: /^\d{4}-\d{2}$/.test(month),
  })
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['tbt-tracker'] })
  }

  const remindOne = useMutation({
    mutationFn: (deliveryId: string) =>
      api.post<{ reminded: number }>(`/api/v1/toolbox-talks/deliveries/${deliveryId}/remind`, {}),
    onSuccess: () => { setNote('Reminder sent.'); refresh() },
  })
  const remindAll = useMutation({
    mutationFn: () => api.post<{ reminded: number }>(
      `/api/v1/toolbox-talks/remind-all?month=${day}`, {}),
    onSuccess: (r) => {
      setNote(r.reminded === 0 ? 'Nothing outstanding to chase.'
        : `Reminder sent for ${r.reminded} talk${r.reminded === 1 ? '' : 's'}.`)
      refresh()
    },
  })
  const busy = remindOne.isPending || remindAll.isPending
  const data = tracker.data

  return (
    <section className="mb-6" aria-label="Monthly tracker">
      <SectionTitle className="mb-1">Monthly tracker</SectionTitle>
      <p className="mb-2 max-w-[72ch] text-xs text-neutral-500">
        Everyone a talk was handed to in the month, and whether they have
        delivered it. A reminder is the same email as the fortnightly one, sent
        now; the fortnight then counts from today.
      </p>

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-neutral-700">
          Month
          <input type="month" value={month} aria-label="Month"
                 className="h-9 rounded-lg border border-neutral-300 px-2 text-sm"
                 onChange={(e) => { setMonth(e.target.value); setPage(1); setNote(null) }} />
        </label>
        {data && (
          <span className="text-xs tabular-nums text-neutral-600">
            {data.assigned} handed out · {data.delivered} delivered ·{' '}
            <span className={data.outstanding > 0 ? 'font-semibold text-danger-700' : ''}>
              {data.outstanding} outstanding
            </span>
          </span>
        )}
        <span className="flex-1" />
        <button type="button"
                disabled={busy || !data || data.outstanding === 0}
                onClick={() => {
                  if (busy) return
                  if (window.confirm(`Send a reminder for every talk of ${month} still outstanding?`)) {
                    setNote(null)
                    remindAll.mutate()
                  }
                }}
                className="min-h-[36px] rounded-lg border border-neutral-300 bg-white px-3 text-xs font-semibold text-neutral-800 hover:bg-neutral-50 disabled:opacity-40">
          {remindAll.isPending ? 'Sending…' : 'Remind everyone outstanding'}
        </button>
      </div>

      <SearchBox value={search} onChange={(v) => { setSearch(v); setPage(1) }}
                 placeholder="Search by person" />
      {note && <p className="mb-2 text-xs text-success-700">{note}</p>}
      <ErrorNote error={remindOne.error || remindAll.error} />
      {tracker.isError && <LoadFailedNote what="The tracker" />}

      <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
        <div className="max-h-[60vh] overflow-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="sticky top-0 z-10 bg-neutral-50 shadow-[0_1px_0_#e5e5e5]">
              <tr>
                <SortTh label="Person" sortKey="person_name" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Handed out" sortKey="assigned" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Delivered" sortKey="delivered" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Outstanding" sortKey="outstanding" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Last reminded" sortKey="last_reminded_on" sort={sort} onSort={onSort} className={TH} />
                <th className={TH}>Talks</th>
              </tr>
            </thead>
            <tbody>
              {tracker.isPending && (
                <tr><td colSpan={6} className="p-6 text-center text-sm text-neutral-500">Loading…</td></tr>
              )}
              {data && data.items.length === 0 && (
                <tr>
                  <td colSpan={6} className="p-8 text-center text-sm text-neutral-500">
                    {search.trim() ? 'Nobody matches.' : 'No talks were handed out that month.'}
                  </td>
                </tr>
              )}
              {data?.items.map((r) => (
                <tr key={r.person_id ?? 'nobody'} className="border-t border-neutral-100 align-top">
                  <td className="px-3 py-2 font-medium text-neutral-900">{r.person_name ?? 'Nobody named'}</td>
                  <td className="px-3 py-2 tabular-nums">{r.assigned}</td>
                  <td className="px-3 py-2 tabular-nums">{r.delivered}</td>
                  <td className={`px-3 py-2 tabular-nums ${r.outstanding > 0 ? 'font-semibold text-danger-700' : ''}`}>
                    {r.outstanding}
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-600">
                    {r.last_reminded_on ? formatDate(r.last_reminded_on) : '—'}
                  </td>
                  <td className="px-3 py-2">
                    <ul className="flex flex-col gap-1">
                      {r.deliveries.map((d) => (
                        <li key={d.id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
                          <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                            d.status === 'delivered' ? 'bg-success-50 text-success-700'
                              : 'bg-warning-50 text-warning-700'}`}>
                            {d.status === 'delivered' ? 'Delivered' : 'Outstanding'}
                          </span>
                          <span className="text-neutral-800">{d.title}</span>
                          <span className="text-neutral-400">
                            {d.department_name ?? ''}
                            {d.held_at ? ` · held ${formatDate(d.held_at)}` : ''}
                          </span>
                          {d.status === 'pending' && (
                            <button type="button" disabled={busy}
                                    onClick={() => { if (!busy) { setNote(null); remindOne.mutate(d.id) } }}
                                    className="min-h-[28px] rounded px-1.5 font-semibold text-primary-700 hover:bg-primary-50 disabled:opacity-40">
                              Remind
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pagination page={page} pageSize={pageSize} total={data?.total ?? 0} onPageChange={setPage}
                    onPageSizeChange={(n) => { setPageSize(n); setPage(1) }} />
      </div>
    </section>
  )
}
