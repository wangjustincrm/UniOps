/**
 * Training: who is compliant, what is being run, and what is on file.
 *
 * Three tabs because three different questions get asked of this data and they
 * have different audiences. Compliance is what a Ministry inspector asks for.
 * Sessions is what a supervisor lives in. Records is where a certificate that
 * arrived on paper gets typed in.
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Award, Pencil, Plus, Printer, Sheet, Upload, X, Paperclip } from 'lucide-react'
import MaterialsList from '@/components/MaterialsList'
import { api, apiBlob, useFileOpener } from '@/lib/api'
import { Pagination, useListView, DateRangeFilter, useDateFilter } from '@/components/ListView'
import { Pagination as ShellPagination, SortTh, sortParams, useSort } from '@uniops/shell'
import { CoursePicker } from '@/components/CoursePicker'
import { formatDate, todayForInput } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import {
  Card, Empty, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, Tabs,
} from '@/components/ui'
import TrainingGapsPanel from '@/pages/TrainingGapsPage'

interface Course {
  id: string; code: string; name: string; is_statutory: boolean
  applies_to_all: boolean; validity_months: number | null; is_active: boolean
  material_filename: string | null
}
interface QuizSummary {
  id: string; title: string; course_id: string | null; question_count: number; marked_count: number
}
/** What the quiz's own page already computes, shown where the question is asked. */
interface QuizResults {
  people: number; passed: number; average_score: number | null; total_attempts: number
}
interface Session {
  id: string; delivery_no: string; course_label: string; title: string
  session_date: string; start_time: string | null; location_text: string | null
  coordinator_name: string | null; instructor_name: string | null
  status: string; enrolled: number; passed: number
}
interface Record_ {
  id: string; user_name: string; course_label: string; completed_on: string
  expires_on: string | null; delivery: string; recorded_by_name: string | null
  certificate_file_id: string | null; provider?: string | null
}

const TABS = [
  { key: 'compliance', label: 'Compliance' },
  { key: 'courses', label: 'Courses' },
  { key: 'sessions', label: 'Course sessions' },
  { key: 'records', label: 'Records' },
] as const
type TabKey = (typeof TABS)[number]['key']

export default function TrainingPage() {
  // The first-aid cover panel can only tell somebody that cover is short; the
  // thing that fixes it is a training record, which lives here. `?record=CODE`
  // is how that panel hands the job over — it opens this page on the records
  // tab with the form already on the right course, instead of naming a course
  // and leaving the reader to find it.
  const [params, setParams] = useSearchParams()
  const recordCourse = params.get('record')
  const [tab, setTab] = useState<TabKey>(recordCourse ? 'records' : 'compliance')
  // Round 9: a figure on the compliance tab opens the records behind it.
  const [expiry, setExpiry] = useState<ExpiryWindow | null>(null)
  const { can } = usePermissions()
  const mayWrite = can('ehs.training.write')

  return (
    <div className="p-4">
      <PageHeader title="Training" />
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === 'compliance' && (
        <>
          <ExpiryFigures onOpen={(w) => { setExpiry(w); setTab('records') }} />
          <TrainingGapsPanel />
        </>
      )}
      {tab === 'courses' && <CoursesTab mayWrite={mayWrite} />}
      {tab === 'sessions' && <SessionsTab mayWrite={mayWrite} />}
      {tab === 'records' && (
        <RecordsTab
          mayWrite={mayWrite}
          expiry={expiry}
          onClearExpiry={() => setExpiry(null)}
          openForCourse={recordCourse}
          onConsumed={() => setParams({}, { replace: true })}
        />
      )}
    </div>
  )
}

// ── Expiry figures (round 9) ─────────────────────────────────────────────────

/**
 * ★ Round 9: "on the compliance screen, create a dashboard listing the total
 * number of trainings that are going to expire in the next year, in 6 months,
 * in 30 days and expired. Use the course records as the data source. Like
 * always, each graph shall allow the user to click on the number and be shown
 * the records that make up that value." Each window includes the shorter ones;
 * the server counts what its own list returns for the same window.
 */
type ExpiryWindow = 'expired' | '30d' | '6m' | '1y' | 'valid'
const EXPIRY_LABELS: Record<ExpiryWindow, string> = {
  // Round 12, TRN-03: "… first aid training that is not expired."
  valid: 'Not expired',
  expired: 'Expired',
  '30d': 'Expiring within 30 days',
  '6m': 'Expiring within 6 months',
  '1y': 'Expiring within a year',
}
interface ExpirySummary {
  expired: number; within_30_days: number; within_6_months: number; within_a_year: number
}

function ExpiryFigures({ onOpen }: { onOpen: (w: ExpiryWindow) => void }) {
  const summary = useQuery({
    queryKey: ['training-records', 'expiry-summary'],
    queryFn: () => api.get<ExpirySummary>('/api/v1/training/records/expiry-summary'),
  })
  const d = summary.data
  const tiles: [ExpiryWindow, number | undefined, string][] = [
    ['1y', d?.within_a_year, 'text-primary-700'],
    ['6m', d?.within_6_months, 'text-primary-700'],
    ['30d', d?.within_30_days, 'text-warning-700'],
    ['expired', d?.expired, 'text-danger-700'],
  ]
  return (
    <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Training expiry figures">
      {tiles.map(([w, n, tone]) => (
        <button key={w} type="button" onClick={() => onOpen(w)}
                className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-left hover:border-primary-300 hover:bg-primary-50">
          <span className={`block text-xl font-semibold tabular-nums ${tone}`}>{n ?? '—'}</span>
          <span className="text-xs text-neutral-500">{EXPIRY_LABELS[w]}</span>
        </button>
      ))}
    </div>
  )
}

// ── Course sessions ──────────────────────────────────────────────────────────

const TH = 'px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500'
type SessionSort = 'delivery_no' | 'title' | 'session_date' | 'status'

function SessionsTab({ mayWrite }: { mayWrite: boolean }) {
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)

  // ★ Round 12, TRN-04: "the same search options as on the training
  // compliance page … by title, date or number". ★ Review C7: one table under
  // the list standard — searched, sorted and paged on the server, so it reaches
  // past the hundred most recent sessions.
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [sort, onSort] = useSort<SessionSort>(null, () => setPage(1))
  const dateFilter = useDateFilter(() => setPage(1))
  const { from, to } = dateFilter.range
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize), ...sortParams(sort) })
  if (search.trim()) qs.set('search', search.trim())
  if (from) qs.set('date_from', from)
  if (to) qs.set('date_to', to)

  const sessions = useQuery({
    queryKey: ['course-sessions', 'page', qs.toString()],
    queryFn: () => api.get<{ items: Session[]; total: number }>(`/api/v1/course-sessions/page?${qs}`),
    placeholderData: (prev) => prev,
  })
  const rows = sessions.data?.items ?? []
  const filtered = Boolean(search.trim() || from || to)

  return (
    <>
      {mayWrite && (
        <div className="mb-3 flex justify-end">
          <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            Schedule a session
          </button>
        </div>
      )}

      {creating && <NewSessionForm onClose={() => setCreating(false)} />}

      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center">
        <input className="h-10 min-w-0 flex-1 rounded-lg border border-neutral-200 px-3 text-sm"
               value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }}
               placeholder="Search by session number or title" aria-label="Search course sessions" />
        <DateRangeFilter {...dateFilter} />
      </div>

      <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
        <div className="max-h-[calc(100vh-18rem)] overflow-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="sticky top-0 z-10 bg-neutral-50 shadow-[0_1px_0_#e5e5e5]">
              <tr>
                <SortTh label="No." sortKey="delivery_no" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Session" sortKey="title" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Date" sortKey="session_date" sort={sort} onSort={onSort} className={TH} />
                <th className={TH}>Roster</th>
                <th className={TH}>Run by</th>
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className={TH} />
              </tr>
            </thead>
            <tbody>
              {sessions.isPending && (
                <tr><td colSpan={6} className="p-6 text-center text-sm text-neutral-500">Loading…</td></tr>
              )}
              {sessions.data && rows.length === 0 && (
                <tr><td colSpan={6} className="p-8 text-center text-sm text-neutral-500">
                  {filtered ? 'No session matches.'
                    : 'No course sessions yet. Scheduling one is how a course gets a date, a room, and a register of who attended.'}
                </td></tr>
              )}
              {rows.map((s) => (
                <tr key={s.id} onClick={() => navigate(`/training/sessions/${s.id}`)}
                    className="cursor-pointer border-t border-neutral-100 hover:bg-primary-50/40">
                  <td className="px-3 py-2 font-mono text-xs text-neutral-600">{s.delivery_no}</td>
                  <td className="px-3 py-2">
                    <Link to={`/training/sessions/${s.id}`} onClick={(e) => e.stopPropagation()}
                          className="font-medium text-neutral-900 hover:text-primary-700 hover:underline">
                      {s.title}
                    </Link>
                    {s.location_text && <span className="block text-xs text-neutral-500">{s.location_text}</span>}
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    {formatDate(s.session_date)}{s.start_time && ` ${s.start_time.slice(0, 5)}`}
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-600">
                    {s.enrolled === 0 ? 'Nobody yet' : `${s.enrolled}${s.passed ? `, ${s.passed} passed` : ''}`}
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-600">{s.coordinator_name ?? '—'}</td>
                  <td className="px-3 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                      s.status === 'scheduled' ? 'bg-primary-50 text-primary-700' : 'bg-neutral-100 text-neutral-600'}`}>
                      {s.status === 'scheduled' ? 'Coming up' : s.status === 'completed' ? 'Complete' : 'Cancelled'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ShellPagination page={page} pageSize={pageSize} total={sessions.data?.total ?? 0}
                         onPageChange={setPage} onPageSizeChange={(n) => { setPageSize(n); setPage(1) }} />
      </div>
    </>
  )
}

function NewSessionForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [courseId, setCourseId] = useState('')
  const [title, setTitle] = useState('')
  const [date, setDate] = useState(todayForInput())
  const [start, setStart] = useState('')
  const [where, setWhere] = useState('')
  const [coordinator, setCoordinator] = useState<PersonValue>(EMPTY_PERSON)
  // The roster, built here rather than on a screen you reach afterwards.
  // Scheduling and deciding who is coming are one thought, and splitting them
  // across two screens meant the session existed for a while with nobody in
  // it — which is also the state it stays in if the second screen is never
  // opened.
  const [learners, setLearners] = useState<PersonValue[]>([])
  const [adding, setAdding] = useState<PersonValue>(EMPTY_PERSON)

  const courses = useQuery({
    queryKey: ['courses'],
    queryFn: () => api.get<Course[]>('/api/v1/training/courses'),
  })

  const create = useMutation({
    mutationFn: async () => {
      const session = await api.post<Session>('/api/v1/course-sessions', {
        course_id: courseId,
        title: title || null,
        session_date: date,
        start_time: start ? `${start}:00` : null,
        location_text: where || null,
        coordinator_id: coordinator.userId,
      })
      const ids = learners.map((l) => l.userId).filter((x): x is string => Boolean(x))
      if (ids.length) {
        // Two calls because the session has to exist before anyone can be on
        // it. If this half fails the session is still there to add them to,
        // which is the failure worth having.
        await api.post(`/api/v1/course-sessions/${session.id}/roster`, { user_ids: ids })
      }
      return session
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['course-sessions'] })
      onClose()
    },
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">Schedule a session</h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Course">
          <CoursePicker courses={courses.data ?? []} value={courseId} onChange={setCourseId}
                        loading={courses.isLoading} failed={courses.isError} />
        </Field>
        <Field label="Title" hint="Leave blank to use the course name">
          <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)}
                 placeholder="e.g. WHMIS — refresher" />
        </Field>
        <Field label="Date">
          <input type="date" className={inputClass} value={date}
                 onChange={(e) => setDate(e.target.value)} />
        </Field>
        <Field label="Start time">
          <input type="time" className={inputClass} value={start}
                 onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="Where">
          <input className={inputClass} value={where} onChange={(e) => setWhere(e.target.value)}
                 placeholder="Training Room 107" />
        </Field>
        <Field label="Who runs it"
               hint="This person gets a task to build the roster, and can edit it without being HSE">
          <PersonPicker value={coordinator} onChange={setCoordinator} allowExternal={false}
                        placeholder="Search for a supervisor" />
        </Field>
      </div>

      <div className="mt-3">
        <Field label="Who is coming"
               hint="Optional — more can be added later, and the supervisor running it can add their own">
          <PersonPicker value={adding} onChange={(p) => {
            if (p.userId && !learners.some((l) => l.userId === p.userId)) {
              setLearners([...learners, p])
            }
            setAdding(EMPTY_PERSON)
          }} />
        </Field>
        {learners.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {learners.map((l) => (
              <li key={l.userId ?? l.name}
                  className="flex items-center gap-1.5 rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-600">
                {l.name}
                <button type="button" aria-label={`Remove ${l.name}`}
                        onClick={() => setLearners(learners.filter((x) => x.userId !== l.userId))}
                        className="text-neutral-400 hover:text-neutral-700">
                  <X className="h-3 w-3" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ErrorNote error={create.error} />

      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!courseId || !date || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Scheduling…' : 'Schedule'}
        </button>
      </div>
    </Card>
  )
}

// ── Records ──────────────────────────────────────────────────────────────────

function RecordsTab({ mayWrite, openForCourse, onConsumed, expiry, onClearExpiry }: {
  mayWrite: boolean; openForCourse?: string | null; onConsumed?: () => void
  expiry?: ExpiryWindow | null; onClearExpiry?: () => void
}) {
  // Opened straight from the link, not from the button on this page. The
  // parameter is cleared as it is consumed so that closing the form and
  // reopening the tab does not put it back.
  const [adding, setAdding] = useState(Boolean(openForCourse && mayWrite))
  // "Who has done this course", "what has this person done" and "what happened
  // in March" are three questions of the same list, and it answered none of
  // them on screen.
  const [search, setSearch] = useState('')
  const [order, setOrder] = useState<'date' | 'person' | 'topic'>('date')
  // The same range control the registers use, but the bounds go to the server:
  // this list is filtered in SQL, so a narrow range reaches records older than
  // the page would otherwise have been sent.
  const dateFilter = useDateFilter()
  const { from, to } = dateFilter.range
  // ★ Round 12, TRN-03: status and type are filters of their own here, so a
  // search like "first aid, not expired" can be put together on this tab — and
  // then printed as certificates. A figure opened from Compliance still sets
  // the status; choosing another one here replaces it.
  const [status, setStatus] = useState<ExpiryWindow | ''>('')
  const [type, setType] = useState<'' | 'internal' | 'external'>('')
  const window_ = expiry ?? (status || null)

  const query = [
    search.trim() ? `search=${encodeURIComponent(search.trim())}` : '',
    order !== 'date' ? `order=${order}` : '',
    from ? `completed_from=${from}` : '',
    to ? `completed_to=${to}` : '',
    window_ ? `expiry=${window_}` : '',
    type ? `delivery=${type}` : '',
  ].filter(Boolean).join('&')

  const records = useQuery({
    queryKey: ['training-records', search.trim(), order, from, to, window_ ?? '', type],
    queryFn: () => api.get<Record_[]>(
      `/api/v1/training/records${query ? `?${query}` : ''}`),
  })
  // The search and the range go to the server; only the pager is client side,
  // over what came back. Filtering the same rows again here would narrow a set
  // that was already narrowed, and hide rows for a reason nobody could see.
  const recordPage = useListView(records.data, () => [])

  // ★ Round 4: "create a way to print training records based on the filter
  // created." The same query goes to the server, so what prints is what is on
  // screen — and the page states the filter, because a list of rows with
  // nothing saying what was asked for reads as "these are the trained people".
  // Through the shared opener, which carries its own busy and error state and
  // the pop-up-blocked fallback every other download here goes through.
  const printer = useFileOpener()
  const exporter = useFileOpener()
  const certificates = useFileOpener()

  return (
    <>
      {expiry && (
        <p className="mb-3 flex flex-wrap items-center gap-2 rounded-lg bg-primary-50 px-3 py-2 text-sm text-primary-800">
          Showing records {EXPIRY_LABELS[expiry].toLowerCase()} — each person&rsquo;s latest
          record of a course, people still here.
          <button type="button" onClick={onClearExpiry}
                  className="font-semibold text-primary-700 underline">Show all records</button>
        </p>
      )}
      <div className="mb-3 flex flex-wrap justify-end gap-2">
        <button type="button" className={secondaryButton} disabled={printer.busy}
                onClick={() => printer.open(
                  `/api/v1/training/records/print${query ? `?${query}` : ''}`,
                  'training-records.pdf')}>
          <Printer className="h-4 w-4" aria-hidden />
          {printer.busy ? 'Preparing…' : 'Print these records'}
        </button>
        {/* ★ Round 5, TC-08: "provide a way to export the report to a xls
            file." The same query as the list and the print, so the three cannot
            disagree about what was asked for. */}
        <button type="button" className={secondaryButton} disabled={exporter.busy}
                onClick={() => exporter.open(
                  `/api/v1/training/records/export${query ? `?${query}` : ''}`,
                  'training-records.xlsx')}>
          <Sheet className="h-4 w-4" aria-hidden />
          {exporter.busy ? 'Preparing…' : 'Export to Excel'}
        </button>
        {/* ★ Round 12, TRN-03: "create a way to print a worker or workers
            certificates based on the search criteria." The same query again;
            those taken elsewhere are listed at the end, not reissued. */}
        <button type="button" className={secondaryButton}
                disabled={certificates.busy || records.data?.length === 0}
                onClick={() => certificates.open(
                  `/api/v1/training/records/certificates${query ? `?${query}` : ''}`,
                  'training-certificates.pdf')}>
          <Award className="h-4 w-4" aria-hidden />
          {certificates.busy ? 'Preparing…' : 'Print their certificates'}
        </button>
        {mayWrite && (
          <button type="button" className={primaryButton} onClick={() => setAdding(true)}>
            <Upload className="h-4 w-4" aria-hidden />
            Record training taken elsewhere
          </button>
        )}
      </div>
      {(printer.error || exporter.error || certificates.error) && (
        <p className="mb-3 rounded-lg bg-danger-50 px-3 py-2 text-xs text-danger-700">
          {printer.error || exporter.error || certificates.error}
        </p>
      )}

      {adding && (
        <NewRecordForm
          courseCode={openForCourse}
          onClose={() => { setAdding(false); onConsumed?.() }}
        />
      )}

      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          className="h-10 min-w-0 flex-1 rounded-lg border border-neutral-200 px-3 text-sm"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by person or course"
          aria-label="Search training records"
        />
        <select className="h-10 rounded-lg border border-neutral-200 px-2 text-sm"
                value={order} onChange={(e) => setOrder(e.target.value as typeof order)}
                aria-label="Group the records by">
          <option value="date">Newest first</option>
          <option value="person">By person</option>
          <option value="topic">By course</option>
        </select>
        <select className="h-10 rounded-lg border border-neutral-200 px-2 text-sm"
                value={window_ ?? ''} aria-label="Status"
                onChange={(e) => {
                  if (expiry) onClearExpiry?.()
                  setStatus(e.target.value as ExpiryWindow | '')
                }}>
          <option value="">Any status</option>
          {(Object.keys(EXPIRY_LABELS) as ExpiryWindow[]).map((w) => (
            <option key={w} value={w}>{EXPIRY_LABELS[w]}</option>
          ))}
        </select>
        <select className="h-10 rounded-lg border border-neutral-200 px-2 text-sm"
                value={type} aria-label="Type"
                onChange={(e) => setType(e.target.value as typeof type)}>
          <option value="">Any type</option>
          <option value="internal">Taken here</option>
          <option value="external">Taken elsewhere</option>
        </select>
        <DateRangeFilter {...dateFilter} />
      </div>

      {records.data?.length === 0 && !adding ? (
        <Empty>
          Nothing on file yet. Records appear here when somebody passes a course
          session, or when a certificate earned elsewhere is entered.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {recordPage.pageRows.map((r) => (
            <li key={r.id}>
              <Card>
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="text-sm font-semibold text-neutral-900">{r.user_name}</span>
                  <span className="min-w-0 flex-1 text-sm text-neutral-700">{r.course_label}</span>
                  {r.delivery === 'external' && (
                    <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600">
                      External
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-neutral-500">
                  Completed {formatDate(r.completed_on)}
                  {r.expires_on ? ` · expires ${formatDate(r.expires_on)}` : ' · does not expire'}
                  {r.provider && ` · by ${r.provider}`}
                  {r.recorded_by_name && ` · entered by ${r.recorded_by_name}`}
                </p>
                <Certificate record={r} mayWrite={mayWrite} />
                {/* Round 9: the record's own files, any number. */}
                <MaterialsList base={`/api/v1/training/records/${r.id}/materials`}
                               mayWrite={mayWrite} what="this record" />
              </Card>
            </li>
          ))}
        </ul>      )}


      <Pagination page={recordPage.page} pageCount={recordPage.pageCount}
                  total={recordPage.total} onPage={recordPage.setPage} noun="records" />
    </>
  )
}

/**
 * The default expiry: the course's own period, less a day.
 *
 * ★ Round 9, question 5: "each course should carry its own expiry date … if I
 * create a course with a 12 month expiry, have the certificate expire in 12
 * months minus a day." (Round 8 asked for three years less a day for every
 * course; this replaces it.) Calendar months, the same rule as the server's
 * services/validity.py — a month without the day expires on its last day. On the
 * date strings themselves: a date-only value put through local time comes out
 * a day early here (UTC−4). Empty for a course that does not expire.
 */
function courseExpiry(completed: string, months: number | null | undefined): string {
  const [y, m, d] = completed.split('-').map(Number)
  if (!y || !m || !d || !months) return ''
  const month0 = m - 1 + months
  const year = y + Math.floor(month0 / 12)
  const month = month0 % 12
  const last = new Date(Date.UTC(year, month + 1, 0)).getUTCDate()
  // No such day that month: the month's last, which is the day before the 1st
  // it would roll to (round 8: 29 Feb + 3 years → 28 Feb). Otherwise less a day.
  const t = d > last
    ? new Date(Date.UTC(year, month, last))
    : new Date(Date.UTC(year, month, d) - 86_400_000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}`
}

function NewRecordForm({ onClose, courseCode }: {
  onClose: () => void; courseCode?: string | null
}) {
  const queryClient = useQueryClient()
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const [courseId, setCourseId] = useState('')
  const [completedOn, setCompletedOn] = useState(todayForInput())
  const [expiresOn, setExpiresOn] = useState('')
  const [delivery, setDelivery] = useState('external')
  // ★ Round 9, TC-07: who gave it, printed and exported with the record.
  const [provider, setProvider] = useState('')
  // ★ Round 8: files chosen with the record, not afterwards on it. Round 9,
  // question 6: "several files on a training record. all kinds of file types."
  const [files, setFiles] = useState<File[]>([])
  // Which of them reached the server, so a retry sends only the rest.
  const [sent, setSent] = useState<string[]>([])
  // Once the record is saved, a failed upload must not save it a second time
  // when Save is pressed again — it retries the file alone.
  const [savedId, setSavedId] = useState<string | null>(null)

  const courses = useQuery({
    queryKey: ['courses'],
    queryFn: () => api.get<Course[]>('/api/v1/training/courses'),
  })

  // The course arrives as a code because that is what the sending page knows;
  // the list it has to be matched against loads separately, so the match runs
  // when the list arrives rather than at mount. Only ever preselects: once
  // somebody has chosen a course themselves, this leaves it alone.
  const courseList = courses.data
  useEffect(() => {
    if (!courseCode || courseId || !courseList) return
    const match = courseList.find(
      (c) => c.code.toLowerCase() === courseCode.toLowerCase())
    if (match) setCourseId(match.id)
  }, [courseCode, courseId, courseList])

  // The default is set whenever the course or the completion date changes, and
  // left alone otherwise — a date typed by hand stays until one of them moves.
  const months = courseList?.find((c) => c.id === courseId)?.validity_months
  useEffect(() => {
    setExpiresOn(courseExpiry(completedOn, months))
  }, [completedOn, courseId, months])

  const create = useMutation({
    mutationFn: async () => {
      let id = savedId
      if (!id) {
        const made = await api.post<{ id: string }>('/api/v1/training/records', {
          user_id: person.userId,
          course_id: courseId,
          completed_on: completedOn,
          // Left blank, the server derives it from the course's validity period.
          expires_on: expiresOn || null,
          delivery,
          provider: delivery === 'external' ? provider.trim() || null : null,
        })
        id = made.id
        setSavedId(id)
      }
      for (const f of files) {
        const key = `${f.name}:${f.size}:${f.lastModified}`
        if (sent.includes(key)) continue
        const body = new FormData()
        body.append('file', f)
        await api.upload(`/api/v1/training/records/${id}/materials`, body)
        setSent((done) => [...done, key])
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['training-records'] })
      queryClient.invalidateQueries({ queryKey: ['training', 'gaps'] })
      onClose()
    },
    // The record may be saved even though the file was not.
    onError: () => queryClient.invalidateQueries({ queryKey: ['training-records'] }),
  })
  const locked = savedId !== null

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">Record training</h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Who">
          <PersonPicker value={person} onChange={setPerson} allowExternal={false}
                        disabled={locked} />
        </Field>
        <Field label="Course">
          <select className={inputClass} value={courseId} disabled={locked}
                  onChange={(e) => setCourseId(e.target.value)}>
            <option value="">Select…</option>
            {courses.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </Field>
        <Field label="Completed on">
          <input type="date" className={inputClass} value={completedOn} disabled={locked}
                 onChange={(e) => setCompletedOn(e.target.value)} />
        </Field>
        <Field label="Expires on"
               hint="The course's validity period less a day, filled in from the course. Change it if the certificate says otherwise; a course that does not expire leaves it empty">
          <input type="date" className={inputClass} value={expiresOn} disabled={locked}
                 onChange={(e) => setExpiresOn(e.target.value)} />
        </Field>
        <Field label="Taken">
          <select className={inputClass} value={delivery} disabled={locked}
                  onChange={(e) => setDelivery(e.target.value)}>
            <option value="external">With an outside provider</option>
            <option value="internal">In house</option>
          </select>
        </Field>
        {delivery === 'external' && (
          <Field label="Provider" hint="Who gave the training">
            <input className={inputClass} value={provider} disabled={locked} maxLength={200}
                   onChange={(e) => setProvider(e.target.value)}
                   placeholder="e.g. Workplace Safety & Prevention Services" />
          </Field>
        )}
        <Field label="Files"
               hint="Optional. The certificate and anything else — any number, any document, picture or video">
          <input type="file" multiple className="block min-h-[44px] w-full text-sm"
                 onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
        </Field>
      </div>

      {locked && create.isError && (
        <p role="alert" className="mt-3 text-sm text-amber-800">
          The training is saved; {files.length - sent.length} of {files.length} file
          {files.length === 1 ? '' : 's'} did not upload. Press Attach to retry them, or close and
          attach them from the record.
        </p>
      )}
      <ErrorNote error={create.error} />

      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!person.userId || !courseId || !completedOn || create.isPending
                          || (locked && files.length === sent.length)}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Saving…' : locked ? 'Attach' : 'Save'}
        </button>
      </div>
    </Card>
  )
}

/**
 * The course catalogue.
 *
 * Which courses everybody has to take is the plant's decision and was not
 * editable: four are required of every employee, the compliance report counts
 * every employee against them, and there was nothing on screen to change it.
 * The material columns had the same history — added with the table, never
 * written to, so a course's own handout had to be attached to each session
 * separately or not at all.
 */
type CourseSort = 'name' | 'validity_months'

function CoursesTab({ mayWrite }: { mayWrite: boolean }) {
  const [adding, setAdding] = useState(false)
  // ★ Round 12, TRN-04: courses are searched by name — round 8 took the
  // number off them and they carry no date. TRN-08: and corrected in place.
  // ★ Review C7: a table under the list standard, paged on the server.
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [sort, onSort] = useSort<CourseSort>(null, () => setPage(1))
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize), ...sortParams(sort) })
  if (search.trim()) qs.set('q', search.trim())
  const courses = useQuery({
    queryKey: ['training-courses', 'page', qs.toString()],
    queryFn: () => api.get<{ items: Course[]; total: number }>(`/api/v1/training/courses/page?${qs}`),
    placeholderData: (prev) => prev,
  })
  const quizzes = useQuery({
    queryKey: ['quizzes'],
    queryFn: () => api.get<QuizSummary[]>('/api/v1/quizzes'),
  })
  const quizFor = (courseId: string) =>
    (quizzes.data ?? []).find((q) => q.course_id === courseId) ?? null
  const rows = courses.data?.items ?? []

  return (
    <div>
      {mayWrite && (
        <div className="mb-3 flex justify-end">
          <button type="button" className={primaryButton} onClick={() => setAdding(true)}>
            Add a course
          </button>
        </div>
      )}
      {adding && <NewCourseForm onClose={() => setAdding(false)} />}

      <input className="mb-3 h-10 w-full rounded-lg border border-neutral-200 px-3 text-sm"
             value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }}
             placeholder="Search by course name" aria-label="Search courses" />

      <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
        <div className="max-h-[calc(100vh-18rem)] overflow-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="sticky top-0 z-10 bg-neutral-50 shadow-[0_1px_0_#e5e5e5]">
              <tr>
                <SortTh label="Course" sortKey="name" sort={sort} onSort={onSort} className={TH} />
                <SortTh label="Valid for" sortKey="validity_months" sort={sort} onSort={onSort} className={TH} />
                <th className={TH}>Quiz</th>
                <th className={TH}>Material</th>
                {mayWrite && <th className={TH}><span className="sr-only">Edit</span></th>}
              </tr>
            </thead>
            <tbody>
              {courses.isPending && (
                <tr><td colSpan={5} className="p-6 text-center text-sm text-neutral-500">Loading…</td></tr>
              )}
              {courses.data && rows.length === 0 && (
                <tr><td colSpan={5} className="p-8 text-center text-sm text-neutral-500">
                  {search.trim() ? 'No course matches.' : 'No courses yet.'}
                </td></tr>
              )}
              {rows.map((c) => {
                const quiz = quizFor(c.id)
                if (editing === c.id) {
                  return (
                    <tr key={c.id} className="border-t border-neutral-100">
                      <td colSpan={5} className="p-3.5">
                        <EditCourseForm course={c} hasQuiz={Boolean(quiz)} onClose={() => setEditing(null)} />
                      </td>
                    </tr>
                  )
                }
                return (
                  <tr key={c.id} className="border-t border-neutral-100 align-top">
                    {/* ★ Round 8: the name is the course; the code stays internal. */}
                    <td className="px-3 py-2 font-medium text-neutral-900">{c.name}</td>
                    <td className="px-3 py-2 text-xs text-neutral-600">
                      {c.validity_months ? `${c.validity_months} months` : 'Does not expire'}
                    </td>
                    {/* The quiz that belongs to this course, and how the room did on it. */}
                    <td className="px-3 py-2">
                      {quiz ? <CourseQuiz quiz={quiz} /> : <span className="text-xs text-neutral-400">No quiz</span>}
                    </td>
                    <td className="px-3 py-2"><CourseMaterial course={c} mayWrite={mayWrite} /></td>
                    {mayWrite && (
                      <td className="px-3 py-2 text-right">
                        <button type="button" onClick={() => setEditing(c.id)}
                                className="inline-flex min-h-[32px] items-center gap-1 rounded-md px-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50 hover:text-neutral-900">
                          <Pencil className="h-3.5 w-3.5" aria-hidden />
                          Edit
                        </button>
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <ShellPagination page={page} pageSize={pageSize} total={courses.data?.total ?? 0}
                         onPageChange={setPage} onPageSizeChange={(n) => { setPageSize(n); setPage(1) }} />
      </div>
      <p className="mt-2 text-xs text-neutral-500">
        Who has to take a course is set on the position, under Settings → Positions;
        the compliance report counts it against the people in those positions.
      </p>
    </div>
  )
}

/**
 * ★ Round 12, TRN-08: "create a way to edit a course after its created to
 * correct spelling mistakes and add quizzes or change the valid for date."
 * Q14: "certificates should stay once issued. a new valid for period should
 * only affect new course sessions." A record keeps the name and expiry it was
 * made with, so neither change reaches anything already recorded — said on
 * the form, because it is the first thing anybody correcting a name asks.
 */
function EditCourseForm({ course, hasQuiz, onClose }: {
  course: Course; hasQuiz: boolean; onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(course.name)
  const [months, setMonths] = useState(course.validity_months ? String(course.validity_months) : '')
  const save = useMutation({
    mutationFn: () => api.patch(`/api/v1/training/courses/${course.id}`, {
      name: name.trim(),
      validity_months: months.trim() ? Number(months) : null,
    }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['training-courses'] })
      onClose()
    },
  })
  const monthsOk = !months.trim() || (/^\d+$/.test(months.trim()) && Number(months) >= 1 && Number(months) <= 600)
  const ready = name.trim().length > 0 && monthsOk
  return (
    <div>
      <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
        <Field label="Course name">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Valid for (months)" hint="Blank: does not expire">
          <input className={inputClass} inputMode="numeric" value={months}
                 onChange={(e) => setMonths(e.target.value)} />
        </Field>
      </div>
      <p className="mt-2 text-xs text-neutral-500">
        Certificates already issued keep the name and expiry they were issued
        with. A new period applies to training recorded from now on.
      </p>
      <p className="mt-1 text-xs text-neutral-500">
        {hasQuiz ? 'This course has a quiz. ' : 'No quiz yet. '}
        <Link to="/quizzes" className="font-semibold text-primary-700 hover:underline">
          {hasQuiz ? 'Change it under Quizzes' : 'Add one under Quizzes'}
        </Link>
      </p>
      <ErrorNote error={save.error} />
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton} disabled={!ready || save.isPending}
                onClick={() => { if (ready && !save.isPending) save.mutate() }}>
          {save.isPending ? 'Saving…' : 'Save the course'}
        </button>
      </div>
    </div>
  )
}

/**
 * The ticket from a course taken outside.
 *
 * The record has carried a column for it since it was built and the endpoint
 * accepted a file id at create time — which meant a caller had to already know
 * one, and there was no way to produce one. So the certificate, which is the
 * thing a record of external training exists to hold, could not be attached at
 * all. Anybody can see it; only training staff can put it there.
 */
function Certificate({ record, mayWrite }: { record: Record_; mayWrite: boolean }) {
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [opening, setOpening] = useState(false)

  const open = async () => {
    if (opening) return
    setOpening(true)
    try {
      const blob = await apiBlob(`/api/v1/training/records/${record.id}/certificate`)
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank', 'noopener')
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } finally {
      setOpening(false)
    }
  }
  const upload = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData()
      body.append('file', file)
      return api.upload(`/api/v1/training/records/${record.id}/certificate`, body)
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['training-records'] }),
  })

  // The issued one is rendered on request rather than stored, so it is fetched
  // the same way as an uploaded file: with the session token, as a blob.
  //
  // ★ Through `useFileOpener`, which keeps the error. A bare try/finally
  // swallowed a 403 and put the button back as if nothing had happened.
  const issued = useFileOpener()

  // ★ Training run here gets a certificate the plant issues, generated from
  // the record so it cannot drift from it. A course taken elsewhere keeps the
  // one its awarding body gave — issuing our own for somebody else's ticket
  // would be making a claim that is not ours to make.
  const isOurs = record.delivery !== 'external'

  // ★ Round 9: files on a record taken elsewhere go in its file list below,
  // any number; this slot stays only for a certificate already in it.
  if (!record.certificate_file_id && !isOurs) return null

  return (
    <p className="mt-1 flex items-center gap-1.5 text-xs">
      <Paperclip className="h-3.5 w-3.5 shrink-0 text-neutral-400" aria-hidden />
      {record.certificate_file_id ? (
        // Fetched with the session token and handed to the browser as a blob.
        // A plain link cannot carry the header and would come back a 401.
        <button type="button" disabled={opening} onClick={open}
                className="font-medium text-primary-700 hover:underline disabled:opacity-40">
          {opening ? 'Opening…' : 'Certificate'}
        </button>
      ) : isOurs ? (
        <button type="button" disabled={issued.busy}
                onClick={() => issued.open(
                  `/api/v1/training/records/${record.id}/certificate/issued`,
                  `certificate-${record.user_name}.pdf`)}
                className="font-medium text-primary-700 hover:underline disabled:opacity-40">
          {issued.busy ? 'Opening…' : 'Certificate'}
        </button>
      ) : (
        <span className="text-neutral-500">No certificate on file</span>
      )}
      {issued.error && <span className="text-danger-700">{issued.error}</span>}
      {mayWrite && record.delivery === 'external' && (
        <>
          <input ref={inputRef} type="file" className="hidden"
                 accept="application/pdf,image/png,image/jpeg,image/webp"
                 onChange={(e) => {
                   const file = e.target.files?.[0]
                   if (file) upload.mutate(file)
                   e.target.value = ''
                 }} />
          <button type="button" disabled={upload.isPending}
                  onClick={() => inputRef.current?.click()}
                  className="font-medium text-primary-700 disabled:opacity-40">
            {upload.isPending ? 'Uploading…'
              : record.certificate_file_id ? 'Replace' : 'Attach'}
          </button>
        </>
      )}
    </p>
  )
}


/**
 * Everything attached to a course.
 *
 * It held one file, and uploading a second replaced the first with nothing on
 * screen saying so. A course is rarely one file — the deck, the handout, the
 * sign-off sheet, last year's revision — which is what the second acceptance
 * round asked for.
 */
/**
 * A course's quiz, and how everyone who has taken it did.
 *
 * Every attempt on that quiz, across every session the course has ever run —
 * not one sitting. The numbers are already computed for the quiz's own page;
 * this puts them where the question gets asked.
 *
 * ★ Loading is not "nobody has taken it". That is the same mistake as an empty
 * list standing in for a failed request, and on a slow connection an instructor
 * would read "nobody has taken it yet" for a second or two before the real
 * figures arrived.
 */
function CourseQuiz({ quiz }: { quiz: QuizSummary }) {
  const results = useQuery({
    queryKey: ['quiz-results', quiz.id],
    queryFn: () => api.get<QuizResults>(`/api/v1/quizzes/${quiz.id}/results`),
  })
  const r = results.data

  return (
    <Link to={`/quizzes/${quiz.id}/edit`}
          className="flex min-w-0 flex-col text-xs font-medium text-primary-700 hover:underline">
      <span className="truncate">Quiz: {quiz.title}</span>
      <span className="text-[11px] font-normal text-neutral-500">
        {results.isError ? 'results unavailable'
          : results.isLoading || !r ? '…'
          : r.people === 0 ? 'nobody has taken it yet'
          : quiz.question_count > 0 && quiz.marked_count === 0
            ? `feedback from ${r.people}, all sittings`
          : `${r.passed} of ${r.people} passed, all sittings`
            + (r.average_score !== null ? ` · average ${r.average_score}%` : '')}
      </span>
    </Link>
  )
}

function CourseMaterial({ course, mayWrite }: { course: Course; mayWrite: boolean }) {
  // ★ Round 6: any file but a page or a program, and links. One list for the
  // course and its sessions — see MaterialsList.
  return (
    <MaterialsList base={`/api/v1/training/courses/${course.id}/materials`}
                   mayWrite={mayWrite} what="this course" />
  )
}

function NewCourseForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [months, setMonths] = useState('')
  const create = useMutation({
    // ★ Round 8: name only. The server makes the internal code from it.
    mutationFn: () => api.post('/api/v1/training/courses', {
      name: name.trim(),
      validity_months: months ? Number(months) : null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['training-courses'] })
      // The session and record forms read the same list under another key.
      queryClient.invalidateQueries({ queryKey: ['courses'] })
      onClose()
    },
  })
  return (
    <Card className="mb-4">
      <h3 className="mb-3 text-sm font-semibold text-neutral-900">Add a course</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><input className={inputClass} value={name}
               onChange={(e) => setName(e.target.value)} placeholder="Ladder safety" /></Field>
        <Field label="Valid for (months)" hint="Leave empty if it does not expire">
          <input className={inputClass} type="number" min={1} value={months}
                 onChange={(e) => setMonths(e.target.value)} />
        </Field>
      </div>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!name.trim() || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Adding…' : 'Add'}
        </button>
      </div>
    </Card>
  )
}
