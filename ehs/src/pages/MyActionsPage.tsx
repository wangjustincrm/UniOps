/**
 * What is assigned to me — where most employees land.
 *
 * Card list rather than a table: this is read one-handed on a phone, and a
 * table hides the one thing that matters, which is how late something is.
 */
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, ClipboardList } from 'lucide-react'
import { api } from '@/lib/api'
import { daysOverdue, formatDate } from '@/lib/formatDate'
import type { ActionListItem } from '@/lib/types'
import { StatusBadge } from '@/components/StatusBadge'

function Lateness({ dueDate }: { dueDate: string }) {
  const late = daysOverdue(dueDate)
  if (late === null) return null
  if (late > 0) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md bg-danger-600 px-2.5 py-1 text-xs font-medium text-white tabular-nums">
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        {late}d late
      </span>
    )
  }
  if (late === 0) {
    return (
      <span className="rounded-md bg-warning-50 px-2.5 py-1 text-xs font-medium text-warning-700">
        Due today
      </span>
    )
  }
  return (
    <span className="rounded-md bg-neutral-100 px-2.5 py-1 text-xs font-medium text-neutral-600 tabular-nums">
      Due {formatDate(dueDate)}
    </span>
  )
}

const ESCALATION_NOTE: Record<number, string> = {
  2: 'Escalated to your supervisor.',
  3: 'Escalated to the HSE Manager.',
}

/**
 * A titled block, drawn only when it holds something.
 *
 * The count is in the heading because the first question anyone asks this page
 * is "how much is late", and counting cards to answer it is the reason the
 * page felt long.
 */
function Group({ title, count, tone = 'plain', children }: {
  title: string
  count: number
  tone?: 'plain' | 'warn' | 'bad'
  children: React.ReactNode
}) {
  if (count === 0) return null
  const badge = tone === 'bad'
    ? 'bg-danger-600 text-white'
    : tone === 'warn'
      ? 'bg-warning-100 text-warning-800'
      : 'bg-neutral-100 text-neutral-600'
  return (
    <section className="mb-1 flex flex-col gap-2">
      <h2 className="mt-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
        {title}
        <span className={`rounded-full px-1.5 py-0.5 text-[11px] font-bold tabular-nums ${badge}`}>
          {count}
        </span>
      </h2>
      {children}
    </section>
  )
}

function ActionCard({ action: a }: { action: ActionListItem }) {
  return (
    <Link
      to={`/actions/${a.id}`}
      className="block rounded-xl border border-neutral-200 bg-white p-3.5 transition-colors hover:border-primary-300 hover:bg-primary-50/40"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-neutral-900">{a.title}</h3>
          <p className="mt-0.5 text-xs text-neutral-500">
            {a.action_no}
            {a.source_ref ? ` · from ${a.source_ref}` : ''}
          </p>
        </div>
        <Lateness dueDate={a.due_date} />
      </div>
      {ESCALATION_NOTE[a.escalation_level] && (
        <p className="mt-2.5 border-t border-neutral-100 pt-2.5 text-xs text-neutral-600">
          {ESCALATION_NOTE[a.escalation_level]}
        </p>
      )}
      <div className="mt-2.5">
        <StatusBadge status={a.status} />
      </div>
    </Link>
  )
}

interface AssignedTask {
  id: string; type: string; title: string; description: string | null
  priority: string; document_type: string; document_id: string
  document_number: string; created_at: string; path: string
}

export default function MyActionsPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['actions', 'mine'],
    queryFn: () => api.get<ActionListItem[]>('/api/v1/actions/mine'),
  })
  // Everything else this module has handed me. The page listed corrective
  // actions only, so a toolbox talk to deliver, an inspection due or a policy
  // to sign appeared nowhere on the screen named after being assigned work —
  // which is where the person told to do it goes looking.
  const tasks = useQuery({
    queryKey: ['actions', 'mine', 'tasks'],
    queryFn: () => api.get<AssignedTask[]>('/api/v1/actions/mine/tasks'),
  })
  // Judging whether somebody else's fix worked is a different job from doing
  // your own, so it is its own group rather than more rows in the same list.
  const toVerify = useQuery({
    queryKey: ['actions', 'mine', 'to-verify'],
    queryFn: () => api.get<ActionListItem[]>('/api/v1/actions/mine/to-verify'),
  })

  if (isLoading) {
    return <p className="p-4 text-sm text-neutral-500">Loading your actions…</p>
  }
  if (isError) {
    return (
      <p className="p-4 text-sm text-danger-700">
        {(error as Error).message}
      </p>
    )
  }

  const actions = data ?? []
  const other = tasks.data ?? []
  const verifying = toVerify.data ?? []

  if (actions.length === 0 && other.length === 0 && verifying.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 p-10 text-center">
        <CheckCircle2 className="h-8 w-8 text-success-500" aria-hidden />
        <p className="text-sm font-medium text-neutral-700">Nothing is assigned to you.</p>
        <p className="text-sm text-neutral-500">
          Corrective actions raised from incidents and inspections appear here.
        </p>
      </div>
    )
  }

  // ★ Grouped by when it is wanted, not by where it came from. The page was
  // one run of cards with a couple of headings appearing only when the block
  // above them happened to be non-empty — so the first group was unlabelled,
  // and something three weeks late sat between two things due next month with
  // nothing but a red pill to tell them apart.
  const overdue = actions.filter((a) => (daysOverdue(a.due_date) ?? 0) > 0)
  const today = actions.filter((a) => daysOverdue(a.due_date) === 0)
  const later = actions.filter((a) => (daysOverdue(a.due_date) ?? 0) < 0)

  return (
    <div className="flex flex-col gap-2 p-4">
      <Group title="Overdue" count={overdue.length} tone="bad">
        {overdue.map((a) => <ActionCard key={a.id} action={a} />)}
      </Group>

      <Group title="Due today" count={today.length} tone="warn">
        {today.map((a) => <ActionCard key={a.id} action={a} />)}
      </Group>

      <Group title="Waiting on you to verify" count={verifying.length} tone="warn">
        {verifying.map((a) => (
          <Link
            key={a.id}
            to={`/actions/${a.id}`}
            className="block rounded-xl border border-warning-500/40 bg-warning-50/40 p-3.5 transition-colors hover:border-warning-500"
          >
            <h2 className="text-sm font-semibold text-neutral-900">{a.title}</h2>
            <p className="mt-0.5 text-xs text-neutral-500">
              {a.action_no}
              {a.source_ref ? ` · from ${a.source_ref}` : ''}
            </p>
            <p className="mt-1.5 text-xs text-neutral-600">
              The work is reported done. Confirm the control is effective —
              or that it is not, which sends it back.
            </p>
          </Link>
        ))}
      </Group>

      <Group title="Coming up" count={later.length}>
        {later.map((a) => <ActionCard key={a.id} action={a} />)}
      </Group>

      {/* Everything else this module has handed me: a toolbox talk to deliver,
          an inspection due, a policy to sign. They used to open the page
          unlabelled, above a heading that belonged to the block underneath. */}
      <Group title="Other work assigned to you" count={other.length}>
        {other.map((t) => (
          <Link
            key={t.id}
            to={t.path || '/actions/mine'}
            className="block rounded-xl border border-neutral-200 bg-white p-3.5 transition-colors hover:border-primary-300 hover:bg-primary-50/40"
          >
            <div className="flex items-start gap-3">
              <ClipboardList className="mt-0.5 h-4 w-4 shrink-0 text-neutral-400" aria-hidden />
              <div className="min-w-0 flex-1">
                <h2 className="text-sm font-semibold text-neutral-900">{t.title}</h2>
                <p className="mt-0.5 text-xs text-neutral-500">{t.document_number}</p>
                {t.description && (
                  <p className="mt-1 text-xs text-neutral-600">{t.description}</p>
                )}
              </div>
            </div>
          </Link>
        ))}
      </Group>
    </div>
  )
}
