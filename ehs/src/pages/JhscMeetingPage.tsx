interface PreviewRecipient {
  id: string; name: string; email: string | null
  will_receive: boolean; reason: string | null
}
interface Preview {
  subject: string; body: string
  recipients: PreviewRecipient[]
  unreachable: string[]
  seq_will_be: number
}

/**
 * Choosing who has to answer, seeing what they will be sent, and sending it.
 *
 * ★ Two requests from the third round, and they belong together: "allow the
 * recommendation and the staff to send it to in one entry process" and "create
 * a process to review the communication in draft mode before it is sent to
 * allow for configuration before sending it".
 *
 * So recording and sending are one flow, with the message shown in between.
 * The preview is fetched from the server rather than composed here, because a
 * message composed twice is a message that drifts — and the half that drifts
 * is always the one nobody reads afterwards.
 *
 * ★ Sending does not start or move the twenty-one day clock — that runs from
 * when the recommendation was made. If it moved, not sending would extend a
 * statutory deadline indefinitely.
 */
function RecommendationComposer({
  meetingId, recId, recommendationText, onDone, onCancel,
}: {
  meetingId: string
  /** Set when an already-recorded recommendation is being sent. */
  recId?: string
  /** Set when the text is being written here, in the same step. */
  recommendationText?: string
  onDone: () => void
  onCancel: () => void
}) {
  const [chosen, setChosen] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [text, setText] = useState(recommendationText ?? '')
  // Round 8: where it happened, picked as on an incident. Only asked while
  // writing one; an existing recommendation carries its own.
  const [locationId, setLocationId] = useState<string | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  // The body once somebody has edited it. Null means "as the server composed it".
  //
  // ★ `touched` is the point. This used to hold whatever the first preview
  // returned and send it back on every later call — so after pressing Back,
  // rewording the recommendation and previewing again, the record kept the new
  // wording and the email carried the old one. A statutory notice that does not
  // match the record it notifies. Found in review by uniops-9a.
  const [edited, setEdited] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)
  // ★ Which wording the edited message was written against. Even with `touched`,
  // editing the message, going Back, rewording the recommendation and sending
  // leaves the record saying one thing and the email saying another — rarer than
  // before and the same outcome. Found in the second pass of the same review.
  // Neither silently overwritten nor silently sent: the reader is told and
  // chooses. */
  const [editedFor, setEditedFor] = useState('')

  const writing = recId === undefined
  const people = useQuery({
    queryKey: ['toolbox-supervisors', true],
    queryFn: () => api.get<{ id: string; full_name: string; role: string }[]>(
      '/api/v1/toolbox-talks/supervisors?everyone=true'),
  })

  // ★ The body to preview is passed in, never read from the closure.
  //
  // It used to be `message: touched ? edited : null`, and "Use the new wording"
  // called `setTouched(false); setEdited(null); draft.mutate()` — three things
  // whose order is React's business, not this component's. It happened to work,
  // because react-query re-reads `mutationFn` on every render, and that is a
  // fact about the library rather than about the code: the next person reading
  // this would have to know it to believe the button. Raised in review by
  // uniops-9a, whose reading of it was the one this code deserved.
  const draft = useMutation({
    mutationFn: (vars: { message: string | null }) =>
      api.post<Preview>('/api/v1/jhsc/recommendations/preview', {
        meeting_id: meetingId, recommendation: text, user_ids: chosen,
        // Which recommendation this is about, when it is one that exists. The
        // preview numbered everything as the next one otherwise.
        recommendation_id: recId ?? null,
        location_id: writing ? locationId : null,
        message: vars.message,
      }),
    // Judged on what was sent, not on what the state happens to say now.
    onSuccess: (p, vars) => {
      setPreview(p)
      if (vars.message === null) { setEdited(p.body); setTouched(false); setEditedFor('') }
    },
  })

  const submit = useMutation({
    mutationFn: () => (writing
      ? api.post(`/api/v1/jhsc/meetings/${meetingId}/recommendations`,
                 { recommendation: text, send_to: chosen, location_id: locationId,
                   message: touched ? edited : null })
      : api.post(`/api/v1/jhsc/recommendations/${recId}/send`,
                 { user_ids: chosen, message: touched ? edited : null })),
    onSuccess: onDone,
  })

  // Recording without naming anybody stays available: the clock runs from the
  // recording, so a committee that has not decided who answers can still start it.
  const recordOnly = useMutation({
    mutationFn: () => api.post(`/api/v1/jhsc/meetings/${meetingId}/recommendations`,
                               { recommendation: text, location_id: locationId }),
    onSuccess: onDone,
  })

  const loaded = people.data ?? []
  const shown = search.trim()
    ? loaded.filter((p) => chosen.includes(p.id)
        || p.full_name.toLowerCase().includes(search.trim().toLowerCase()))
    : loaded.slice(0, 12)

  const busy = draft.isPending || submit.isPending || recordOnly.isPending
  const ready = text.trim().length > 0

  if (preview) {
    return (
      <div className="mt-2 rounded-lg border border-primary-200 bg-primary-50/40 p-2.5">
        <p className="mb-1.5 text-xs font-semibold text-neutral-800">
          This is what will be sent — recommendation #{preview.seq_will_be}
        </p>
        <dl className="mb-2 text-xs">
          <dt className="text-neutral-500">Subject</dt>
          <dd className="mb-1.5 text-neutral-900">{preview.subject}</dd>
          <dt className="text-neutral-500">To</dt>
          <dd className="text-neutral-900">
            {preview.recipients.filter((r) => r.will_receive)
              .map((r) => r.name).join(', ') || 'nobody who can be emailed'}
          </dd>
        </dl>
        {/* ★ Said plainly, because the send drops these people silently. A
            recommendation "sent" to somebody with no address on file is owed a
            reply by somebody who was never told. */}
        {preview.unreachable.length > 0 && (
          <p className="mb-2 rounded-lg bg-warning-50 px-2.5 py-2 text-xs text-warning-800">
            <b>Will not receive the email:</b>{' '}
            {preview.recipients.filter((r) => !r.will_receive)
              .map((r) => `${r.name} (${r.reason ?? 'no address'})`).join(', ')}.
            They are still recorded as having been asked, and the reply is still
            owed — tell them another way.
          </p>
        )}
        {touched && editedFor !== text && (
          <p className="mb-1.5 flex flex-wrap items-center gap-2 rounded-lg bg-warning-50 px-2.5 py-2 text-xs text-warning-800">
            <span>
              You changed the recommendation after editing this message, so the
              message below still carries the old wording.
            </span>
            <button type="button"
                    onClick={() => draft.mutate({ message: null })}
                    className="font-semibold text-warning-900 underline">
              Use the new wording
            </button>
          </p>
        )}
        <label className="mb-1 block text-xs text-neutral-500" htmlFor="rec-body">
          Message — edit it if the committee worded it differently
        </label>
        <textarea id="rec-body" rows={6} className={`${inputClass} py-2 text-sm`}
                  value={edited ?? preview.body}
                  onChange={(e) => {
                   setEdited(e.target.value); setTouched(true); setEditedFor(text)
                 }} />
        <ErrorNote error={submit.error} />
        <div className="mt-2 flex justify-end gap-2">
          <button type="button" className={secondaryButton} disabled={busy}
                  onClick={() => setPreview(null)}>Back</button>
          <button type="button" className={primaryButton} disabled={busy}
                  onClick={() => { if (!busy) submit.mutate() }}>
            {submit.isPending ? 'Sending…' : writing ? 'Record and send' : 'Send it'}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="mt-2 rounded-lg border border-neutral-200 p-2.5">
      {writing && (
        <Field label="Recommendation">
          <textarea className={`${inputClass} min-h-[80px] py-2`} value={text}
                    onChange={(e) => setText(e.target.value)}
                    placeholder="What the committee recommends the employer do" />
        </Field>
      )}
      {writing && (
        <div className="mt-2">
          <Field label="Where it happened" hint="Optional. Included in the email">
            <LocationSelect value={locationId} onChange={(id) => setLocationId(id)} />
          </Field>
        </div>
      )}
      <p className="mb-1.5 mt-2 text-xs font-medium text-neutral-700">
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
      {people.isError && <LoadFailedNote what="The list of people" />}
      <ErrorNote error={draft.error} />
      <ErrorNote error={recordOnly.error} />
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onCancel}>Cancel</button>
        {writing && (
          <button type="button" className={secondaryButton}
                  disabled={!ready || busy}
                  onClick={() => { if (ready && !busy) recordOnly.mutate() }}>
            {recordOnly.isPending ? 'Recording…' : 'Record without sending'}
          </button>
        )}
        <button type="button" className={primaryButton}
                disabled={!ready || chosen.length === 0 || busy}
                onClick={() => {
                  if (ready && chosen.length > 0 && !busy) {
                    draft.mutate({ message: touched ? edited : null })
                  }
                }}>
          {draft.isPending ? 'Composing…' : 'Review the message'}
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
import { useNavigate, useParams } from 'react-router-dom'
import { useTabTitle } from '@uniops/shell'
import { ehsRoutes } from '@/app/routes'
import { FileText, Plus, Printer, Send } from 'lucide-react'
import { api, useFileOpener } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import {
  Card, Empty, ErrorNote, Field, inputClass, PageHeader, primaryButton, secondaryButton, SectionTitle, LoadProblem, LoadFailedNote,
} from '@/components/ui'
import { anyFailed } from '@/lib/anyFailed'
import { ReplyBadge, type Recommendation } from '@/pages/JhscPage'
import { LocationSelect } from '@/components/LocationSelect'

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

  const printer = useFileOpener()
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['jhsc-meeting', id] })
    queryClient.invalidateQueries({ queryKey: ['jhsc-attendance', id] })
    queryClient.invalidateQueries({ queryKey: ['jhsc-recommendations'] })
    queryClient.invalidateQueries({ queryKey: ['jhsc-meetings'] })
  }

  if (meeting.isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  // ★ The reason first. A refused or failed request leaves `data`
  // undefined exactly as a deleted record does, so this used to answer
  // "not found" to a 403 and to a server error alike.
  if (meeting.isError) return <LoadProblem error={meeting.error} noun="meeting" />
  if (!meeting.data) return <LoadProblem error={null} noun="meeting" />
  const m = meeting.data
  const published = m.status === 'published'
  const rows = attendance.data ?? []
  const present = rows.filter((a) => a.attendance === 'present').length
  // ★ "0 present" is a statement about a committee meeting, and OHSA s.9 wants
  // a record of who was there. A failed fetch must not make it.
  const partial = anyFailed(attendance, recommendations)

  return (
    <div className="p-4">
      {partial && <LoadFailedNote what="Part of this meeting" />}
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
      <div className="mb-2 mt-6 flex flex-wrap items-center justify-between gap-2">
        <SectionTitle>Minutes</SectionTitle>
        {/* ★ Round 6, TC-04: "there is no means to print the meeting minutes
            including the attendance, agenda, minutes, and recommendations." */}
        <button type="button" className={secondaryButton} disabled={printer.busy}
                onClick={() => printer.open(`/api/v1/jhsc/meetings/${m.id}/print`,
                                            `minutes-${m.meeting_no}.pdf`)}>
          <Printer className="h-4 w-4" aria-hidden />
          {printer.busy ? 'Preparing…' : 'Print the minutes'}
        </button>
      </div>
      {printer.error && (
        <p className="mb-2 rounded-lg bg-danger-50 px-3 py-2 text-xs text-danger-700">{printer.error}</p>
      )}
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
  const navigate = useNavigate()
  const [adding, setAdding] = useState(false)
  const [replyTo, setReplyTo] = useState<string | null>(null)
  // Who it was put in front of. Without it the committee could see that
  // nobody had answered and not who had failed to.
  const [sendingTo, setSendingTo] = useState<string | null>(null)

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
                <button type="button" onClick={() => navigate(`/jhsc/recommendations/${r.id}`)}
                        className="font-mono text-xs text-primary-700 hover:underline">#{r.seq}</button>
                <span className="min-w-0 flex-1 text-sm text-neutral-900">{r.recommendation}{r.location_path && (
                  <span className="block text-xs text-neutral-500">
                    Where: {r.location_path.split('/').join(' › ')}
                  </span>
                )}</span>
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
                  ? <RecommendationComposer meetingId={meetingId} recId={r.id}
                      recommendationText={r.recommendation}
                      onDone={() => { setSendingTo(null); onChanged() }}
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
            <RecommendationComposer meetingId={meetingId}
              onDone={() => { setAdding(false); onChanged() }}
              onCancel={() => setAdding(false)} />
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

/**
 * The minutes written on the committee's form, read back onto the meeting.
 *
 * ★ Round 6, TC-04: "a saved meeting minutes form does not get saved on the
 * meeting." It was attached — the meeting held the form's id — but this page
 * showed only the meeting's own text box, empty when the minutes are on a form,
 * and said "No minutes recorded" over a filled-in, submitted form.
 */
function FormMinutes({ meeting }: { meeting: Meeting }) {
  const rows = useQuery({
    queryKey: ['jhsc-minutes', meeting.id, meeting.minutes_submission_id],
    queryFn: () => api.get<{ label: string; text: string | null; heading: boolean; emphasis?: boolean }[]>(
      `/api/v1/jhsc/meetings/${meeting.id}/minutes-answers`),
    enabled: Boolean(meeting.minutes_submission_id),
  })
  if (!meeting.minutes_submission_id) return null
  if (rows.isError) return <LoadFailedNote what="The minutes form" />
  return (
    <dl className="flex flex-col gap-2">
      {(rows.data ?? []).map((r, i) => r.heading ? (
        <dt key={i} className="mt-1 text-sm font-semibold text-neutral-900">{r.label}</dt>
      ) : (
        <div key={i}>
          {/* Round 9: a question marked to stand out, two sizes up and bold. */}
          <dt className={r.emphasis ? 'text-sm font-bold text-neutral-900' : 'text-xs font-medium text-neutral-500'}>{r.label}</dt>
          <dd className={`whitespace-pre-wrap ${r.emphasis ? 'text-base font-bold' : 'text-sm'} text-neutral-900`}>
            {r.text ?? <span className="text-neutral-400">Not answered</span>}
          </dd>
        </div>
      ))}
    </dl>
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
                                  // Same field, same mistake: a meeting minuted
                                  // on the form has an empty box and was being
                                  // put back to "scheduled" on every save.
                                  status: (minutes.trim() || meeting.minutes_submission_id)
                                    ? 'held' : 'scheduled' }),
    onSuccess: onSaved,
  })
  const publish = useMutation({
    mutationFn: () => api.post(`/api/v1/jhsc/meetings/${meeting.id}/publish`, {}),
    onSuccess: onSaved,
  })
  // Starting the minutes and attaching them to this meeting are one call, so
  // there is no half-done state where a form exists and nothing points at it.
  const navigate = useNavigate()
  const startMinutes = useMutation({
    mutationFn: () => api.post<{ submission_id: string; submission_no: string }>(
      `/api/v1/jhsc/meetings/${meeting.id}/minutes-form/start`, {}),
    onSuccess: (r) => {
      onSaved()
      navigate(`/forms/submissions/${r.submission_id}`)
    },
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
        {meeting.minutes && (
          <p className="whitespace-pre-wrap text-sm text-neutral-900">{meeting.minutes}</p>
        )}
        <FormMinutes meeting={meeting} />
        {!meeting.minutes && !meeting.minutes_submission_id && (
          <p className="text-sm text-neutral-500">No minutes recorded.</p>
        )}
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
            {/* ★ Started from here, so the link between the minutes and the
                meeting is made when the document comes into existence. The
                screen used to say "fill it in under Forms and attach it here"
                and offer nothing here to attach it with — so the minutes and
                the meeting they belong to were two unrelated records. Pressing
                it again opens the same ones. */}
            <button type="button"
                    className="mt-2 inline-flex min-h-[32px] items-center gap-1.5 text-xs font-semibold text-primary-700 hover:underline disabled:opacity-50"
                    disabled={startMinutes.isPending}
                    onClick={() => { if (!startMinutes.isPending) startMinutes.mutate() }}>
              <FileText className="h-3.5 w-3.5" aria-hidden />
              {startMinutes.isPending
                ? 'Opening…'
                : meeting.minutes_submission_id
                  ? 'Open the minutes'
                  : 'Write the minutes'}
            </button>
            {meeting.minutes_submission_id && (
              <p className="mt-1.5 text-xs text-success-700">
                A filled-in form is attached to this meeting.
              </p>
            )}
            <div className="mt-2"><FormMinutes meeting={meeting} /></div>
            <ErrorNote error={startMinutes.error} />
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
        {/* ★ Was disabled on the text box alone, and where HSE has configured a
            minutes form that box is not even rendered — so Publish could never
            be pressed. The server decides whether there are minutes to publish
            (either the box or a submitted form) and says so if there are not;
            this only has to stop locking the button. One judge, not two. */}
        <button type="button" className={primaryButton}
                disabled={!(minutes.trim() || meeting.minutes_submission_id)
                          || publish.isPending || save.isPending}
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
