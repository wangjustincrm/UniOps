/**
 * The forms someone can fill in, and the ones already filled.
 *
 * Two tabs rather than two pages: starting an inspection and checking whether
 * last month's was done are the same errand, and a supervisor doing one
 * usually wants to see the other.
 */
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ClipboardList, Plus } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { Pagination, SearchBox, useListView, DateRangeFilter } from '@/components/ListView'
import { formatDateTime } from '@/lib/formatDate'
import { LocationSelect } from '@/components/LocationSelect'
import { StatusBadge } from '@/components/StatusBadge'

interface Template {
  id: string
  code: string
  name: string
  description: string | null
  category: string
}

interface Run {
  id: string
  template_id: string
  due_on: string
  department_name: string | null
  status: string
  submission_id: string | null
}

interface SubmissionItem {
  id: string
  submission_no: string
  template_id: string
  status: string
  occurred_at: string | null
  location_path: string | null
  submitted_by_name: string | null
  failed_count: number
}

type Tab = 'due' | 'available' | 'done'

export default function FormsListPage({ initialTab = 'due' as Tab }) {
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>(initialTab)
  const [starting, setStarting] = useState<Template | null>(null)

  const templates = useQuery({
    queryKey: ['form-templates'],
    queryFn: () => api.get<Template[]>('/api/v1/forms/templates'),
  })
  // Overdue and upcoming inspections. Ordered by due date server-side, so the
  // late ones are simply first.
  // ★ The limits are asked for explicitly, and at the endpoint's maximum. The
  // defaults are 100 due runs and 50 submissions, which the date filter would
  // quietly turn into a lie: a range over rows that were never fetched finds
  // nothing and looks like a month in which nothing was filled in. The pager
  // says so when a list comes back full.
  const RUNS_LIMIT = 300
  const SUBMISSIONS_LIMIT = 200

  const runs = useQuery({
    queryKey: ['form-runs'],
    queryFn: () => api.get<Run[]>(
      `/api/v1/forms/runs?status=pending&status=missed&limit=${RUNS_LIMIT}`),
  })

  const startRun = useMutation({
    mutationFn: (runId: string) =>
      api.post<{ id: string }>(`/api/v1/forms/runs/${runId}/start`, {}),
    onSuccess: (sub) => navigate(`/forms/submissions/${sub.id}`),
  })

  const submissions = useQuery({
    queryKey: ['form-submissions'],
    queryFn: () => api.get<SubmissionItem[]>(
      `/api/v1/forms/submissions?limit=${SUBMISSIONS_LIMIT}`),
  })
  // Two lists on this page grow: what is owed and what has been filed. The
  // template list is a handful of forms and stays one screen.
  const dueList = useListView(runs.data, (r) => [
    r.department_name, r.status, r.due_on,
  ], { dateOf: (r) => r.due_on })
  const filedList = useListView(submissions.data, (x) => [
    x.submission_no, x.location_path, x.submitted_by_name, x.status,
  ], { dateOf: (x) => x.occurred_at })

  const names = new Map((templates.data ?? []).map((t) => [t.id, t.name]))

  const tabCls = (on: boolean) =>
    `min-h-[40px] flex-1 rounded-md px-3 text-sm font-medium ${
      on ? 'bg-white text-primary-700 shadow-sm ring-1 ring-neutral-200' : 'text-neutral-600'}`

  return (
    <div className="p-4">
      <div className="mb-4 flex gap-1 rounded-lg bg-neutral-100 p-1">
        <button type="button" className={tabCls(tab === 'due')}
                onClick={() => setTab('due')}>
          Due
          {(runs.data?.length ?? 0) > 0 && (
            <span className="ml-1.5 rounded-full bg-danger-100 px-1.5 text-[11px] font-semibold text-danger-700">
              {runs.data!.length}
            </span>
          )}
        </button>
        <button type="button" className={tabCls(tab === 'available')}
                onClick={() => setTab('available')}>Start a form</button>
        <button type="button" className={tabCls(tab === 'done')}
                onClick={() => setTab('done')}>Completed</button>
      </div>

      {tab === 'due' && (
        <>
          <SearchBox value={dueList.query} onChange={dueList.setQuery}
                     placeholder="Search by department or due date">
            <DateRangeFilter {...dueList.dateFilter} />
          </SearchBox>

          {runs.data?.length === 0 && (
            <p className="p-8 text-center text-sm text-neutral-500">
              Nothing scheduled is outstanding.
            </p>
          )}
          <ul className="flex flex-col gap-2">
            {dueList.pageRows.map((r) => {
              const late = r.status === 'missed'
              return (
                <li key={r.id}>
                  <button
                    type="button"
                    disabled={startRun.isPending}
                    onClick={() => { if (!startRun.isPending) startRun.mutate(r.id) }}
                    className={[
                      'flex w-full items-center gap-3 rounded-xl border bg-white p-3.5 text-left transition-colors',
                      late ? 'border-danger-200 hover:border-danger-300'
                           : 'border-neutral-200 hover:border-primary-300',
                    ].join(' ')}
                  >
                    <ClipboardList className={`h-5 w-5 shrink-0 ${late ? 'text-danger-600' : 'text-primary-600'}`}
                                   aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-semibold text-neutral-900">
                        {names.get(r.template_id) ?? 'Inspection'}
                        {r.department_name && (
                          <span className="font-normal text-neutral-500"> — {r.department_name}</span>
                        )}
                      </span>
                      <span className={`mt-0.5 block text-xs ${late ? 'font-medium text-danger-700' : 'text-neutral-500'}`}>
                        {late ? 'Overdue since ' : 'Due '}{r.due_on}
                        {r.submission_id ? ' · started' : ''}
                      </span>
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>

          <Pagination page={dueList.page} pageCount={dueList.pageCount}
                      total={dueList.total} onPage={dueList.setPage} noun="due forms"
                      capped={(runs.data ?? []).length >= RUNS_LIMIT}
                      loaded={(runs.data ?? []).length} />
        </>
      )}

      {tab === 'available' && (
        <>
          {templates.isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
          {templates.data?.length === 0 && (
            <p className="rounded-lg border border-dashed border-neutral-300 p-6 text-center text-sm text-neutral-500">
              No forms have been published yet. HSE creates them under Settings.
            </p>
          )}
          <ul className="flex flex-col gap-2">
            {templates.data?.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => setStarting(t)}
                  className="flex w-full items-center gap-3 rounded-xl border border-neutral-200 bg-white p-3.5 text-left transition-colors hover:border-primary-300 hover:bg-primary-50/40"
                >
                  <ClipboardList className="h-5 w-5 shrink-0 text-primary-600" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">{t.name}</span>
                    {t.description && (
                      <span className="mt-0.5 block text-xs text-neutral-500">{t.description}</span>
                    )}
                  </span>
                  <Plus className="h-4 w-4 shrink-0 text-neutral-400" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {tab === 'done' && (
        <>
          <SearchBox value={filedList.query} onChange={filedList.setQuery}
                     placeholder="Search by number, area or who filed it">
            <DateRangeFilter {...filedList.dateFilter} />
          </SearchBox>

          {submissions.data?.length === 0 && (
            <p className="p-8 text-center text-sm text-neutral-500">Nothing filled in yet.</p>
          )}
          <ul className="flex flex-col gap-2">
            {filedList.pageRows.map((s) => (
              <li key={s.id}>
                <Link to={`/forms/submissions/${s.id}`}
                      className="block rounded-xl border border-neutral-200 bg-white p-3.5 transition-colors hover:border-primary-300 hover:bg-primary-50/40">
                  <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5">
                    <span className="min-w-0 flex-1 text-sm font-semibold text-neutral-900">
                      {names.get(s.template_id) ?? 'Form'}
                    </span>
                    <StatusBadge status={s.status} />
                    {s.failed_count > 0 && (
                      <span className="rounded-full bg-danger-50 px-2.5 py-0.5 text-xs font-semibold text-danger-700">
                        {s.failed_count} failed
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-neutral-500">
                    {s.submission_no}
                    {s.location_path ? ` · ${s.location_path.split('/').slice(1).join(' › ')}` : ''}
                    {s.occurred_at ? ` · ${formatDateTime(s.occurred_at)}` : ''}
                    {s.submitted_by_name ? ` · ${s.submitted_by_name}` : ''}
                  </p>
                </Link>
              </li>
            ))}
          </ul>

          <Pagination page={filedList.page} pageCount={filedList.pageCount}
                      total={filedList.total} onPage={filedList.setPage} noun="submissions"
                      capped={(submissions.data ?? []).length >= SUBMISSIONS_LIMIT}
                      loaded={(submissions.data ?? []).length} />
        </>
      )}

      {starting && (
        <StartDialog template={starting} onClose={() => setStarting(null)}
                     onStarted={(id) => navigate(`/forms/submissions/${id}`)} />
      )}
    </div>
  )
}

/** Where, before what. The area is asked once here rather than as a question on
 *  every template, so it lands in the column reports group by. */
function StartDialog({ template, onClose, onStarted }: {
  template: Template
  onClose: () => void
  onStarted: (id: string) => void
}) {
  const [locationId, setLocationId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const start = useMutation({
    mutationFn: () => api.post<{ id: string }>(
      `/api/v1/forms/templates/${template.id}/submissions`, { location_id: locationId }),
    onSuccess: (created) => onStarted(created.id),
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
      <div className="w-full rounded-t-2xl bg-white p-4 sm:max-w-md sm:rounded-2xl">
        <h2 className="text-base font-bold text-neutral-900">{template.name}</h2>
        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">
          Which area?
        </label>
        <LocationSelect value={locationId} onChange={(id) => setLocationId(id)} />

        {error && (
          <p className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>
        )}

        <div className="mt-4 flex gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 text-sm font-medium text-neutral-700">
            Cancel
          </button>
          <button type="button" disabled={start.isPending}
                  onClick={() => { if (!start.isPending) { setError(null); start.mutate() } }}
                  className="min-h-[44px] flex-[1.4] rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50">
            {start.isPending ? 'Starting…' : 'Start'}
          </button>
        </div>
      </div>
    </div>
  )
}
