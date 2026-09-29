/**
 * The first-aid register — Regulation 1101.
 *
 * A standing record of every treatment given, most of which never becomes an
 * incident. Entries are written once and never edited, so there is no edit
 * control here by design.
 *
 * ★ Treatment is recorded on the injury form now, not here: two places for the
 * same event meant the same cut finger was entered twice, or once in whichever
 * the person happened to open. The entries written here before are kept as
 * they are — they are the statutory record as it stands — and this list reads
 * both sources, so the register is still the register.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ArrowUpRight, Plus } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { formatAreaPath } from '@/lib/areaPath'
import { Pagination, SearchBox, useListView, DateRangeFilter, useDateFilter } from '@/components/ListView'
import { PageHeader } from '@/components/ui'
import { formatDateTime } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'

interface Entry {
  id: string; log_no: string; incident_id: string | null; occurred_at: string
  location_path: string | null; injured_name: string; first_aider_name: string | null
  body_part_label: string | null; treatment_given: string; sent_offsite: boolean
  /** 'incident' when it was reported on an injury form, 'register' when it was
      entered here before first aid moved onto that form. */
  source: string
}

export default function FirstAidLogPage() {
  const { can } = usePermissions()
  const canWrite = can('ehs.firstaid.write')
  const [escalating, setEscalating] = useState<{ id: string; log_no: string; injured_name: string } | null>(null)

  // ★ The range is a query parameter, not a filter over what arrived. The
  // register is served a hundred rows at a time and is kept for five years;
  // narrowing after the fact would answer "nothing in that month" with the
  // hundred most recent treatments in hand.
  const dateFilter = useDateFilter()
  const { from, to } = dateFilter.range
  const params = [
    from ? `occurred_from=${from}` : '',
    to ? `occurred_to=${to}` : '',
  ].filter(Boolean).join('&')

  const { data, isLoading } = useQuery({
    queryKey: ['first-aid', from, to],
    queryFn: () => api.get<Entry[]>(`/api/v1/first-aid${params ? `?${params}` : ''}`),
    // Keep the rows on screen while the new range loads, so the date inputs
    // are not torn down and rebuilt under the person typing into them.
    placeholderData: keepPreviousData,
  })
  // What the note explains. Cheap, cached, and the only thing on the page that
  // answers "does this register still need to exist".
  const sources = useQuery({
    queryKey: ['first-aid', 'sources'],
    queryFn: () => api.get<{ from_incidents: number; register_only: number }>(
      '/api/v1/first-aid/sources'),
    staleTime: 5 * 60_000,
  })

  const list = useListView(data, (e) => [
    e.log_no, e.injured_name, e.first_aider_name, e.body_part_label,
    e.treatment_given, e.location_path,
  ], { resetKey: `${from}|${to}` })



  return (
    <div className="p-4">
      <PageHeader
        title="First-aid register"
        subtitle="Regulation 1101 — every treatment given, kept for at least five years."
      >
        {canWrite && (
          <Link
            to="/report"
            className="flex min-h-[44px] shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-3.5 text-sm font-semibold text-white"
          >
            <Plus className="h-4 w-4" aria-hidden />
            Record a treatment
          </Link>
        )}
      </PageHeader>

      {/* First aid is recorded on the injury form now. Two places recording
          the same event meant the same cut finger was entered twice, or once
          in whichever the person happened to open.

          ★ The entries below that were written here are kept exactly as they
          are. They are the Regulation 1101 record as it stands, and
          renumbering statutory entries to tidy a screen is not a trade worth
          making — so this list reads both, and only the way in has changed. */}
      {/* ★ With the counts, because "this register is redundant now" has come
          back in three consecutive rounds. It is not redundant while entries
          exist that live nowhere else — and that is a number, not an argument.
          If it ever reaches zero the page can go. */}
      <p className="mb-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
        New treatment is recorded on the injury form — report an incident and
        set its type to First Aid.
        {sources.data && (
          <>
            {' '}This register lists both:{' '}
            <b>{sources.data.from_incidents}</b> from injury forms and{' '}
            <b>{sources.data.register_only}</b> entered here before the change.
            {sources.data.register_only > 0 && (
              <> Those {sources.data.register_only} exist nowhere else — they are
              the Regulation 1101 record as it stands, which is why this page
              is still here.</>
            )}
          </>
        )}
      </p>


      <SearchBox value={list.query} onChange={list.setQuery}
                 placeholder="Search by number, who was treated, body part or treatment">
        <DateRangeFilter {...dateFilter} />
      </SearchBox>

      {isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
      {data?.length === 0 && (
        <p className="p-8 text-center text-sm text-neutral-500">
          {dateFilter.preset === 'all'
            ? 'No treatments recorded yet.'
            : 'No treatments in this date range.'}
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {list.pageRows.map((e) => (
          <li key={e.id} className="rounded-xl border border-neutral-200 bg-white p-3.5">
            <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
              <span className="font-mono text-xs text-neutral-500">{e.log_no}</span>
              <span className="min-w-0 flex-1 text-sm font-semibold text-neutral-900">
                {e.injured_name}
                {e.body_part_label && (
                  <span className="ml-1.5 font-normal text-neutral-500">{e.body_part_label}</span>
                )}
              </span>
              {/* ★ "Escalated" means a first-aid entry turned out to need more
                  than first aid. It was drawn for anything holding an incident
                  id — which since the merge is every entry reported on an injury
                  form, so eighteen of twenty-four said a treatment had escalated
                  when none of them had. The two are different facts and now read
                  differently. */}
              {e.source === 'incident' ? (
                <Link to={`/incidents/${e.incident_id}`}
                      className="rounded-full bg-neutral-100 px-2.5 py-0.5 text-xs font-semibold text-neutral-600 hover:bg-neutral-200">
                  On an injury form
                </Link>
              ) : e.incident_id ? (
                <Link to={`/incidents/${e.incident_id}`}
                      className="rounded-full bg-warning-50 px-2.5 py-0.5 text-xs font-semibold text-warning-700 hover:bg-warning-100">
                  Escalated
                </Link>
              ) : null}
              {e.sent_offsite && (
                <span className="rounded-full bg-info-50 px-2.5 py-0.5 text-xs font-semibold text-info-700">
                  Sent off site
                </span>
              )}
            </div>
            <p className="mt-1.5 text-sm text-neutral-700">{e.treatment_given}</p>
            {/* ★ Regulation 1101 asks who gave it, by name. The column existed
                on the record and was never drawn. */}
            <p className="mt-0.5 text-xs text-neutral-500">
              {e.first_aider_name
                ? <>Treated by {e.first_aider_name}</>
                : <span className="text-neutral-400">Who treated it was not recorded</span>}
              {e.location_path && <> · {formatAreaPath(e.location_path)}</>}
            </p>
            {/* The common path to a WSIB obligation: first aid at the time, a
                clinic two days later. Offered from the entry because that entry
                is usually the only contemporaneous record of when it happened. */}
            {e.source === 'register' && !e.incident_id && canWrite && (
              <button
                type="button"
                onClick={() => setEscalating({ id: e.id, log_no: e.log_no, injured_name: e.injured_name })}
                className="mt-2 flex min-h-[36px] items-center gap-1.5 text-xs font-medium text-primary-700"
              >
                <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
                This became more than first aid
              </button>
            )}
            <p className="mt-1 text-xs text-neutral-400">
              {formatDateTime(e.occurred_at)}
              {e.location_path ? ` · ${e.location_path}` : ''}
              {e.first_aider_name ? ` · ${e.first_aider_name}` : ''}
            </p>
          </li>
        ))}
      </ul>

      <Pagination page={list.page} pageCount={list.pageCount} total={list.total}
                  onPage={list.setPage} noun="entries" />

      {escalating && (
        <EscalateDialog entry={escalating} onClose={() => setEscalating(null)} />
      )}
    </div>
  )
}

/** Local "now" as a datetime-local value — the employer-aware instant defaults
 *  to when someone is doing this, which is usually right. */
function localNow(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
         `T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * Turning a first-aid entry into an incident.
 *
 * The usual path to a WSIB obligation: treatment that looked like first aid at
 * the time, and the worker goes to a clinic two days later. The register entry
 * is often the only contemporaneous record of when it happened.
 *
 * The date asked for here is deliberately NOT the treatment date. WSIB's three
 * business days run from when the employer learned there was an obligation, and
 * that is today, not the day of the cut. Getting these two confused starts the
 * clock in the past and makes a report look late that is not.
 */
function EscalateDialog({ entry, onClose }: {
  entry: { id: string; log_no: string; injured_name: string }
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [title, setTitle] = useState(`${entry.injured_name} — treatment became medical aid`)
  const [description, setDescription] = useState('')
  const [awareAt, setAwareAt] = useState(localNow())
  const [error, setError] = useState<string | null>(null)

  const escalate = useMutation({
    mutationFn: () => api.post<{ id: string; incident_no: string }>(
      `/api/v1/first-aid/${entry.id}/escalate`, {
        title,
        description: description || null,
        // Sent with the browser's offset so the clock starts from a real
        // instant rather than a floating local time.
        employer_aware_at: new Date(awareAt).toISOString(),
      }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['first-aid'] })
      onClose()
      navigate(`/incidents/${created.id}`)
    },
    onError: (e: unknown) => setError(e instanceof ApiError ? e.message : String(e)),
  })

  const field = 'min-h-[44px] w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm ' +
                'focus:border-primary-400 focus:outline-none'

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
      <div className="max-h-[92vh] w-full overflow-y-auto rounded-t-2xl bg-white p-4 sm:max-w-md sm:rounded-2xl">
        <h2 className="text-base font-bold text-neutral-900">Raise an incident</h2>
        <p className="mt-0.5 text-xs text-neutral-500">From {entry.log_no}</p>

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">What happened</label>
        <input className={field} value={title} onChange={(e) => setTitle(e.target.value)} />

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">Detail</label>
        <textarea rows={3} className={`${field} py-2`} value={description}
                  onChange={(e) => setDescription(e.target.value)} />

        <label className="mb-1 mt-3 block text-sm font-medium text-neutral-800">
          When did we learn it was more than first aid?
        </label>
        <p className="mb-1.5 text-xs text-neutral-500">
          Not the date of the treatment. WSIB&rsquo;s three business days run from this
          moment, so putting the treatment date here starts the clock in the past.
        </p>
        <input type="datetime-local" className={field} value={awareAt}
               onChange={(e) => setAwareAt(e.target.value)} />

        {error && (
          <p className="mt-3 rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>
        )}

        <div className="mt-4 flex gap-2">
          <button type="button" onClick={onClose}
                  className="min-h-[44px] flex-1 rounded-lg border border-neutral-300 text-sm font-medium text-neutral-700">
            Cancel
          </button>
          <button type="button" disabled={!title.trim() || escalate.isPending}
                  onClick={() => { if (title.trim() && !escalate.isPending) { setError(null); escalate.mutate() } }}
                  className="min-h-[44px] flex-[1.4] rounded-lg bg-primary-600 text-sm font-semibold text-white disabled:opacity-50">
            {escalate.isPending ? 'Raising…' : 'Raise incident'}
          </button>
        </div>
      </div>
    </div>
  )
}
