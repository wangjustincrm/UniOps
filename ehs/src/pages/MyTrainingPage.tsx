/**
 * What I have been trained on, and the certificates for it.
 *
 * ★ This page exists because the certificate did not have a way in. The
 * endpoint that issues one lets the person it belongs to fetch it, but every
 * training screen is behind `ehs.training.read`, which an ordinary employee
 * does not hold — so the document was generated for somebody who could not
 * reach it. That is the same shape as the permit signatures a supervisor could
 * write and not read back, and it is the third time this module has built
 * something correct and unreachable.
 *
 * Deliberately not the register with a filter on it: the register is a
 * different screen for a different job, and the permission that guards it is
 * the reason this page is needed.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Award, CalendarDays, ChevronRight, GraduationCap, Printer, Sheet } from 'lucide-react'
import { Pagination, SortTh, sortParams, useSort } from '@uniops/shell'
import { api, useFileOpener } from '@/lib/api'
import MaterialsList from '@/components/MaterialsList'
import { DateRangeFilter, useDateFilter } from '@/components/ListView'
import {
  Card, Empty, LoadFailedNote, PageHeader, secondaryButton, SectionTitle, Tabs,
} from '@/components/ui'
import { formatDate } from '@/lib/formatDate'
import { daysOverdue } from '@/lib/formatDate'

interface Record_ {
  id: string
  course_label: string
  completed_on: string
  expires_on: string | null
  delivery: string
  certificate_file_id: string | null
  user_name: string
}

interface RunningSession {
  id: string; delivery_no: string; title: string; session_date: string
  start_time: string | null; status: string; enrolled: number
}

const TABS = [
  { key: 'mine', label: 'My training' },
  { key: 'records', label: 'Records' },
] as const
type TabKey = (typeof TABS)[number]['key']

export default function MyTrainingPage() {
  const [tab, setTab] = useState<TabKey>('mine')
  // ★ Round 6, TC-16: whoever HSE names to run a session is usually an ordinary
  // employee, and the only list of sessions was the Training screen, which they
  // cannot open. Their own sessions are listed here instead, where they already
  // come for their own training.
  const running = useQuery({
    queryKey: ['course-sessions', 'mine'],
    queryFn: () => api.get<RunningSession[]>('/api/v1/course-sessions?mine=true'),
  })
  const records = useQuery({
    queryKey: ['my-training'],
    queryFn: () => api.get<Record_[]>('/api/v1/training/records/mine'),
  })
  const opener = useFileOpener()

  const rows = records.data ?? []

  return (
    <div className="p-4">
      <PageHeader
        title="My training"
        subtitle="What you have completed, and the certificate for each."
      />
      {/* ★ Round 12, TRN-02: "provide a Records tab with similar functionality
          as the records tab that the HSE role has" — search, print, export to
          Excel, and every certificate at once — for your own records only. */}
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === 'records' ? <MyRecordsTab /> : <>

      {running.isError && <LoadFailedNote what="The sessions you run" />}
      {(running.data ?? []).length > 0 && (
        <section className="mb-5">
          <SectionTitle className="mb-2">Sessions you are running</SectionTitle>
          <ul className="flex flex-col gap-2">
            {(running.data ?? []).map((s) => (
              <li key={s.id}>
                <Link to={`/training/sessions/${s.id}`}
                      className="flex items-center gap-3 rounded-xl border border-neutral-200 bg-white p-3 hover:border-primary-300">
                  <CalendarDays className="h-4 w-4 shrink-0 text-primary-600" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">{s.title}</span>
                    <span className="block text-xs text-neutral-500">
                      {s.delivery_no} · {formatDate(s.session_date)}
                      {s.start_time && ` · ${s.start_time.slice(0, 5)}`} · {s.enrolled} on the roster
                      {s.status !== 'scheduled' && ` · ${s.status === 'completed' ? 'closed' : s.status}`}
                    </span>
                  </span>
                  <ChevronRight className="h-4 w-4 shrink-0 text-neutral-300" aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {records.isError && <LoadFailedNote what="Your training record" />}
      {opener.error && (
        <p className="mb-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {opener.error}
        </p>
      )}

      {!records.isLoading && rows.length === 0 && !records.isError && (
        <Empty>
          Nothing is on your training record yet. It fills in as you complete
          courses — ask HSE if you think something is missing.
        </Empty>
      )}

      <ul className="flex flex-col gap-2">
        {rows.map((r) => {
          const lapsed = r.expires_on ? (daysOverdue(r.expires_on) ?? 0) > 0 : false
          return (
            <li key={r.id}>
              <Card>
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <GraduationCap className="h-4 w-4 shrink-0 text-primary-600" aria-hidden />
                  <span className="min-w-0 flex-1 text-sm font-semibold text-neutral-900">
                    {r.course_label}
                  </span>
                  {lapsed && (
                    <span className="rounded-full bg-danger-50 px-2.5 py-0.5 text-xs font-semibold text-danger-700">
                      Expired
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-neutral-500">
                  Completed {formatDate(r.completed_on)}
                  {r.expires_on && ` · ${lapsed ? 'expired' : 'valid until'} ${formatDate(r.expires_on)}`}
                  {r.delivery === 'external' && ' · taken elsewhere'}
                </p>
                <div className="mt-2">
                  {r.delivery === 'external' ? (
                    // ★ Since round 9 the certificate HSE files for an outside
                    // course is one of the record's files, not certificate_file_id
                    // (kept for records entered before). The reviewer found the
                    // page still read only the old field: "No certificate on file".
                    <div className="flex flex-col gap-1">
                      {r.certificate_file_id && (
                        <button type="button" disabled={opener.busy}
                                onClick={() => opener.open(
                                  `/api/v1/training/records/${r.id}/certificate`,
                                  `certificate-${r.course_label}.pdf`)}
                                className="self-start text-xs font-semibold text-primary-700 hover:underline disabled:opacity-40">
                          {opener.busy ? 'Opening…' : 'Open the certificate you gave us'}
                        </button>
                      )}
                      <MaterialsList base={`/api/v1/training/records/${r.id}/materials`}
                                     mayWrite={false} what="this record"
                                     emptyText={r.certificate_file_id ? undefined : 'No certificate on file for this one.'} />
                    </div>
                  ) : (
                    <button type="button" disabled={opener.busy}
                            onClick={() => opener.open(
                              `/api/v1/training/records/${r.id}/certificate/issued`,
                              `certificate-${r.course_label}.pdf`)}
                            className="text-xs font-semibold text-primary-700 hover:underline disabled:opacity-40">
                      {opener.busy ? 'Opening…' : 'Certificate'}
                    </button>
                  )}
                </div>
              </Card>
            </li>
          )
        })}
      </ul>
      </>}
    </div>
  )
}

interface MyRecord extends Record_ { provider: string | null }
type SortKey = 'course_label' | 'completed_on' | 'expires_on' | 'delivery'
const TH = 'px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500'

function MyRecordsTab() {
  const [search, setSearch] = useState('')
  const [type, setType] = useState<'' | 'internal' | 'external'>('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [sort, onSort] = useSort<SortKey>(null, () => setPage(1))
  const dateFilter = useDateFilter(() => setPage(1))
  const { from, to } = dateFilter.range

  // One query string for the list, the print, the spreadsheet and the
  // certificates, so the four cannot disagree about what was asked for.
  const filters = new URLSearchParams()
  if (search.trim()) filters.set('search', search.trim())
  if (from) filters.set('completed_from', from)
  if (to) filters.set('completed_to', to)
  if (type) filters.set('delivery', type)
  const qs = filters.toString()

  // ★ Review C7: paged and sorted on the server, as every list is.
  const records = useQuery({
    queryKey: ['my-training', 'records', qs, sort, page, pageSize],
    queryFn: () => {
      const all = new URLSearchParams(filters)
      Object.entries(sortParams(sort)).forEach(([k, v]) => all.set(k, String(v)))
      all.set('page', String(page))
      all.set('page_size', String(pageSize))
      return api.get<{ items: MyRecord[]; total: number }>(`/api/v1/training/records/mine/page?${all}`)
    },
    placeholderData: (prev) => prev,
  })
  const shown = records.data?.items ?? []
  const total = records.data?.total ?? 0
  const printer = useFileOpener()
  const exporter = useFileOpener()
  const certs = useFileOpener()
  const opener = useFileOpener()
  const error = printer.error || exporter.error || certs.error || opener.error

  return (
    <>
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center">
        <input className="h-10 min-w-0 flex-1 rounded-lg border border-neutral-200 px-3 text-sm"
               value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }}
               placeholder="Search by course" aria-label="Search my training records" />
        <select className="h-10 rounded-lg border border-neutral-200 px-2 text-sm" value={type}
                onChange={(e) => { setType(e.target.value as typeof type); setPage(1) }}
                aria-label="Type">
          <option value="">Any type</option>
          <option value="internal">Taken here</option>
          <option value="external">Taken elsewhere</option>
        </select>
        <DateRangeFilter {...dateFilter} />
      </div>

      <div className="mb-3 flex flex-wrap justify-end gap-2">
        <button type="button" className={secondaryButton} disabled={printer.busy}
                onClick={() => printer.open(`/api/v1/training/records/mine/print${qs ? `?${qs}` : ''}`,
                                            'my-training-records.pdf')}>
          <Printer className="h-4 w-4" aria-hidden />
          {printer.busy ? 'Preparing…' : 'Print this list'}
        </button>
        <button type="button" className={secondaryButton} disabled={exporter.busy}
                onClick={() => exporter.open(`/api/v1/training/records/mine/export${qs ? `?${qs}` : ''}`,
                                             'my-training-records.xlsx')}>
          <Sheet className="h-4 w-4" aria-hidden />
          {exporter.busy ? 'Preparing…' : 'Export to Excel'}
        </button>
        <button type="button" className={secondaryButton} disabled={certs.busy || total === 0}
                onClick={() => certs.open(`/api/v1/training/records/mine/certificates${qs ? `?${qs}` : ''}`,
                                          'my-certificates.pdf')}>
          <Award className="h-4 w-4" aria-hidden />
          {certs.busy ? 'Preparing…' : 'Print all certificates'}
        </button>
      </div>
      <p className="mb-2 text-xs text-neutral-500">
        Certificates for training taken here print one to a page; training
        taken elsewhere is listed at the end, since its certificate is the
        provider&rsquo;s.
      </p>
      {error && (
        <p className="mb-3 rounded-lg bg-danger-50 px-3 py-2 text-xs text-danger-700">{error}</p>
      )}
      {records.isError && <LoadFailedNote what="Your training records" />}

      <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
        <div className="max-h-[calc(100vh-18rem)] overflow-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="sticky top-0 z-10 bg-neutral-50 shadow-[0_1px_0_#e5e5e5]">
              <tr>
                <SortTh label="Course" sortKey="course_label" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Completed" sortKey="completed_on" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Expires" sortKey="expires_on" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Type" sortKey="delivery" sort={sort} onSort={onSort} className={TH} />
                <th className={TH}>Certificate</th>
              </tr>
            </thead>
            <tbody>
              {records.isPending && (
                <tr><td colSpan={5} className="p-6 text-center text-sm text-neutral-500">Loading…</td></tr>
              )}
              {records.data && total === 0 && (
                <tr><td colSpan={5} className="p-8 text-center text-sm text-neutral-500">
                  {qs ? 'Nothing matches.' : 'Nothing is on your training record yet.'}
                </td></tr>
              )}
              {shown.map((r) => {
                const lapsed = r.expires_on ? (daysOverdue(r.expires_on) ?? 0) > 0 : false
                return (
                  <tr key={r.id} className="border-t border-neutral-100">
                    <td className="px-3 py-2 font-medium text-neutral-900">{r.course_label}</td>
                    <td className="px-3 py-2 tabular-nums">{formatDate(r.completed_on)}</td>
                    <td className={`px-3 py-2 tabular-nums ${lapsed ? 'font-semibold text-danger-700' : ''}`}>
                      {r.expires_on ? `${formatDate(r.expires_on)}${lapsed ? ' · expired' : ''}` : 'Does not expire'}
                    </td>
                    <td className="px-3 py-2 text-xs text-neutral-600">
                      {r.delivery === 'external' ? `Elsewhere${r.provider ? ` · ${r.provider}` : ''}` : 'Here'}
                    </td>
                    <td className="px-3 py-2">
                      {r.delivery === 'external' ? (
                        r.certificate_file_id ? (
                          <button type="button" disabled={opener.busy}
                                  onClick={() => opener.open(`/api/v1/training/records/${r.id}/certificate`,
                                                             `certificate-${r.course_label}.pdf`)}
                                  className="text-xs font-semibold text-primary-700 hover:underline disabled:opacity-40">
                            Open
                          </button>
                        ) : <span className="text-xs text-neutral-400">On the My training tab</span>
                      ) : (
                        <button type="button" disabled={opener.busy}
                                onClick={() => opener.open(`/api/v1/training/records/${r.id}/certificate/issued`,
                                                           `certificate-${r.course_label}.pdf`)}
                                className="text-xs font-semibold text-primary-700 hover:underline disabled:opacity-40">
                          Print
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage}
                    onPageSizeChange={(n) => { setPageSize(n); setPage(1) }} />
      </div>
    </>
  )
}
