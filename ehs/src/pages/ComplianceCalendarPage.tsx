/**
 * Everything with a clock on it, in one place.
 *
 * Statutory notifications, certificate expiries and periodic reviews are all
 * the same kind of object in this system, which is why they can share a screen
 * — and why the regulation each one comes from is shown beside it.
 */
import TabLink from '@/components/TabLink'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import {
  PageHeader, SectionTitle,
} from '@/components/ui'
import { formatDateTime } from '@/lib/formatDate'
import { Countdown } from '@/components/Countdown'
import type { Deadline } from '@/lib/types'

interface CalendarDeadline extends Deadline {
  source_type: string
  /** The incident this clock is running against. The endpoint has always sent
   *  it; the interface simply never declared it, so the rows had nothing to
   *  link to. */
  source_id: string | null
  source_ref: string | null
  /** Where this row opens, computed by the server from the same map the task
   *  inbox uses. Empty when the clock's kind has no page — which a test on the
   *  server now prevents, so in practice every row has one. */
  path: string
}

export default function ComplianceCalendarPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['deadlines'],
    queryFn: () => api.get<CalendarDeadline[]>('/api/v1/deadlines'),
  })

  if (isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>

  const rows = data ?? []
  const now = Date.now()
  const running = rows.filter((d) => !d.satisfied_at &&
    new Date(d.due_at).getTime() - now < 30 * 86_400_000)
  const later = rows.filter((d) => !d.satisfied_at &&
    new Date(d.due_at).getTime() - now >= 30 * 86_400_000)

  return (
    <div className="p-4">
      <PageHeader title="Compliance calendar" subtitle="Statutory notifications, certificate expiries and periodic reviews." />

      <Section title="Running now" rows={running} empty="Nothing is due in the next month." />
      <Section title="Later" rows={later} empty="Nothing further scheduled." />
    </div>
  )
}

function Section({ title, rows, empty }: {
  title: string; rows: CalendarDeadline[]; empty: string
}) {
  return (
    <>
      <SectionTitle className="mb-2 mt-4">{title}</SectionTitle>
      {rows.length === 0 ? (
        <p className="text-sm text-neutral-500">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((d) => (
            <li key={d.id}>
              <DeadlineRow d={d} />
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

/** A clock, linked to whatever it is running against.
 *
 * ★ HSE, round 4: "every item in the compliance calendar should be clickable
 * and take the user to that item." This used to link incidents only and render
 * a certificate expiry, an SDS review and a committee reply as plain rows. The
 * destination comes from the server rather than from a second map here,
 * because the task inbox has one already and two copies drift — the committee
 * reply's link moved this round. A row with no path still renders, as a plain
 * row; it just cannot happen for the clocks that exist. */
function DeadlineRow({ d }: { d: CalendarDeadline }) {
  const body = (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Countdown kind={d.kind} dueAt={d.due_at} satisfiedAt={d.satisfied_at} />
        <span className="min-w-0 flex-1 text-sm text-neutral-800">
          {d.source_ref ?? d.source_type}
        </span>
      </div>
      <p className="mt-1.5 text-xs text-neutral-400">
        {d.regulation_ref ? `${d.regulation_ref} · ` : ''}due {formatDateTime(d.due_at)}
      </p>
    </>
  )
  const card = 'block rounded-xl border border-neutral-200 bg-white p-3.5'

  if (d.path) {
    return (
      <TabLink
        to={d.path}
        className={`${card} transition-colors hover:border-primary-300 hover:bg-primary-50/40`}
      >
        {body}
      </TabLink>
    )
  }
  return <div className={card}>{body}</div>
}
