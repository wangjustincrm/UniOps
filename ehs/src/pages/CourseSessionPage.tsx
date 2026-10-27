/**
 * One course session: its details, its roster, and marking who passed.
 *
 * Two people work on this screen and they see different things. The supervisor
 * HSE nominated builds the roster — that is all they can do, and it is all they
 * are shown. HSE fills in the instructor and marks results, which is what turns
 * an attendance list into certificates in people's files.
 *
 * Marking is deliberately not one control per person. HSE comes back from a
 * session with a sheet of paper and works down it, so the roster is a list of
 * three-way choices and one Save at the end.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { useTabTitle } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'
import { Check, Plus, QrCode, Trash2, X } from 'lucide-react'
import { api, errorCode } from '@/lib/api'
import QrPanel from '@/components/QrPanel'
import MaterialsList from '@/components/MaterialsList'
import { formatDate, formatDateTime } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { useEhsAuth } from '@/store/auth'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import {
  Card, Empty, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle, LoadProblem,
  LoadFailedNote,
} from '@/components/ui'

interface Session {
  id: string; delivery_no: string; course_label: string; title: string
  session_date: string; start_time: string | null; end_time: string | null
  location_text: string | null; coordinator_id: string | null; coordinator_name: string | null
  instructor_name: string | null; status: string; notes: string | null
  enrolled: number; passed: number
  quiz_id: string | null; quiz_title: string | null; quiz_qr_token: string | null
  length_minutes: number | null
  reopened_at: string | null; reopened_by_name: string | null; reopen_reason: string | null
  /** The end was taken from the first quiz pass, not typed by anybody. */
  end_time_from_quiz: boolean
}

/** 1 h 30 min, 45 min — the same words as the printed report. */
function lengthText(minutes: number | null): string | null {
  if (!minutes) return null
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return [h ? `${h} h` : '', m ? `${m} min` : ''].filter(Boolean).join(' ')
}
interface QuizResult {
  user_id: string | null; user_name: string; score_percent: number; passed: boolean; submitted_at: string
}
interface Enrolment {
  id: string; user_id: string | null; user_name: string; department_name: string | null
  result: string; marked_by_name: string | null; marked_at: string | null
  // roster | quiz — quiz means they scanned the quiz code in the room.
  joined_via: string
  quiz_passed_at: string | null; quiz_marked_by_name: string | null
  training_record_id: string | null
}

const RESULTS = [
  { key: 'passed', label: 'Passed' },
  { key: 'failed', label: 'Failed' },
  { key: 'absent', label: 'Did not attend' },
] as const

export default function CourseSessionPage() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  // ★ Or whoever is running this session. The backend was opened up to the
  // coordinator — HSE nominates anybody, including people with no training
  // permission at all — and this was left on the permission alone, so the
  // person named to run it got in and found no buttons. Fifth time in this
  // module that a gate was widened on one side only; found in review by
  // uniops-9a.
  const me = useEhsAuth((st) => st.user?.id) ?? null

  const session = useQuery({
    queryKey: ['course-session', id],
    queryFn: () => api.get<Session>(`/api/v1/course-sessions/${id}`),
    enabled: Boolean(id),
  })
  const roster = useQuery({
    queryKey: ['course-roster', id],
    queryFn: () => api.get<Enrolment[]>(`/api/v1/course-sessions/${id}/roster`),
    enabled: Boolean(id),
  })

  // The document number, not the uuid — a tab reading "Session 8f3a2b1c" tells
  // nobody which session it is.
  useTabTitle(ehsRoutes, session.data?.delivery_no)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['course-session', id] })
    queryClient.invalidateQueries({ queryKey: ['course-roster', id] })
    queryClient.invalidateQueries({ queryKey: ['course-sessions'] })
    queryClient.invalidateQueries({ queryKey: ['training-records'] })
    queryClient.invalidateQueries({ queryKey: ['training', 'gaps'] })
  }

  if (session.isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  // ★ The reason first. A refused or failed request leaves `data`
  // undefined exactly as a deleted record does, so this used to answer
  // "not found" to a 403 and to a server error alike.
  if (session.isError) return <LoadProblem error={session.error} noun="course session" />
  if (!session.data) return <LoadProblem error={null} noun="course session" />
  const s = session.data
  // Whoever may manage training at all, or the person this session was given to.
  const mayRun = can('ehs.training.write') || (Boolean(me) && s.coordinator_id === me)
  const open = s.status === 'scheduled'

  return (
    <div className="p-4">
      <PageHeader
        title={s.title}
        subtitle={`${s.delivery_no} · ${s.course_label}`}
      >
        {s.status !== 'scheduled' && (
          <span className="rounded-full bg-neutral-100 px-2.5 py-0.5 text-xs font-semibold text-neutral-600">
            {s.status === 'completed' ? 'Complete' : 'Cancelled'}
          </span>
        )}
      </PageHeader>

      <Card className="mb-4">
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
          <Detail label="When">
            {formatDate(s.session_date)}
            {s.start_time && ` · ${s.start_time.slice(0, 5)}`}
            {s.end_time && `–${s.end_time.slice(0, 5)}`}
            {s.end_time && s.end_time_from_quiz && (
              <span className="ml-1 text-xs text-neutral-500">
                (taken from the first quiz pass{mayRun && open ? ' · correct it with Change the arrangements' : ''})
              </span>
            )}
          </Detail>
          {/* ★ Round 6: "track the length of time the training is". From the
              start and end; the end is filled in by the first person to finish
              the quiz when nobody typed one. */}
          <Detail label="Length">
            {lengthText(s.length_minutes)
              ?? (s.start_time ? 'Set when the first person passes the quiz, or when an end time is entered'
                               : 'Needs a start and an end time')}
          </Detail>
          <Detail label="Where">{s.location_text ?? '—'}</Detail>
          <Detail label="Run by">{s.coordinator_name ?? 'Not assigned'}</Detail>
          <Detail label="Instructor">{s.instructor_name ?? 'Not recorded yet'}</Detail>
        </dl>
        {s.notes && <p className="mt-2 text-sm text-neutral-700">{s.notes}</p>}
        {mayRun && open && <ArrangementsEditor key={s.id} session={s} onSaved={invalidate}
                                                mayReassign={can('ehs.training.write')} />}
      </Card>

      {/* ★ Round 6, TC-06: a session starts with a copy of its course's
          material, and whoever runs it can add files and links. */}
      <Card className="mb-4">
        <SectionTitle className="mb-1.5">Material</SectionTitle>
        <MaterialsList base={`/api/v1/course-sessions/${s.id}/materials`}
                       mayWrite={mayRun && open} what="this session" />
      </Card>

      {s.quiz_id && <QuizCard session={s} mayRun={mayRun} />}

      {/* ★ A failed roster read is not an empty roster: "Nobody on the roster"
          is a statement about the session, and making it on the strength of a
          request that never answered is how somebody enrols a student twice. */}
      {roster.isError ? (
        <Card className="mt-4"><LoadFailedNote what="The roster" /></Card>
      ) : (
        <RosterPanel
          // ★ Round 10, TC-05: closed means read only — for HSE too. A result
          // is corrected on the person's training record instead.
          session={s} roster={roster.data ?? []} mayWrite={mayRun && open}
          onChanged={invalidate}
        />
      )}
    </div>
  )
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd className="text-sm text-neutral-900">{children}</dd>
    </div>
  )
}

/**
 * The session's arrangements, for whoever runs it.
 *
 * ★ Round 5, TC-07, answered A: the person running a session changes "the
 * instructor, the notes, the date and the place". The server has allowed all
 * four since then; this screen offered the instructor alone, so three of the
 * four were reachable only by calling the API — the same one-sided shape the
 * fix was for, the other way round. Found writing the round-6 script.
 *
 * Who runs it is not here: that is HSE's nomination and the server refuses it
 * to anybody else.
 */
function ArrangementsEditor({ session, onSaved, mayReassign }: {
  session: Session; onSaved: () => void
  /** ★ Round 7, TC-06: "allow the HSE role to change the run by person." The
   *  server took a new coordinator from training-write all along; nothing on
   *  screen offered it. Still HSE's alone — see the endpoint. */
  mayReassign: boolean
}) {
  const initialRunner: PersonValue = {
    ...EMPTY_PERSON, userId: session.coordinator_id, name: session.coordinator_name ?? '',
  }
  const [runner, setRunner] = useState<PersonValue>(initialRunner)
  const runnerChanged = mayReassign && runner.userId !== session.coordinator_id
  const initial = {
    session_date: session.session_date,
    start_time: session.start_time?.slice(0, 5) ?? '',
    end_time: session.end_time?.slice(0, 5) ?? '',
    location_text: session.location_text ?? '',
    instructor_name: session.instructor_name ?? '',
    notes: session.notes ?? '',
  }
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState(initial)
  const set = (k: keyof typeof initial, v: string) => setForm((f) => ({ ...f, [k]: v }))

  // Only what changed, and an emptied box as null — which this endpoint reads
  // as "clear it", not "leave it".
  const changed = (Object.keys(initial) as (keyof typeof initial)[])
    .filter((k) => form[k] !== initial[k])
  const save = useMutation({
    mutationFn: () => api.patch(`/api/v1/course-sessions/${session.id}`, {
      ...Object.fromEntries(changed.map((k) => [k, form[k] === '' ? null : form[k]])),
      ...(runnerChanged ? { coordinator_id: runner.userId } : {}),
    }),
    onSuccess: () => { setEditing(false); onSaved() },
  })

  if (!editing) {
    return (
      <div className="mt-3 border-t border-neutral-100 pt-3">
        <button type="button" className={secondaryButton}
                onClick={() => { setForm(initial); setRunner(initialRunner); setEditing(true) }}>
          Change the arrangements
        </button>
      </div>
    )
  }

  return (
    <div className="mt-3 grid gap-3 border-t border-neutral-100 pt-3 sm:grid-cols-2">
      <Field label="Date">
        <input type="date" className={inputClass} value={form.session_date}
               onChange={(e) => set('session_date', e.target.value)} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Starts">
          <input type="time" className={inputClass} value={form.start_time}
                 onChange={(e) => set('start_time', e.target.value)} />
        </Field>
        <Field label="Ends">
          <input type="time" className={inputClass} value={form.end_time}
                 onChange={(e) => set('end_time', e.target.value)} />
        </Field>
      </div>
      {mayReassign && (
        <Field label="Who runs it" hint="They are given the session’s task; the previous person’s is closed">
          <PersonPicker value={runner} onChange={setRunner} allowExternal={false} />
        </Field>
      )}
      <Field label="Where">
        <input className={inputClass} value={form.location_text}
               onChange={(e) => set('location_text', e.target.value)}
               placeholder="Training room, or the line itself" />
      </Field>
      <Field label="Instructor" hint="Often an outside trainer with no UniOps account">
        <input className={inputClass} value={form.instructor_name}
               onChange={(e) => set('instructor_name', e.target.value)}
               placeholder="Name of whoever taught it" />
      </Field>
      <div className="sm:col-span-2">
        <Field label="Notes">
          <textarea className={inputClass} rows={3} value={form.notes}
                    onChange={(e) => set('notes', e.target.value)} />
        </Field>
      </div>
      <div className="sm:col-span-2">
        <ErrorNote error={save.error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={secondaryButton} disabled={save.isPending}
                  onClick={() => setEditing(false)}>
            Cancel
          </button>
          <button type="button" className={primaryButton}
                  disabled={save.isPending || (changed.length === 0 && !runnerChanged) || !form.session_date}
                  onClick={() => { if (!save.isPending) save.mutate() }}>
            {save.isPending ? 'Saving…' : 'Save the arrangements'}
          </button>
        </div>
      </div>
    </div>
  )
}

/**
 * The session's quiz: its code for the room, and how the room did.
 *
 * Round 6: "place the QR code for the quiz on the course session. place the
 * quiz results also on the course session to allow the run by person easier
 * visibility of the quiz results."
 */
function QuizCard({ session, mayRun }: { session: Session; mayRun: boolean }) {
  const [showing, setShowing] = useState(false)
  const results = useQuery({
    queryKey: ['course-session', session.id, 'quiz-results'],
    queryFn: () => api.get<QuizResult[]>(`/api/v1/course-sessions/${session.id}/quiz-results`),
    enabled: mayRun,
  })
  return (
    <Card className="mb-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <SectionTitle className="mb-0.5">Quiz</SectionTitle>
          <p className="text-sm text-neutral-800">{session.quiz_title}</p>
        </div>
        {mayRun && session.status === 'scheduled' && session.quiz_qr_token && (
          <button type="button" className={primaryButton} onClick={() => setShowing(true)}>
            <QrCode className="h-4 w-4" aria-hidden />
            Show the quiz code
          </button>
        )}
      </div>
      {mayRun && (
        <div className="mt-3 border-t border-neutral-100 pt-3">
          {results.isError ? <LoadFailedNote what="The quiz results" /> : results.isPending ? (
            <p className="text-xs text-neutral-500">Loading the results…</p>
          ) : (
            (results.data ?? []).length === 0
              ? <p className="text-xs text-neutral-500">Nobody has answered it at this session yet.</p>
              : (
                <ul className="flex flex-col gap-1">
                  {(results.data ?? []).map((r, i) => (
                    <li key={`${r.user_id}-${i}`} className="flex items-center gap-3 text-sm">
                      <span className="min-w-0 flex-1 truncate text-neutral-800">{r.user_name}</span>
                      <span className={`tabular-nums font-semibold ${r.passed ? 'text-success-700' : 'text-warning-700'}`}>
                        {Math.round(r.score_percent)}% {r.passed ? 'passed' : 'not passed'}
                      </span>
                      <span className="shrink-0 text-xs tabular-nums text-neutral-500">
                        {formatDateTime(r.submitted_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )
          )}
        </div>
      )}
      {showing && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 bg-white p-6"
             role="dialog" aria-modal="true" aria-label="Scan to take the quiz">
          <QrPanel token={session.quiz_qr_token} caption="Scan to take the quiz" sub={session.title} size={300} />
          <button type="button" className={secondaryButton} onClick={() => setShowing(false)}>
            <X className="h-4 w-4" aria-hidden />
            Close
          </button>
        </div>
      )}
    </Card>
  )
}

function RosterPanel({
  session, roster, mayWrite, onChanged,
}: {
  session: Session; roster: Enrolment[]; mayWrite: boolean; onChanged: () => void
}) {
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const [marks, setMarks] = useState<Record<string, string>>({})
  const open = session.status === 'scheduled'

  // Somebody already marked keeps their result until it is changed here; the
  // draft only holds what this pass has touched.
  useEffect(() => { setMarks({}) }, [session.id])

  const add = useMutation({
    mutationFn: (userId: string) =>
      api.post(`/api/v1/course-sessions/${session.id}/roster`, { user_ids: [userId] }),
    onSuccess: () => { setPerson(EMPTY_PERSON); onChanged() },
  })
  const remove = useMutation({
    mutationFn: (enrolmentId: string) =>
      api.del(`/api/v1/course-sessions/${session.id}/roster/${enrolmentId}`),
    onSuccess: onChanged,
  })
  const quizMark = useMutation({
    mutationFn: ({ id, passed }: { id: string; passed: boolean }) =>
      api.post(`/api/v1/course-sessions/${session.id}/roster/${id}/quiz`, { passed }),
    onSuccess: onChanged,
  })
  // Passed the quiz, not yet marked: closing would record them as not attended.
  const waiting = roster.filter((e) =>
    e.quiz_passed_at && (marks[e.id] ?? e.result) === 'enrolled').length
  const save = useMutation({
    mutationFn: ({ complete, anyway = false }: { complete: boolean; anyway?: boolean }) =>
      api.post(`/api/v1/course-sessions/${session.id}/results`, {
        results: Object.entries(marks).map(([enrolment_id, result]) => ({ enrolment_id, result })),
        complete,
        close_anyway: anyway,
      }),
    onSuccess: () => { setMarks({}); onChanged() },
  })
  // ★ The server names who passed the quiz but is unmarked and holds the close
  // (409); closing anyway records them as not attended, so it is asked for.
  const heldBack = errorCode(save.error) === 'unmarked_passes'
    && save.variables?.complete && open

  return (
    <>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <SectionTitle>Roster</SectionTitle>
        <span className="text-xs text-neutral-500">
          {roster.length} enrolled{session.passed ? ` · ${session.passed} certified` : ''}
        </span>
      </div>

      {open && (
        <Card className="mb-3">
          <Field label="Add somebody">
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="min-w-0 flex-1">
                <PersonPicker value={person} onChange={setPerson} allowExternal={false} />
              </div>
              <button type="button" className={primaryButton}
                      disabled={!person.userId || add.isPending}
                      onClick={() => { if (person.userId && !add.isPending) add.mutate(person.userId) }}>
                <Plus className="h-4 w-4" aria-hidden />
                Add
              </button>
            </div>
          </Field>
          <ErrorNote error={add.error} />
        </Card>
      )}

      {roster.length === 0 ? (
        <Empty>
          Nobody on the roster yet. Whoever is running this session adds the
          people from their area; they are reminded the day before it runs.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {roster.map((e) => {
            const current = marks[e.id] ?? (e.result === 'enrolled' ? '' : e.result)
            return (
              <li key={e.id}>
                <Card>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-semibold text-neutral-900">
                        {e.user_name}
                      </span>
                      {e.department_name && (
                        <span className="block text-xs text-neutral-500">{e.department_name}</span>
                      )}
                      {/* ★ Round 5, box 4. The code is a fixed URL; whoever runs the
                          session sees who let themselves in, and can remove anybody
                          who was not in the room before closing it. */}
                      {e.joined_via === 'quiz' && (
                        <span className="mt-0.5 mr-2 inline-flex items-center gap-1 rounded bg-info-50 px-1.5 py-0.5 text-xs text-info-700">
                          <QrCode className="h-3 w-3" aria-hidden />
                          Scanned in
                          {e.marked_by_name === 'Online quiz' && e.result !== 'enrolled' && ' · marked by the quiz'}
                        </span>
                      )}
                      {session.quiz_id && e.quiz_passed_at && (
                        <span className="mt-0.5 mr-2 inline-flex items-center gap-1 rounded bg-success-50 px-1.5 py-0.5 text-xs text-success-700">
                          <Check className="h-3 w-3" aria-hidden />
                          Quiz passed{e.quiz_marked_by_name === 'Online quiz' ? ' online' : ` · marked by ${e.quiz_marked_by_name ?? 'hand'}`}
                        </span>
                      )}
                      {e.training_record_id && (
                        <span className="mt-0.5 inline-flex items-center gap-1 text-xs text-success-700">
                          <Check className="h-3 w-3" aria-hidden />
                          Certificate issued
                          {e.marked_at && ` ${formatDateTime(e.marked_at)}`}
                        </span>
                      )}
                    </span>

                    {mayWrite ? (
                      <div className="flex shrink-0 flex-wrap gap-1" role="group"
                           aria-label={`Result for ${e.user_name}`}>
                        {/* ★ Round 6: "add a button that indicates that the quiz has
                            been passed by the attendee." Set by itself when they pass
                            online; pressed here for a quiz done on paper. */}
                        {session.quiz_id && (
                          <button type="button" aria-pressed={Boolean(e.quiz_passed_at)}
                                  disabled={quizMark.isPending}
                                  onClick={() => { if (!quizMark.isPending) quizMark.mutate({ id: e.id, passed: !e.quiz_passed_at }) }}
                                  className={'min-h-[36px] rounded-lg px-2.5 text-xs font-semibold transition-colors ' +
                                    (e.quiz_passed_at ? 'bg-success-50 text-success-800 ring-1 ring-success-300'
                                      : 'border border-dashed border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50')}>
                            Quiz passed
                          </button>
                        )}
                        {RESULTS.map((r) => (
                          <button
                            key={r.key} type="button"
                            aria-pressed={current === r.key}
                            // ★ A pass needs the quiz first, where there is one.
                            disabled={r.key === 'passed' && Boolean(session.quiz_id)
                              && !e.quiz_passed_at && e.result !== 'passed'}
                            title={r.key === 'passed' && session.quiz_id && !e.quiz_passed_at
                              ? 'Needs the quiz passed first' : undefined}
                            onClick={() => setMarks((m) => ({ ...m, [e.id]: r.key }))}
                            className={
                              'min-h-[36px] rounded-lg px-2.5 text-xs font-semibold transition-colors ' +
                              (current === r.key
                                ? r.key === 'passed'
                                  ? 'bg-success-600 text-white'
                                  : r.key === 'failed'
                                    ? 'bg-danger-600 text-white'
                                    : 'bg-neutral-600 text-white'
                                : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50 disabled:opacity-40')
                            }
                          >
                            {r.label}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <span className="shrink-0 text-xs font-semibold text-neutral-600">
                        {e.result === 'enrolled' ? 'Not marked' : e.result}
                      </span>
                    )}

                    {open && !e.training_record_id && (
                      <button type="button" aria-label={`Remove ${e.user_name}`}
                              disabled={remove.isPending}
                              onClick={() => { if (!remove.isPending) remove.mutate(e.id) }}
                              className="shrink-0 rounded p-1.5 text-neutral-400 hover:bg-danger-50 hover:text-danger-600">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </div>
                </Card>
              </li>
            )
          })}
        </ul>
      )}

      <ErrorNote error={save.error || remove.error || quizMark.error} />
      {heldBack && mayWrite && (
        <div className="mt-2 flex justify-end">
          <button type="button" className={secondaryButton} disabled={save.isPending}
                  onClick={() => { if (!save.isPending) save.mutate({ complete: true, anyway: true }) }}>
            Close anyway and record them as not attended
          </button>
        </div>
      )}

      {mayWrite && roster.length > 0 && (
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <button type="button" className={secondaryButton}
                  disabled={Object.keys(marks).length === 0 || save.isPending}
                  onClick={() => { if (!save.isPending) save.mutate({ complete: false }) }}>
            Save results
          </button>
          {open && (
            <button type="button" className={primaryButton} disabled={save.isPending}
                    onClick={() => { if (!save.isPending) save.mutate({ complete: true }) }}>
              {save.isPending ? 'Saving…' : 'Save and close the session'}
            </button>
          )}
        </div>
      )}
      {open && waiting > 0 && (
        <p className="mt-2 text-right text-xs font-medium text-warning-700">
          {waiting} {waiting === 1 ? 'person has' : 'people have'} passed the quiz but {waiting === 1 ? 'is' : 'are'} not marked yet.
        </p>
      )}
      {!open && session.status === 'completed' && (
        <ReopenBox session={session} onDone={onChanged} />
      )}
      {open && session.reopen_reason && (
        <p className="mt-2 text-right text-xs text-warning-700">
          Reopened {session.reopened_at && formatDateTime(session.reopened_at)}
          {session.reopened_by_name && ` by ${session.reopened_by_name}`}: {session.reopen_reason}.
          Closing it again withdraws the certificate of anyone no longer marked Passed.
        </p>
      )}
      {open && (
        <p className="mt-2 text-right text-xs text-neutral-500">
          Closing issues a certificate to everyone marked Passed, and records anyone still
          unmarked as having not attended.
        </p>
      )}
    </>
  )
}

/**
 * ★ Review (uniops-8b): a closed session is read only, and the way to correct
 * one used to point at the training record, where nothing can be changed. HSE
 * reopens it saying why; closing it again issues what is now owed and
 * withdraws what this session issued to anyone no longer marked Passed.
 */
function ReopenBox({ session, onDone }: { session: Session; onDone: () => void }) {
  const { can } = usePermissions()
  const [asking, setAsking] = useState(false)
  const [reason, setReason] = useState('')
  const reopen = useMutation({
    mutationFn: () => api.post(`/api/v1/course-sessions/${session.id}/reopen`, { reason: reason.trim() }),
    onSuccess: () => { setAsking(false); setReason(''); onDone() },
  })
  const mayReopen = can('ehs.training.write')
  return (
    <div className="mt-2 flex flex-col items-end gap-2 text-xs text-neutral-500">
      <p className="text-right">
        Closed and certificates issued — read only.
        {mayReopen ? ' To correct a result, reopen it.' : ' HSE can reopen it to correct a result.'}
      </p>
      {mayReopen && !asking && (
        <button type="button" className={secondaryButton} onClick={() => setAsking(true)}>
          Reopen to correct
        </button>
      )}
      {mayReopen && asking && (
        <div className="w-full max-w-md rounded-lg border border-neutral-200 bg-neutral-50 p-2.5 text-left">
          <label className="block font-medium text-neutral-700">
            Why is it being reopened?
            <textarea rows={2} className={`${inputClass} mt-1 py-2`} value={reason}
                      onChange={(e) => setReason(e.target.value)} />
          </label>
          <ErrorNote error={reopen.error} />
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" className={secondaryButton} disabled={reopen.isPending}
                    onClick={() => setAsking(false)}>Cancel</button>
            <button type="button" className={primaryButton}
                    disabled={reason.trim().length < 5 || reopen.isPending}
                    onClick={() => { if (!reopen.isPending) reopen.mutate() }}>
              {reopen.isPending ? 'Reopening…' : 'Reopen the session'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
