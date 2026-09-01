/**
 * Training: who is compliant, what is being run, and what is on file.
 *
 * Three tabs because three different questions get asked of this data and they
 * have different audiences. Compliance is what a Ministry inspector asks for.
 * Sessions is what a supervisor lives in. Records is where a certificate that
 * arrived on paper gets typed in.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { CalendarDays, Plus, Upload, X } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import {
  Card, Empty, ErrorNote, Field, PageHeader, Tabs, inputClass, primaryButton, secondaryButton,
} from '@/components/ui'
import TrainingGapsPanel from '@/pages/TrainingGapsPage'

interface Course { id: string; code: string; name: string; is_statutory: boolean; validity_months: number | null }
interface Session {
  id: string; delivery_no: string; course_label: string; title: string
  session_date: string; start_time: string | null; location_text: string | null
  coordinator_name: string | null; instructor_name: string | null
  status: string; enrolled: number; passed: number
}
interface Record_ {
  id: string; user_name: string; course_label: string; completed_on: string
  expires_on: string | null; delivery: string; recorded_by_name: string | null
}

const TABS = [
  { key: 'compliance', label: 'Compliance' },
  { key: 'sessions', label: 'Course sessions' },
  { key: 'records', label: 'Records' },
] as const
type TabKey = (typeof TABS)[number]['key']

export default function TrainingPage() {
  const [tab, setTab] = useState<TabKey>('compliance')
  const { can } = usePermissions()
  const mayWrite = can('ehs.training.write')

  return (
    <div className="p-4">
      <PageHeader title="Training" />
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === 'compliance' && <TrainingGapsPanel />}
      {tab === 'sessions' && <SessionsTab mayWrite={mayWrite} />}
      {tab === 'records' && <RecordsTab mayWrite={mayWrite} />}
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
          <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-neutral-400">
            Coming up
          </h2>
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
          <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-neutral-400">
            Held
          </h2>
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

  const courses = useQuery({
    queryKey: ['courses'],
    queryFn: () => api.get<Course[]>('/api/v1/training/courses'),
  })

  const create = useMutation({
    mutationFn: () => api.post<Session>('/api/v1/course-sessions', {
      course_id: courseId,
      title: title || null,
      session_date: date,
      start_time: start ? `${start}:00` : null,
      location_text: where || null,
      coordinator_id: coordinator.userId,
    }),
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

function RecordsTab({ mayWrite }: { mayWrite: boolean }) {
  const [adding, setAdding] = useState(false)
  const records = useQuery({
    queryKey: ['training-records'],
    queryFn: () => api.get<Record_[]>('/api/v1/training/records'),
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

      {adding && <NewRecordForm onClose={() => setAdding(false)} />}

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
              </Card>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function NewRecordForm({ onClose }: { onClose: () => void }) {
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
