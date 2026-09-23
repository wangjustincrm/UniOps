/**
 * Recording who a recommendation was put in front of.
 *
 * ★ Sending does not start or move the twenty-one day clock — that runs from
 * when the recommendation was made. If it moved, not sending would extend a
 * statutory deadline indefinitely.
 */
function SendForm({ recId, onDone, onCancel }: {
  recId: string; onDone: () => void; onCancel: () => void
}) {
  const [chosen, setChosen] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const people = useQuery({
    queryKey: ['toolbox-supervisors', true],
    queryFn: () => api.get<{ id: string; full_name: string; role: string }[]>(
      '/api/v1/toolbox-talks/supervisors?everyone=true'),
  })
  const send = useMutation({
    mutationFn: () => api.post(`/api/v1/jhsc/recommendations/${recId}/send`,
                               { user_ids: chosen }),
    onSuccess: onDone,
  })

  const loaded = people.data ?? []
  const shown = search.trim()
    ? loaded.filter((p) => chosen.includes(p.id)
        || p.full_name.toLowerCase().includes(search.trim().toLowerCase()))
    : loaded.slice(0, 12)

  return (
    <div className="mt-2 rounded-lg border border-neutral-200 p-2.5">
      <p className="mb-1.5 text-xs font-medium text-neutral-700">
        Who is this being put in front of?
      </p>
      <input className={`${inputClass} mb-1.5`} value={search}
             onChange={(e) => setSearch(e.target.value)}
             placeholder="Search by name" aria-label="Search people" />
      <ul className="mb-2 flex max-h-40 flex-col gap-0.5 overflow-y-auto">
        {shown.map((p) => (
          <li key={p.id}>
            <label className="flex min-h-[32px] items-center gap-2 text-sm text-neutral-800">
              <input type="checkbox" checked={chosen.includes(p.id)}
                     onChange={(e) => setChosen(e.target.checked
                       ? [...chosen, p.id]
                       : chosen.filter((x) => x !== p.id))} />
              {p.full_name}
            </label>
          </li>
        ))}
      </ul>
      <ErrorNote error={send.error} />
      <div className="flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onCancel}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={chosen.length === 0 || send.isPending}
                onClick={() => { if (!send.isPending) send.mutate() }}>
          {send.isPending ? 'Sending…' : 'Send it'}
        </button>
      </div>
    </div>
  )
}

/**
 * One committee meeting: who came, what was recommended, and the minutes.
 *
 * Attendance is a pre-seeded list of the current committee rather than an empty
 * box, because the question at the meeting is "who is missing", not "type
 * twenty-three names". Everyone starts absent: marking who came is fewer taps
 * than marking who did not, and an unmarked sheet is honestly empty rather than
 * falsely full.
 *
 * Publishing needs minutes. The published record is what goes on the
 * noticeboard and to the Ministry if asked, and a published blank would satisfy
 * the dashboard and nothing else.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useParams } from 'react-router-dom'
import { useTabTitle } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'
import { Plus, Send } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import {
  Card, Empty, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle,
} from '@/components/ui'
import { ReplyBadge, type Recommendation } from '@/pages/JhscPage'

interface Meeting {
  id: string; meeting_no: string; title: string | null; held_on: string
  location_text: string | null; agenda: string | null; minutes: string | null
  minutes_submission_id: string | null
  status: string; published_at: string | null; inspection_department_name: string | null
}
interface Attendance {
  id: string; user_id: string | null; user_name: string
  representation: string | null; attendance: string
}

const MARKS = [
  { key: 'present', label: 'Present' },
  { key: 'regrets', label: 'Regrets' },
  { key: 'absent', label: 'Absent' },
] as const

export default function JhscMeetingPage() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const mayManage = can('ehs.jhsc.manage')

  const meeting = useQuery({
    queryKey: ['jhsc-meeting', id],
    queryFn: () => api.get<Meeting>(`/api/v1/jhsc/meetings/${id}`),
    enabled: Boolean(id),
  })
  const attendance = useQuery({
    queryKey: ['jhsc-attendance', id],
    queryFn: () => api.get<Attendance[]>(`/api/v1/jhsc/meetings/${id}/attendance`),
    enabled: Boolean(id),
  })
  const recommendations = useQuery({
    queryKey: ['jhsc-recommendations', id],
    queryFn: () => api.get<Recommendation[]>(`/api/v1/jhsc/recommendations?meeting_id=${id}`),
    enabled: Boolean(id),
  })

  useTabTitle(ehsRoutes, meeting.data?.meeting_no)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['jhsc-meeting', id] })
    queryClient.invalidateQueries({ queryKey: ['jhsc-attendance', id] })
    queryClient.invalidateQueries({ queryKey: ['jhsc-recommendations'] })
    queryClient.invalidateQueries({ queryKey: ['jhsc-meetings'] })
  }

  if (meeting.isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  if (!meeting.data) return <p className="p-4 text-sm text-neutral-500">Meeting not found.</p>
  const m = meeting.data
  const published = m.status === 'published'
  const rows = attendance.data ?? []
  const present = rows.filter((a) => a.attendance === 'present').length

  return (
    <div className="p-4">
      <PageHeader
        title={m.title || `Meeting of ${formatDate(m.held_on)}`}
        subtitle={`${m.meeting_no} · ${formatDate(m.held_on)}`
          + (m.location_text ? ` · ${m.location_text}` : '')}
      >
        {published && (
          <span className="rounded-full bg-success-50 px-2.5 py-0.5 text-xs font-semibold text-success-700">
            Published {m.published_at && formatDate(m.published_at)}
          </span>
        )}
      </PageHeader>

      {/* ── Attendance ──────────────────────────────────────────────────── */}
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <SectionTitle>Attendance</SectionTitle>
        <span className="text-xs text-neutral-500">
          {present} of {rows.length} present
        </span>
      </div>
      {rows.length === 0 ? (
        <Empty>
          The committee roster was empty when this meeting was created, so there
          is nobody to mark. Add members, then create the next meeting.
        </Empty>
      ) : (
        <AttendanceList meetingId={id} rows={rows} editable={mayManage && !published}
                        onSaved={invalidate} />
      )}

      {/* ── Recommendations ─────────────────────────────────────────────── */}
      <SectionTitle className="mb-2 mt-6">Recommendations</SectionTitle>
      <RecommendationList
        meetingId={id} rows={recommendations.data ?? []} mayManage={mayManage}
        onChanged={invalidate}
      />

      {/* ── Agenda and minutes ──────────────────────────────────────────── */}
      <SectionTitle className="mb-2 mt-6">Minutes</SectionTitle>
      <MinutesPanel meeting={m} editable={mayManage && !published} onSaved={invalidate} />
    </div>
  )
}

function AttendanceList({
  meetingId, rows, editable, onSaved,
}: {
  meetingId: string; rows: Attendance[]; editable: boolean; onSaved: () => void
}) {
  const [draft, setDraft] = useState<Record<string, string>>({})
  const save = useMutation({
    mutationFn: () => api.post(`/api/v1/jhsc/meetings/${meetingId}/attendance`, {
      marks: Object.entries(draft).map(([id, attendance]) => ({ id, attendance })),
    }),
    onSuccess: () => { setDraft({}); onSaved() },
  })

  return (
    <>
      <ul className="flex flex-col gap-1.5">
        {rows.map((a) => {
          const current = draft[a.id] ?? a.attendance
          return (
            <li key={a.id}>
              <Card className="!p-2.5">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm text-neutral-900">{a.user_name}</span>
                    <span className="block text-xs text-neutral-500">
                      {a.representation === 'worker' ? 'Worker rep' : 'Management rep'}
                    </span>
                  </span>
                  {editable ? (
                    <div className="flex shrink-0 gap-1" role="group"
                         aria-label={`Attendance for ${a.user_name}`}>
                      {MARKS.map((mk) => (
                        <button key={mk.key} type="button" aria-pressed={current === mk.key}
                                onClick={() => setDraft((d) => ({ ...d, [a.id]: mk.key }))}
                                className={
                                  'min-h-[36px] rounded-lg px-2.5 text-xs font-semibold transition-colors ' +
                                  (current === mk.key
                                    ? mk.key === 'present'
                                      ? 'bg-success-600 text-white'
                                      : 'bg-neutral-600 text-white'
                                    : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50')
                                }>
                          {mk.label}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <span className={
                      'shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ' +
                      (a.attendance === 'present'
                        ? 'bg-success-50 text-success-700'
                        : 'bg-neutral-100 text-neutral-600')
                    }>
                      {MARKS.find((mk) => mk.key === a.attendance)?.label ?? a.attendance}
                    </span>
                  )}
                </div>
              </Card>
            </li>
          )
        })}
      </ul>
      <ErrorNote error={save.error} />
      {editable && Object.keys(draft).length > 0 && (
        <div className="mt-2 flex justify-end">
          <button type="button" className={primaryButton} disabled={save.isPending}
                  onClick={() => { if (!save.isPending) save.mutate() }}>
            {save.isPending ? 'Saving…' : 'Save attendance'}
          </button>
        </div>
      )}
    </>
  )
}

function RecommendationList({
  meetingId, rows, mayManage, onChanged,
}: {
  meetingId: string; rows: Recommendation[]; mayManage: boolean; onChanged: () => void
}) {
  const [adding, setAdding] = useState(false)
  const [text, setText] = useState('')
  const [replyTo, setReplyTo] = useState<string | null>(null)
  // Who it was put in front of. Without it the committee could see that
  // nobody had answered and not who had failed to.
  const [sendingTo, setSendingTo] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () => api.post(`/api/v1/jhsc/meetings/${meetingId}/recommendations`,
                               { recommendation: text }),
    onSuccess: () => { setText(''); setAdding(false); onChanged() },
  })

  return (
    <>
      {rows.length === 0 && !adding && (
        <Empty>
          Nothing recommended at this meeting. Recording one starts the
          twenty-one days OHSA s.9(20) gives for a written reply.
        </Empty>
      )}

      <ul className="flex flex-col gap-2">
        {rows.map((r) => (
          <li key={r.id}>
            <Card>
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="font-mono text-xs text-neutral-400">#{r.seq}</span>
                <span className="min-w-0 flex-1 text-sm text-neutral-900">{r.recommendation}</span>
                <ReplyBadge rec={r} />
              </div>
              {r.sent_at ? (
                <p className="mt-1.5 text-xs text-neutral-500">
                  Sent to {r.sent_to.map((p) => p.name).join(', ') || 'nobody recorded'}
                  {' '}on {formatDate(r.sent_at)}
                  {r.sent_by_name && ` by ${r.sent_by_name}`}
                </p>
              ) : mayManage && (
                sendingTo === r.id
                  ? <SendForm recId={r.id} onDone={() => { setSendingTo(null); onChanged() }}
                              onCancel={() => setSendingTo(null)} />
                  : (
                    <button type="button" onClick={() => setSendingTo(r.id)}
                            className="mt-1.5 text-xs font-semibold text-neutral-500 hover:text-neutral-900 hover:underline">
                      Record who this was sent to
                    </button>
                  )
              )}

              {r.response ? (
                <p className="mt-2 border-l-2 border-neutral-200 pl-2.5 text-sm text-neutral-700">
                  {r.response}
                  <span className="mt-0.5 block text-xs text-neutral-500">
                    {r.responded_by_name} · {r.responded_at && formatDate(r.responded_at)}
                  </span>
                </p>
              ) : mayManage && (
                replyTo === r.id
                  ? <ReplyForm recId={r.id} onDone={() => { setReplyTo(null); onChanged() }}
                               onCancel={() => setReplyTo(null)} />
                  : (
                    <button type="button" onClick={() => setReplyTo(r.id)}
                            className="mt-2 text-xs font-semibold text-primary-700 hover:underline">
                      Write the reply
                    </button>
                  )
              )}
            </Card>
          </li>
        ))}
      </ul>

      {mayManage && (
        adding ? (
          <Card className="mt-2">
            <Field label="Recommendation">
              <textarea className={`${inputClass} min-h-[80px] py-2`} value={text}
                        onChange={(e) => setText(e.target.value)}
                        placeholder="What the committee recommends the employer do" />
            </Field>
            <ErrorNote error={create.error} />
            <div className="mt-2 flex justify-end gap-2">
              <button type="button" className={secondaryButton}
                      onClick={() => setAdding(false)}>Cancel</button>
              <button type="button" className={primaryButton}
                      disabled={!text.trim() || create.isPending}
                      onClick={() => { if (!create.isPending) create.mutate() }}>
                {create.isPending ? 'Recording…' : 'Record'}
              </button>
            </div>
          </Card>
        ) : (
          <button type="button" className={`${secondaryButton} mt-2`} onClick={() => setAdding(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            Add a recommendation
          </button>
        )
      )}
    </>
  )
}

function ReplyForm(
  { recId, onDone, onCancel }: { recId: string; onDone: () => void; onCancel: () => void },
) {
  const [response, setResponse] = useState('')
  const [outcome, setOutcome] = useState('accepted')
  const send = useMutation({
    mutationFn: () => api.post(`/api/v1/jhsc/recommendations/${recId}/respond`,
                               { response, outcome }),
    onSuccess: onDone,
  })

  return (
    <div className="mt-2 border-t border-neutral-100 pt-2">
      <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
        <Field label="Written response">
          <textarea className={`${inputClass} min-h-[70px] py-2`} value={response}
                    onChange={(e) => setResponse(e.target.value)}
                    placeholder="What the employer will do, and by when" />
        </Field>
        <Field label="Outcome">
          <select className={inputClass} value={outcome}
                  onChange={(e) => setOutcome(e.target.value)}>
            <option value="accepted">Accepted</option>
            <option value="declined">Declined</option>
            <option value="deferred">Deferred</option>
          </select>
        </Field>
      </div>
      <ErrorNote error={send.error} />
      <div className="mt-2 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onCancel}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!response.trim() || send.isPending}
                onClick={() => { if (!send.isPending) send.mutate() }}>
          <Send className="h-4 w-4" aria-hidden />
          {send.isPending ? 'Sending…' : 'Send the reply'}
        </button>
      </div>
      <p className="mt-1 text-right text-xs text-neutral-500">
        This stops the twenty-one day clock and cannot be rewritten afterwards.
      </p>
    </div>
  )
}

function MinutesPanel(
  { meeting, editable, onSaved }: { meeting: Meeting; editable: boolean; onSaved: () => void },
) {
  const [agenda, setAgenda] = useState(meeting.agenda ?? '')
  const [minutes, setMinutes] = useState(meeting.minutes ?? '')

  // Which form the minutes are written on, if Settings names one. A meeting
  // written before a template was chosen keeps its text box and still reads:
  // choosing one changes the next meeting, not the last.
  const config = useQuery({
    queryKey: ['settings', 'config'],
    queryFn: () => api.get<{ jhsc_minutes_template_code: string | null }>(
      '/api/v1/settings/config'),
  })
  const templateCode = config.data?.jhsc_minutes_template_code || null
  const onAForm = Boolean(templateCode) && !meeting.minutes

  const save = useMutation({
    mutationFn: () => api.patch(`/api/v1/jhsc/meetings/${meeting.id}`,
                                { agenda: agenda || null, minutes: minutes || null,
                                  status: minutes.trim() ? 'held' : 'scheduled' }),
    onSuccess: onSaved,
  })
  const publish = useMutation({
    mutationFn: () => api.post(`/api/v1/jhsc/meetings/${meeting.id}/publish`, {}),
    onSuccess: onSaved,
  })

  if (!editable) {
    return (
      <Card>
        {meeting.agenda && (
          <>
            <p className="text-xs font-medium text-neutral-500">Agenda</p>
            <p className="mb-3 whitespace-pre-wrap text-sm text-neutral-700">{meeting.agenda}</p>
          </>
        )}
        {meeting.minutes
          ? <p className="whitespace-pre-wrap text-sm text-neutral-900">{meeting.minutes}</p>
          : <p className="text-sm text-neutral-500">No minutes recorded.</p>}
      </Card>
    )
  }

  return (
    <Card>
      <div className="grid gap-3">
        <Field label="Agenda">
          <textarea className={`${inputClass} min-h-[70px] py-2`} value={agenda}
                    onChange={(e) => setAgenda(e.target.value)} />
        </Field>
        {onAForm ? (
          <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
            <p className="text-xs font-medium text-neutral-700">Minutes</p>
            <p className="mt-1 max-w-[68ch] text-xs text-neutral-600">
              These are written on the <span className="font-medium">{templateCode}</span>{' '}
              form, so the headings can change as the committee's practice does.
              Fill it in under Forms and attach it here.
            </p>
            <Link to={`/forms?template=${encodeURIComponent(templateCode ?? '')}`}
                  className="mt-2 inline-block text-xs font-semibold text-primary-700 hover:underline">
              Open the minutes form
            </Link>
            {meeting.minutes_submission_id && (
              <p className="mt-1.5 text-xs text-success-700">
                A filled-in form is attached to this meeting.
              </p>
            )}
          </div>
        ) : (
          <Field label="Minutes"
                 hint="These are what goes on the noticeboard and to the Ministry if asked">
            <textarea className={`${inputClass} min-h-[140px] py-2`} value={minutes}
                      onChange={(e) => setMinutes(e.target.value)} />
          </Field>
        )}
      </div>
      <ErrorNote error={save.error || publish.error} />
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button type="button" className={secondaryButton} disabled={save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}>
          Save draft
        </button>
        <button type="button" className={primaryButton}
                disabled={!minutes.trim() || publish.isPending || save.isPending}
                onClick={async () => {
                  if (publish.isPending || save.isPending) return
                  await save.mutateAsync()
                  publish.mutate()
                }}>
          {publish.isPending ? 'Publishing…' : 'Publish the minutes'}
        </button>
      </div>
    </Card>
  )
}
