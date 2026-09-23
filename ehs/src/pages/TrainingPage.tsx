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
import { CalendarDays, Plus, Upload, X, Paperclip } from 'lucide-react'
import { api, apiBlob } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import {
  Card, Empty, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle, Tabs,
} from '@/components/ui'
import TrainingGapsPanel from '@/pages/TrainingGapsPage'

interface Course {
  id: string; code: string; name: string; is_statutory: boolean
  applies_to_all: boolean; validity_months: number | null; is_active: boolean
  material_filename: string | null
}
interface QuizSummary { id: string; title: string; course_id: string | null }
interface Session {
  id: string; delivery_no: string; course_label: string; title: string
  session_date: string; start_time: string | null; location_text: string | null
  coordinator_name: string | null; instructor_name: string | null
  status: string; enrolled: number; passed: number
}
interface Record_ {
  id: string; user_name: string; course_label: string; completed_on: string
  expires_on: string | null; delivery: string; recorded_by_name: string | null
  certificate_file_id: string | null
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
  const { can } = usePermissions()
  const mayWrite = can('ehs.training.write')

  return (
    <div className="p-4">
      <PageHeader title="Training" />
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === 'compliance' && <TrainingGapsPanel />}
      {tab === 'courses' && <CoursesTab mayWrite={mayWrite} />}
      {tab === 'sessions' && <SessionsTab mayWrite={mayWrite} />}
      {tab === 'records' && (
        <RecordsTab
          mayWrite={mayWrite}
          openForCourse={recordCourse}
          onConsumed={() => setParams({}, { replace: true })}
        />
      )}
    </div>
  )
}

// ── Course sessions ──────────────────────────────────────────────────────────

function SessionsTab({ mayWrite }: { mayWrite: boolean }) {
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)

  const sessions = useQuery({
    queryKey: ['course-sessions'],
    queryFn: () => api.get<Session[]>('/api/v1/course-sessions'),
  })

  const upcoming = (sessions.data ?? []).filter((s) => s.status === 'scheduled')
  const past = (sessions.data ?? []).filter((s) => s.status !== 'scheduled')

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

      {sessions.data?.length === 0 && !creating && (
        <Empty>
          No course sessions yet. Scheduling one is how a course gets a date, a
          room, and a register of who attended.
        </Empty>
      )}

      {upcoming.length > 0 && (
        <>
          <SectionTitle className="mb-2">Coming up</SectionTitle>
          <ul className="mb-6 flex flex-col gap-2">
            {upcoming.map((s) => (
              <SessionRow key={s.id} session={s}
                          onOpen={() => navigate(`/training/sessions/${s.id}`)} />
            ))}
          </ul>
        </>
      )}

      {past.length > 0 && (
        <>
          <SectionTitle className="mb-2">Held</SectionTitle>
          <ul className="flex flex-col gap-2">
            {past.map((s) => (
              <SessionRow key={s.id} session={s}
                          onOpen={() => navigate(`/training/sessions/${s.id}`)} />
            ))}
          </ul>
        </>
      )}
    </>
  )
}

function SessionRow({ session, onOpen }: { session: Session; onOpen: () => void }) {
  return (
    <li>
      <button type="button" onClick={onOpen}
              className="flex w-full items-start gap-3 rounded-xl border border-neutral-200 bg-white p-3.5 text-left transition-colors hover:border-primary-300">
        <CalendarDays className="mt-0.5 h-5 w-5 shrink-0 text-primary-600" aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-neutral-900">{session.title}</span>
          <span className="mt-0.5 block text-xs text-neutral-500">
            {session.delivery_no} · {formatDate(session.session_date)}
            {session.start_time && ` at ${session.start_time.slice(0, 5)}`}
            {session.location_text && ` · ${session.location_text}`}
          </span>
          <span className="mt-1 block text-xs text-neutral-600">
            {session.enrolled === 0
              ? 'Nobody on the roster yet'
              : `${session.enrolled} on the roster${session.passed ? `, ${session.passed} passed` : ''}`}
            {session.coordinator_name && ` · run by ${session.coordinator_name}`}
          </span>
        </span>
        {session.status !== 'scheduled' && (
          <span className="shrink-0 rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-semibold text-neutral-600">
            {session.status === 'completed' ? 'Complete' : 'Cancelled'}
          </span>
        )}
      </button>
    </li>
  )
}

function NewSessionForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [courseId, setCourseId] = useState('')
  const [title, setTitle] = useState('')
  const [date, setDate] = useState('')
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
          <select className={inputClass} value={courseId}
                  onChange={(e) => setCourseId(e.target.value)}>
            <option value="">Select…</option>
            {courses.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}{c.is_statutory ? ' (required)' : ''}
              </option>
            ))}
          </select>
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

function RecordsTab({ mayWrite, openForCourse, onConsumed }: {
  mayWrite: boolean; openForCourse?: string | null; onConsumed?: () => void
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
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  const query = [
    search.trim() ? `search=${encodeURIComponent(search.trim())}` : '',
    order !== 'date' ? `order=${order}` : '',
    from ? `completed_from=${from}` : '',
    to ? `completed_to=${to}` : '',
  ].filter(Boolean).join('&')

  const records = useQuery({
    queryKey: ['training-records', search.trim(), order, from, to],
    queryFn: () => api.get<Record_[]>(
      `/api/v1/training/records${query ? `?${query}` : ''}`),
  })

  return (
    <>
      {mayWrite && (
        <div className="mb-3 flex justify-end">
          <button type="button" className={primaryButton} onClick={() => setAdding(true)}>
            <Upload className="h-4 w-4" aria-hidden />
            Record training taken elsewhere
          </button>
        </div>
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
        <span className="flex items-center gap-1.5">
          <input type="date" className="h-10 rounded-lg border border-neutral-200 px-2 text-sm"
                 value={from} onChange={(e) => setFrom(e.target.value)}
                 aria-label="Completed on or after" />
          <span className="text-xs text-neutral-400">to</span>
          <input type="date" className="h-10 rounded-lg border border-neutral-200 px-2 text-sm"
                 value={to} onChange={(e) => setTo(e.target.value)}
                 aria-label="Completed on or before" />
        </span>
      </div>

      {records.data?.length === 0 && !adding ? (
        <Empty>
          Nothing on file yet. Records appear here when somebody passes a course
          session, or when a certificate earned elsewhere is entered.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {records.data?.map((r) => (
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
                  {r.recorded_by_name && ` · entered by ${r.recorded_by_name}`}
                </p>
                <Certificate record={r} mayWrite={mayWrite} />
              </Card>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function NewRecordForm({ onClose, courseCode }: {
  onClose: () => void; courseCode?: string | null
}) {
  const queryClient = useQueryClient()
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const [courseId, setCourseId] = useState('')
  const [completedOn, setCompletedOn] = useState('')
  const [expiresOn, setExpiresOn] = useState('')
  const [delivery, setDelivery] = useState('external')

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

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/training/records', {
      user_id: person.userId,
      course_id: courseId,
      completed_on: completedOn,
      // Left blank, the server derives it from the course's validity period.
      expires_on: expiresOn || null,
      delivery,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['training-records'] })
      queryClient.invalidateQueries({ queryKey: ['training', 'gaps'] })
      onClose()
    },
  })

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
          <PersonPicker value={person} onChange={setPerson} allowExternal={false} />
        </Field>
        <Field label="Course">
          <select className={inputClass} value={courseId}
                  onChange={(e) => setCourseId(e.target.value)}>
            <option value="">Select…</option>
            {courses.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </Field>
        <Field label="Completed on">
          <input type="date" className={inputClass} value={completedOn}
                 onChange={(e) => setCompletedOn(e.target.value)} />
        </Field>
        <Field label="Expires on"
               hint="Leave blank to use the course's own validity period">
          <input type="date" className={inputClass} value={expiresOn}
                 onChange={(e) => setExpiresOn(e.target.value)} />
        </Field>
        <Field label="Taken">
          <select className={inputClass} value={delivery}
                  onChange={(e) => setDelivery(e.target.value)}>
            <option value="external">With an outside provider</option>
            <option value="internal">In house</option>
          </select>
        </Field>
      </div>

      <ErrorNote error={create.error} />

      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!person.userId || !courseId || !completedOn || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Saving…' : 'Save'}
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
function CoursesTab({ mayWrite }: { mayWrite: boolean }) {
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const courses = useQuery({
    queryKey: ['training-courses'],
    queryFn: () => api.get<Course[]>('/api/v1/training/courses'),
  })
  const quizzes = useQuery({
    queryKey: ['quizzes'],
    queryFn: () => api.get<QuizSummary[]>('/api/v1/quizzes'),
  })
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api.patch(`/api/v1/training/courses/${id}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['training-courses'] })
      queryClient.invalidateQueries({ queryKey: ['training', 'gaps'] })
    },
  })

  if (courses.isLoading) return <p className="text-sm text-neutral-500">Loading…</p>

  const quizFor = (courseId: string) =>
    (quizzes.data ?? []).find((q) => q.course_id === courseId) ?? null

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

      <ul className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
        {(courses.data ?? []).map((c) => {
          const quiz = quizFor(c.id)
          return (
            <li key={c.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-neutral-100 p-3.5 last:border-b-0">
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-neutral-900">{c.name}</span>
                <span className="mt-0.5 block text-xs text-neutral-500">
                  <span className="font-mono">{c.code}</span>
                  {c.is_statutory && ' · required by law'}
                  {c.validity_months ? ` · valid ${c.validity_months} months` : ' · does not expire'}
                </span>
              </span>

              {/* The quiz that belongs to this course, which the training
                  screens never showed — so a quiz and the course it tests
                  looked like two unrelated things. */}
              {quiz ? (
                <Link to={`/quizzes/${quiz.id}/edit`}
                      className="text-xs font-medium text-primary-700 hover:underline">
                  Quiz: {quiz.title}
                </Link>
              ) : (
                <span className="text-xs text-neutral-400">No quiz</span>
              )}

              <CourseMaterial course={c} mayWrite={mayWrite} />

              <label className="flex min-h-[32px] items-center gap-1.5 text-xs text-neutral-700">
                <input
                  type="checkbox"
                  checked={c.applies_to_all}
                  disabled={!mayWrite || patch.isPending}
                  onChange={(e) => {
                    if (!patch.isPending) {
                      patch.mutate({ id: c.id, body: { applies_to_all: e.target.checked } })
                    }
                  }}
                />
                Everyone must take it
              </label>
            </li>
          )
        })}
      </ul>
      <ErrorNote error={patch.error} />
      <p className="mt-2 text-xs text-neutral-500">
        Untick “everyone” and the course stops counting against every employee in
        the compliance report — it is then required only of the positions that
        list it.
      </p>
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

  if (!record.certificate_file_id && !mayWrite) return null

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
      ) : (
        <span className="text-neutral-500">No certificate on file</span>
      )}
      {mayWrite && (
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

interface Material {
  id: string; filename: string; content_type: string; file_size: number
  uploaded_by_name: string | null; created_at: string
}

/**
 * Everything attached to a course.
 *
 * It held one file, and uploading a second replaced the first with nothing on
 * screen saying so. A course is rarely one file — the deck, the handout, the
 * sign-off sheet, last year's revision — which is what the second acceptance
 * round asked for.
 */
function CourseMaterial({ course, mayWrite }: { course: Course; mayWrite: boolean }) {
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)

  const materials = useQuery({
    queryKey: ['course-materials', course.id],
    queryFn: () => api.get<Material[]>(`/api/v1/training/courses/${course.id}/materials`),
  })
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['course-materials', course.id] })

  const upload = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData()
      body.append('file', file)
      return api.upload(`/api/v1/training/courses/${course.id}/materials`, body)
    },
    onSuccess: refresh,
  })
  const remove = useMutation({
    mutationFn: (materialId: string) =>
      api.del(`/api/v1/training/courses/${course.id}/materials/${materialId}`),
    onSuccess: refresh,
  })

  const files = materials.data ?? []
  if (!mayWrite && files.length === 0) return null

  return (
    <span className="flex flex-col gap-0.5 text-xs text-neutral-600">
      {files.length === 0 && <span className="text-neutral-500">No material</span>}
      {files.map((m) => (
        <span key={m.id} className="flex items-center gap-1.5">
          <Paperclip className="h-3.5 w-3.5 shrink-0 text-neutral-400" aria-hidden />
          <span className="min-w-0 truncate">{m.filename}</span>
          {mayWrite && (
            <button type="button" aria-label={`Remove ${m.filename}`}
                    disabled={remove.isPending}
                    onClick={() => { if (!remove.isPending) remove.mutate(m.id) }}
                    className="shrink-0 rounded p-0.5 text-neutral-400 hover:bg-neutral-50">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </span>
      ))}
      {mayWrite && (
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
                  className="self-start rounded-md font-medium text-primary-700 disabled:opacity-40">
            {upload.isPending ? 'Uploading…' : 'Attach a file'}
          </button>
        </>
      )}
    </span>
  )
}

function NewCourseForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [months, setMonths] = useState('')
  const [everyone, setEveryone] = useState(false)
  const create = useMutation({
    mutationFn: () => api.post('/api/v1/training/courses', {
      code: code.trim().toUpperCase(), name: name.trim(),
      applies_to_all: everyone,
      validity_months: months ? Number(months) : null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['training-courses'] })
      onClose()
    },
  })
  return (
    <Card className="mb-4">
      <h3 className="mb-3 text-sm font-semibold text-neutral-900">Add a course</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Code"><input className={inputClass} value={code}
               onChange={(e) => setCode(e.target.value)} placeholder="LADDER" /></Field>
        <Field label="Name"><input className={inputClass} value={name}
               onChange={(e) => setName(e.target.value)} placeholder="Ladder safety" /></Field>
        <Field label="Valid for (months)" hint="Leave empty if it does not expire">
          <input className={inputClass} type="number" min={1} value={months}
                 onChange={(e) => setMonths(e.target.value)} />
        </Field>
        <Field label="Who">
          <label className="flex min-h-[44px] items-center gap-2 text-sm">
            <input type="checkbox" checked={everyone}
                   onChange={(e) => setEveryone(e.target.checked)} />
            Everyone must take it
          </label>
        </Field>
      </div>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!code.trim() || !name.trim() || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Adding…' : 'Add'}
        </button>
      </div>
      <p className="mt-2 text-xs text-neutral-500">
        Courses required by law are seeded with the regulation behind them and
        cannot be created here.
      </p>
    </Card>
  )
}
