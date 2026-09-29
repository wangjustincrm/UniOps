/**
 * The Joint Health and Safety Committee.
 *
 * The roster tab opens with whether the committee is lawfully constituted, not
 * with how many people are on it. OHSA s.9 requires at least half the members
 * to be workers who do not exercise managerial functions, and a certified
 * member from each side. Those are conditions, not statistics, so they are
 * stated as met or not met rather than left for somebody to count.
 *
 * Recommendations are their own tab because the twenty-one day reply is the
 * only statutory clock in this module that is not attached to an injury, and
 * it is the one most easily forgotten.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { AlertTriangle, CalendarDays, Check, Plus, UserMinus, X } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate, todayForInput } from '@/lib/formatDate'
import { usePermissions } from '@/hooks/usePermissions'
import { PersonPicker, EMPTY_PERSON, type PersonValue } from '@/components/PersonPicker'
import {
  Card, Empty, ErrorNote, Field, PageHeader, Tabs, inputClass, primaryButton, secondaryButton,
} from '@/components/ui'

interface Member {
  id: string; user_id: string | null; user_name: string; department_name: string | null
  representation: string; committee_role: string; is_certified: boolean
  certified_on: string | null; term_start: string | null; term_end: string | null
  is_active: boolean
}
interface Composition {
  total: number; worker_members: number; management_members: number
  certified_total: number; certified_worker: number; certified_management: number
  worker_majority_met: boolean; certified_both_sides_met: boolean
}
interface Meeting {
  id: string; meeting_no: string; title: string | null; held_on: string
  location_text: string | null; status: string; published_at: string | null
  inspection_department_name: string | null
}
export interface Recommendation {
  id: string; meeting_id: string; seq: number; recommendation: string
  raised_by_name: string | null; response: string | null; outcome: string | null
  responded_at: string | null; responded_by_name: string | null
  sent_at: string | null; sent_by_name: string | null
  sent_to: { id: string; name: string }[]
  action_id: string | null; created_at: string
}

const TABS = [
  { key: 'meetings', label: 'Meetings' },
  { key: 'roster', label: 'Committee' },
  { key: 'recommendations', label: 'Recommendations' },
] as const
type TabKey = (typeof TABS)[number]['key']

export default function JhscPage() {
  // ★ A reply task links here with the recommendation it means. Landing on the
  // meetings list and hunting for it is what the link used to offer, and the
  // task is a statutory obligation with twenty-one days on it.
  const [params] = useSearchParams()
  const wantedRec = params.get('rec')
  const [tab, setTab] = useState<TabKey>(wantedRec ? 'recommendations' : 'meetings')

  // ★ And the tab follows `rec` whenever it changes, not only on the first
  // render. This page lives in a keep-alive tab keyed on its path without the
  // search, so arriving a second time does not remount: open one reply task,
  // look at Meetings, then open another from My Actions, and the highlight
  // would move to a recommendation on a tab the reader is not looking at. Same
  // fault, same fix, as the date filter — raised in review by uniops-9a.
  const [lastRec, setLastRec] = useState(wantedRec)
  if (wantedRec !== lastRec) {
    setLastRec(wantedRec)
    if (wantedRec) setTab('recommendations')
  }
  const { can } = usePermissions()
  const mayManage = can('ehs.jhsc.manage')

  const outstanding = useQuery({
    queryKey: ['jhsc-recommendations', 'outstanding'],
    queryFn: () => api.get<Recommendation[]>('/api/v1/jhsc/recommendations?outstanding=true'),
  })

  const tabs = TABS.map((t) => t.key === 'recommendations'
    ? { ...t, count: outstanding.data?.length }
    : t)

  return (
    <div className="p-4">
      <PageHeader title="Health and safety committee"
                  subtitle="Joint Health and Safety Committee — OHSA s.9" />
      <Tabs tabs={tabs} active={tab} onChange={setTab} />
      {tab === 'meetings' && <MeetingsTab mayManage={mayManage} />}
      {tab === 'roster' && <RosterTab mayManage={mayManage} />}
      {tab === 'recommendations' && (
        <RecommendationsTab mayManage={mayManage} highlight={wantedRec} />
      )}
    </div>
  )
}

// ── Committee roster ─────────────────────────────────────────────────────────

function RosterTab({ mayManage }: { mayManage: boolean }) {
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)

  const members = useQuery({
    queryKey: ['jhsc-members'],
    queryFn: () => api.get<Member[]>('/api/v1/jhsc/members'),
  })
  const composition = useQuery({
    queryKey: ['jhsc-composition'],
    queryFn: () => api.get<Composition>('/api/v1/jhsc/composition'),
  })
  const retire = useMutation({
    mutationFn: (memberId: string) => api.post(`/api/v1/jhsc/members/${memberId}/retire`, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jhsc-members'] })
      queryClient.invalidateQueries({ queryKey: ['jhsc-composition'] })
    },
  })

  const c = composition.data

  return (
    <>
      {c && c.total > 0 && (
        <div className="mb-4 grid gap-2 sm:grid-cols-2">
          <Requirement
            met={c.worker_majority_met}
            title="Worker majority"
            detail={`${c.worker_members} of ${c.total} members are worker representatives. `
              + 'At least half must be workers who do not exercise managerial functions.'}
          />
          <Requirement
            met={c.certified_both_sides_met}
            title="Certified members"
            detail={`${c.certified_worker} worker and ${c.certified_management} management `
              + 'certified. One from each side is required.'}
          />
        </div>
      )}

      {mayManage && (
        <div className="mb-3 flex justify-end">
          <button type="button" className={primaryButton} onClick={() => setAdding(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            Add a member
          </button>
        </div>
      )}
      {adding && <NewMemberForm onClose={() => setAdding(false)} />}

      {members.data?.length === 0 && !adding ? (
        <Empty>
          Nobody on the committee yet. Adding members is what makes the meeting
          attendance sheet and the composition check above mean anything.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {members.data?.map((m) => (
            <li key={m.id}>
              <Card>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-neutral-900">
                      {m.user_name}
                    </span>
                    <span className="block text-xs text-neutral-500">
                      {m.department_name ?? 'No department recorded'}
                      {m.term_start && ` · since ${formatDate(m.term_start)}`}
                    </span>
                  </span>
                  <span className={
                    'rounded-full px-2.5 py-0.5 text-[11px] font-semibold ' +
                    (m.representation === 'worker'
                      ? 'bg-primary-50 text-primary-700'
                      : 'bg-neutral-100 text-neutral-600')
                  }>
                    {m.representation === 'worker' ? 'Worker rep' : 'Management rep'}
                  </span>
                  {m.committee_role !== 'member' && (
                    <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600">
                      {m.committee_role === 'co_chair' ? 'Co-chair' : 'Secretary'}
                    </span>
                  )}
                  {m.is_certified && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-success-50 px-2 py-0.5 text-[11px] font-semibold text-success-700">
                      <Check className="h-3 w-3" aria-hidden />
                      Certified
                    </span>
                  )}
                  {mayManage && (
                    <button type="button" aria-label={`Stand down ${m.user_name}`}
                            disabled={retire.isPending}
                            onClick={() => { if (!retire.isPending) retire.mutate(m.id) }}
                            className="rounded p-1.5 text-neutral-400 hover:bg-neutral-50 hover:text-neutral-700">
                      <UserMinus className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
      <ErrorNote error={retire.error} />
    </>
  )
}

function Requirement({ met, title, detail }: { met: boolean; title: string; detail: string }) {
  return (
    <div className={
      'rounded-xl border p-3 ' +
      (met ? 'border-success-500 bg-success-50' : 'border-warning-500 bg-warning-50')
    }>
      <p className={
        'flex items-center gap-1.5 text-sm font-semibold ' +
        (met ? 'text-success-700' : 'text-warning-700')
      }>
        {met ? <Check className="h-4 w-4" aria-hidden />
             : <AlertTriangle className="h-4 w-4" aria-hidden />}
        {title} — {met ? 'met' : 'not met'}
      </p>
      <p className={`mt-0.5 text-xs ${met ? 'text-success-700' : 'text-warning-700'}`}>
        {detail}
      </p>
    </div>
  )
}

function NewMemberForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [person, setPerson] = useState<PersonValue>(EMPTY_PERSON)
  const [representation, setRepresentation] = useState('worker')
  const [committeeRole, setCommitteeRole] = useState('member')
  const [certified, setCertified] = useState(false)
  const [termStart, setTermStart] = useState('')

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/jhsc/members', {
      user_id: person.userId,
      representation,
      committee_role: committeeRole,
      is_certified: certified,
      term_start: termStart || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jhsc-members'] })
      queryClient.invalidateQueries({ queryKey: ['jhsc-composition'] })
      onClose()
    },
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">Add a committee member</h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Who">
          <PersonPicker value={person} onChange={setPerson} allowExternal={false} />
        </Field>
        <Field label="Represents"
               hint="Worker representatives must not exercise managerial functions">
          <select className={inputClass} value={representation}
                  onChange={(e) => setRepresentation(e.target.value)}>
            <option value="worker">Workers</option>
            <option value="management">Management</option>
          </select>
        </Field>
        <Field label="Role on the committee">
          <select className={inputClass} value={committeeRole}
                  onChange={(e) => setCommitteeRole(e.target.value)}>
            <option value="member">Member</option>
            <option value="co_chair">Co-chair</option>
            <option value="secretary">Secretary</option>
          </select>
        </Field>
        <Field label="Term started">
          <input type="date" className={inputClass} value={termStart}
                 onChange={(e) => setTermStart(e.target.value)} />
        </Field>
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm text-neutral-700">
        <input type="checkbox" checked={certified}
               onChange={(e) => setCertified(e.target.checked)} />
        Certified member
      </label>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton}
                disabled={!person.userId || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Adding…' : 'Add'}
        </button>
      </div>
    </Card>
  )
}

// ── Meetings ─────────────────────────────────────────────────────────────────

function MeetingsTab({ mayManage }: { mayManage: boolean }) {
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)

  const meetings = useQuery({
    queryKey: ['jhsc-meetings'],
    queryFn: () => api.get<Meeting[]>('/api/v1/jhsc/meetings'),
  })

  return (
    <>
      {mayManage && (
        <div className="mb-3 flex justify-end">
          <button type="button" className={primaryButton} onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            New meeting
          </button>
        </div>
      )}
      {creating && <NewMeetingForm onClose={() => setCreating(false)} />}

      {meetings.data?.length === 0 && !creating ? (
        <Empty>
          No meetings recorded. Creating one pre-fills the attendance sheet from
          the current committee, so recording who came is ticking a list.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {meetings.data?.map((m) => (
            <li key={m.id}>
              <button type="button" onClick={() => navigate(`/jhsc/meetings/${m.id}`)}
                      className="flex w-full items-start gap-3 rounded-xl border border-neutral-200 bg-white p-3.5 text-left transition-colors hover:border-primary-300">
                <CalendarDays className="mt-0.5 h-5 w-5 shrink-0 text-primary-600" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-neutral-900">
                    {m.title || `Meeting of ${formatDate(m.held_on)}`}
                  </span>
                  <span className="mt-0.5 block text-xs text-neutral-500">
                    {m.meeting_no} · {formatDate(m.held_on)}
                    {m.location_text && ` · ${m.location_text}`}
                  </span>
                </span>
                <span className={
                  'shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ' +
                  (m.status === 'published'
                    ? 'bg-success-50 text-success-700'
                    : 'bg-neutral-100 text-neutral-600')
                }>
                  {m.status === 'published' ? 'Published' :
                   m.status === 'held' ? 'Minutes pending' : 'Scheduled'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function NewMeetingForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [heldOn, setHeldOn] = useState(todayForInput())
  const [where, setWhere] = useState('')

  const create = useMutation({
    mutationFn: () => api.post('/api/v1/jhsc/meetings', {
      title: title || null, held_on: heldOn, location_text: where || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jhsc-meetings'] })
      onClose()
    },
  })

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">New meeting</h2>
        <button type="button" onClick={onClose} aria-label="Close"
                className="rounded p-1 text-neutral-400 hover:bg-neutral-50">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Title" hint="Optional — the date is enough">
          <input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)}
                 placeholder="September meeting" />
        </Field>
        <Field label="Held on">
          <input type="date" className={inputClass} value={heldOn}
                 onChange={(e) => setHeldOn(e.target.value)} />
        </Field>
        <Field label="Where">
          <input className={inputClass} value={where} onChange={(e) => setWhere(e.target.value)}
                 placeholder="Boardroom, Building 107" />
        </Field>
      </div>
      <ErrorNote error={create.error} />
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onClose}>Cancel</button>
        <button type="button" className={primaryButton} disabled={!heldOn || create.isPending}
                onClick={() => { if (!create.isPending) create.mutate() }}>
          {create.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
    </Card>
  )
}

// ── Recommendations ──────────────────────────────────────────────────────────

function RecommendationsTab({ mayManage, highlight }: {
  mayManage: boolean
  /** The one a reply task was pointing at, brought to the top and marked. */
  highlight?: string | null
}) {
  const navigate = useNavigate()
  const all = useQuery({
    queryKey: ['jhsc-recommendations'],
    queryFn: () => api.get<Recommendation[]>('/api/v1/jhsc/recommendations'),
  })
  const meetings = useQuery({
    queryKey: ['jhsc-meetings'],
    queryFn: () => api.get<Meeting[]>('/api/v1/jhsc/meetings'),
  })

  const meetingNo = (id: string) =>
    meetings.data?.find((m) => m.id === id)?.meeting_no ?? ''

  if (all.data?.length === 0) {
    return (
      <Empty>
        No recommendations recorded. One recorded here starts OHSA s.9(20)&rsquo;s
        twenty-one days for a written reply, and appears on the compliance calendar.
      </Empty>
    )
  }

  return (
    <ul className="flex flex-col gap-2">
      {[...(all.data ?? [])]
        .sort((a, b) => (a.id === highlight ? -1 : b.id === highlight ? 1 : 0))
        .map((r) => (
        <li key={r.id}>
          <Card className={r.id === highlight
            ? 'ring-2 ring-primary-400 ring-offset-1'
            : undefined}>
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <button type="button"
                      onClick={() => navigate(`/jhsc/meetings/${r.meeting_id}`)}
                      className="font-mono text-xs text-primary-700 hover:underline">
                {meetingNo(r.meeting_id)} #{r.seq}
              </button>
              <span className="min-w-0 flex-1 text-sm text-neutral-900">{r.recommendation}</span>
              <ReplyBadge rec={r} />
            </div>
            {r.response && (
              <p className="mt-2 border-l-2 border-neutral-200 pl-2.5 text-sm text-neutral-700">
                {r.response}
                <span className="mt-0.5 block text-xs text-neutral-500">
                  {r.responded_by_name} · {r.responded_at && formatDate(r.responded_at)}
                </span>
              </p>
            )}
            {!r.responded_at && mayManage && (
              <p className="mt-2 text-xs text-neutral-500">
                Reply from the meeting it was raised at.
              </p>
            )}
          </Card>
        </li>
      ))}
    </ul>
  )
}

export function ReplyBadge({ rec }: { rec: Recommendation }) {
  if (rec.responded_at) {
    const tone = rec.outcome === 'declined'
      ? 'bg-neutral-100 text-neutral-600'
      : 'bg-success-50 text-success-700'
    return (
      <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${tone}`}>
        {rec.outcome === 'accepted' ? 'Accepted'
          : rec.outcome === 'declined' ? 'Declined' : 'Deferred'}
      </span>
    )
  }
  // Twenty-one calendar days from when it was recorded — the same arithmetic
  // the server used to set the deadline.
  const due = new Date(new Date(rec.created_at).getTime() + 21 * 86_400_000)
  const days = Math.ceil((due.getTime() - Date.now()) / 86_400_000)
  const overdue = days < 0
  return (
    <span className={
      'shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-semibold ' +
      (overdue ? 'bg-danger-600 text-white'
        : days <= 5 ? 'bg-warning-50 text-warning-700'
        : 'bg-neutral-100 text-neutral-600')
    }>
      {overdue ? `Reply ${-days}d overdue` : `Reply due in ${days}d`}
    </span>
  )
}
