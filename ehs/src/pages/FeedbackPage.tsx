/**
 * Somewhere to say "this is wrong" about anything at all.
 *
 * ★ HSE, round 4: "add a general comments feedback place to allow for general
 * comments for other issues found."
 *
 * Every other screen here is about a specific obligation. Somebody who notices
 * that a list is missing a name, that a form asks the wrong question, or that a
 * word on a button means something else on the plant floor had nowhere to put
 * it — so it arrived in the answer box of an acceptance script, weeks later, or
 * not at all.
 *
 * Two audiences, one screen. Anybody may leave a comment and see their own;
 * whoever manages Settings sees all of them and answers. The narrowing is the
 * server's, and the page asks for what it is allowed to have rather than
 * showing a permission error where a list belongs.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { MessageSquare } from 'lucide-react'
import { api } from '@/lib/api'
import { usePermissions } from '@/hooks/usePermissions'
import { useMarkRead } from '@/hooks/useSeen'
import { formatDateTime } from '@/lib/formatDate'
import {
  Card, Empty, ErrorNote, Field, inputClass, LoadFailedNote, PageHeader,
  primaryButton, secondaryButton, SectionTitle, Tabs,
} from '@/components/ui'

interface Comment {
  id: string
  body: string
  page_path: string | null
  submitted_by: string | null
  submitted_by_name: string | null
  status: string
  reply: string | null
  answered_at: string | null
  answered_by_name: string | null
  created_at: string
}

const STATUS_LABELS: Record<string, string> = {
  new: 'Not looked at yet',
  looked_at: 'Being looked at',
  done: 'Dealt with',
}

const TABS = [
  { key: 'say', label: 'Leave a comment' },
  { key: 'all', label: 'Everything said' },
] as const
type TabKey = (typeof TABS)[number]['key']

export default function FeedbackPage() {
  const { can } = usePermissions()
  // Round 12, ACT-04: the answers to my own comments are in front of me now.
  useMarkRead('feedback', true)
  const mayTriage = can('ehs.settings.manage')
  const [tab, setTab] = useState<TabKey>('say')

  return (
    <div className="p-4">
      <PageHeader
        title="Feedback"
        subtitle="Anything that is wrong, confusing or missing — including things that are nobody's incident"
      />
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === 'say' && <LeaveTab />}
      {tab === 'all' && <ListTab mayTriage={mayTriage} />}
    </div>
  )
}

function LeaveTab() {
  const queryClient = useQueryClient()
  const [body, setBody] = useState('')
  const [sent, setSent] = useState(false)

  const send = useMutation({
    mutationFn: () => api.post('/api/v1/feedback', {
      body: body.trim(),
      // Where they were. Not typed, because "the list is wrong" cannot be
      // acted on without knowing which list — and the browser knows.
      page_path: document.referrer && document.referrer.startsWith(location.origin)
        ? new URL(document.referrer).pathname.slice(0, 200)
        : null,
    }),
    onSuccess: () => {
      setBody('')
      setSent(true)
      queryClient.invalidateQueries({ queryKey: ['feedback'] })
    },
  })

  const ready = body.trim().length > 0

  return (
    <Card className="max-w-[72ch]">
      <SectionTitle className="mb-1.5 flex items-center gap-1.5">
        <MessageSquare className="h-3.5 w-3.5 text-neutral-400" aria-hidden />
        What did you notice?
      </SectionTitle>
      <p className="mb-3 text-xs text-neutral-500">
        It goes to HSE with your name on it. Say where you were and what you
        expected to happen — that is usually the whole of what somebody needs to
        fix it.
      </p>
      <Field label="Your comment">
        <textarea className={inputClass} rows={6} value={body}
                  onChange={(e) => { setBody(e.target.value); setSent(false) }}
                  placeholder="The training list does not show people who left, and we still need their records for the audit." />
      </Field>
      <ErrorNote error={send.error} />
      <div className="mt-3 flex items-center justify-end gap-3">
        {sent && <span className="text-xs text-success-700">Sent. HSE has been told.</span>}
        <button type="button" className={primaryButton}
                disabled={!ready || send.isPending}
                onClick={() => { if (ready && !send.isPending) send.mutate() }}>
          {send.isPending ? 'Sending…' : 'Send it'}
        </button>
      </div>
    </Card>
  )
}

function ListTab({ mayTriage }: { mayTriage: boolean }) {
  const list = useQuery({
    queryKey: ['feedback', mayTriage ? 'all' : 'mine'],
    queryFn: () => api.get<Comment[]>('/api/v1/feedback'),
  })

  if (list.isError) return <LoadFailedNote what="The comments" />
  if (list.isLoading) return <p className="text-sm text-neutral-500">Loading…</p>

  const rows = list.data ?? []
  if (rows.length === 0) {
    return (
      <Empty>
        {mayTriage
          ? 'Nobody has left a comment yet.'
          : 'You have not left a comment yet. Anything you send appears here with the reply.'}
      </Empty>
    )
  }

  return (
    <>
      {!mayTriage && (
        <p className="mb-3 text-xs text-neutral-500">
          Your own comments. Somebody else's comment about their own work is not
          yours to read.
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {rows.map((c) => <li key={c.id}><Row c={c} mayTriage={mayTriage} /></li>)}
      </ul>
    </>
  )
}

function Row({ c, mayTriage }: { c: Comment; mayTriage: boolean }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [reply, setReply] = useState(c.reply ?? '')

  const answer = useMutation({
    mutationFn: (status: string) => api.patch(`/api/v1/feedback/${c.id}`, {
      status, reply: reply.trim() || null,
    }),
    onSuccess: () => {
      setOpen(false)
      queryClient.invalidateQueries({ queryKey: ['feedback'] })
    },
  })

  return (
    <Card>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-sm font-semibold text-neutral-900">
          {c.submitted_by_name ?? 'Not named'}
        </span>
        <span className="min-w-0 flex-1 text-xs text-neutral-500">
          {formatDateTime(c.created_at)}
          {c.page_path && ` · from ${c.page_path}`}
        </span>
        <span className={
          'rounded-full px-2.5 py-0.5 text-[11px] font-semibold ' +
          (c.status === 'done' ? 'bg-success-50 text-success-700'
            : c.status === 'looked_at' ? 'bg-warning-50 text-warning-700'
            : 'bg-neutral-100 text-neutral-600')
        }>
          {STATUS_LABELS[c.status] ?? c.status}
        </span>
      </div>

      {/* ★ Never rewritten. Rewording somebody's complaint is how a complaint
          disappears; the reply sits beside it. */}
      <p className="mt-2 whitespace-pre-wrap text-sm text-neutral-800">{c.body}</p>

      {c.reply && (
        <div className="mt-2.5 rounded-lg bg-neutral-50 px-3 py-2">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
            HSE
          </p>
          <p className="mt-0.5 whitespace-pre-wrap text-sm text-neutral-800">{c.reply}</p>
          <p className="mt-1 text-xs text-neutral-400">
            {[c.answered_by_name, c.answered_at ? formatDateTime(c.answered_at) : null]
              .filter(Boolean).join(' · ')}
          </p>
        </div>
      )}

      {mayTriage && (
        <div className="mt-2.5">
          {!open ? (
            <button type="button" className={secondaryButton} onClick={() => setOpen(true)}>
              {c.reply ? 'Change the reply' : 'Reply'}
            </button>
          ) : (
            <>
              <Field label="Reply">
                <textarea className={inputClass} rows={3} value={reply}
                          onChange={(e) => setReply(e.target.value)} />
              </Field>
              <ErrorNote error={answer.error} />
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" className={primaryButton} disabled={answer.isPending}
                        onClick={() => { if (!answer.isPending) answer.mutate('done') }}>
                  {answer.isPending ? 'Saving…' : 'Reply and mark dealt with'}
                </button>
                <button type="button" className={secondaryButton} disabled={answer.isPending}
                        onClick={() => { if (!answer.isPending) answer.mutate('looked_at') }}>
                  Save, still working on it
                </button>
                <button type="button" className={secondaryButton}
                        onClick={() => { setOpen(false); setReply(c.reply ?? '') }}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </Card>
  )
}
