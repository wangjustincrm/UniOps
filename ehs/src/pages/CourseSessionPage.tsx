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
import { Check, Plus, QrCode, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
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
}
interface Enrolment {
  id: string; user_id: string | null; user_name: string; department_name: string | null
  result: string; marked_by_name: string | null; marked_at: string | null
  // roster | quiz — quiz means they scanned the quiz code in the room.
  joined_via: string
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
          </Detail>
          <Detail label="Where">{s.location_text ?? '—'}</Detail>
          <Detail label="Run by">{s.coordinator_name ?? 'Not assigned'}</Detail>
          <Detail label="Instructor">{s.instructor_name ?? 'Not recorded yet'}</Detail>
        </dl>
        {s.notes && <p className="mt-2 text-sm text-neutral-700">{s.notes}</p>}
        {mayRun && open && <ArrangementsEditor key={s.id} session={s} onSaved={invalidate} />}
      </Card>

      {/* ★ A failed roster read is not an empty roster: "Nobody on the roster"
          is a statement about the session, and making it on the strength of a
          request that never answered is how somebody enrols a student twice. */}
      {roster.isError ? (
        <Card className="mt-4"><LoadFailedNote what="The roster" /></Card>
      ) : (
        <RosterPanel
          session={s} roster={roster.data ?? []} mayWrite={mayRun}
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
function ArrangementsEditor({ session, onSaved }: { session: Session; onSaved: () => void }) {
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
    mutationFn: () => api.patch(`/api/v1/course-sessions/${session.id}`,
      Object.fromEntries(changed.map((k) => [k, form[k] === '' ? null : form[k]]))),
    onSuccess: () => { setEditing(false); onSaved() },
  })

  if (!editing) {
    return (
      <div className="mt-3 border-t border-neutral-100 pt-3">
        <button type="button" className={secondaryButton}
                onClick={() => { setForm(initial); setEditing(true) }}>
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
                  disabled={save.isPending || changed.length === 0 || !form.session_date}
                  onClick={() => { if (!save.isPending) save.mutate() }}>
            {save.isPending ? 'Saving…' : 'Save the arrangements'}
          </button>
        </div>
      </div>
    </div>
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
  const save = useMutation({
    mutationFn: (complete: boolean) => api.post(`/api/v1/course-sessions/${session.id}/results`, {
      results: Object.entries(marks).map(([enrolment_id, result]) => ({ enrolment_id, result })),
      complete,
    }),
    onSuccess: () => { setMarks({}); onChanged() },
  })

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
                      {e.training_record_id && (
                        <span className="mt-0.5 inline-flex items-center gap-1 text-xs text-success-700">
                          <Check className="h-3 w-3" aria-hidden />
                          Certificate issued
                          {e.marked_at && ` ${formatDateTime(e.marked_at)}`}
                        </span>
                      )}
                    </span>

                    {mayWrite ? (
                      <div className="flex shrink-0 gap-1" role="group"
                           aria-label={`Result for ${e.user_name}`}>
                        {RESULTS.map((r) => (
                          <button
                            key={r.key} type="button"
                            aria-pressed={current === r.key}
                            onClick={() => setMarks((m) => ({ ...m, [e.id]: r.key }))}
                            className={
                              'min-h-[36px] rounded-lg px-2.5 text-xs font-semibold transition-colors ' +
                              (current === r.key
                                ? r.key === 'passed'
                                  ? 'bg-success-600 text-white'
                                  : r.key === 'failed'
                                    ? 'bg-danger-600 text-white'
                                    : 'bg-neutral-600 text-white'
                                : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50')
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

      <ErrorNote error={save.error || remove.error} />

      {mayWrite && roster.length > 0 && (
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <button type="button" className={secondaryButton}
                  disabled={Object.keys(marks).length === 0 || save.isPending}
                  onClick={() => { if (!save.isPending) save.mutate(false) }}>
            Save results
          </button>
          {open && (
            <button type="button" className={primaryButton} disabled={save.isPending}
                    onClick={() => { if (!save.isPending) save.mutate(true) }}>
              {save.isPending ? 'Saving…' : 'Save and close the session'}
            </button>
          )}
        </div>
      )}
      {open && (
        <p className="mt-2 text-right text-xs text-neutral-500">
          Closing records anyone still unmarked as having not attended.
        </p>
      )}
    </>
  )
}
