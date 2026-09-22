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
import { Check, Plus, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate, formatDateTime } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import {
  Card, Empty, ErrorNote, Field, PageHeader, inputClass, primaryButton, secondaryButton,
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
  const mayWrite = can('ehs.training.write')

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
  if (!session.data) return <p className="p-4 text-sm text-neutral-500">Session not found.</p>
  const s = session.data
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
        {mayWrite && open && <InstructorField session={s} onSaved={invalidate} />}
      </Card>

      <RosterPanel
        session={s} roster={roster.data ?? []} mayWrite={mayWrite}
        onChanged={invalidate}
      />
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

function InstructorField({ session, onSaved }: { session: Session; onSaved: () => void }) {
  const [value, setValue] = useState(session.instructor_name ?? '')
  const save = useMutation({
    mutationFn: () => api.patch(`/api/v1/course-sessions/${session.id}`,
                                { instructor_name: value || null }),
    onSuccess: onSaved,
  })
  return (
    <div className="mt-3 border-t border-neutral-100 pt-3">
      <Field label="Instructor" hint="Often an outside trainer with no UniOps account">
        <div className="flex gap-2">
          <input className={inputClass} value={value} onChange={(e) => setValue(e.target.value)}
                 placeholder="Name of whoever taught it" />
          <button type="button" className={secondaryButton}
                  disabled={save.isPending || value === (session.instructor_name ?? '')}
                  onClick={() => { if (!save.isPending) save.mutate() }}>
            Save
          </button>
        </div>
      </Field>
      <ErrorNote error={save.error} />
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
        <h2 className="text-xs font-bold uppercase tracking-wider text-neutral-400">
          Roster
        </h2>
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
