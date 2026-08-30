/**
 * Everything with a clock on it, in one place.
 *
 * Statutory notifications, certificate expiries and periodic reviews are all
 * the same kind of object in this system, which is why they can share a screen
 * — and why the regulation each one comes from is shown beside it.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/formatDate'
import { Countdown } from '@/components/Countdown'
import type { Deadline } from '@/lib/types'

interface CalendarDeadline extends Deadline {
  source_type: string
  source_ref: string | null
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
      <h1 className="text-lg font-bold tracking-tight text-neutral-900">Compliance calendar</h1>
      <p className="mb-4 text-xs text-neutral-500">
        Statutory notifications, certificate expiries and periodic reviews.
      </p>

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
      <h2 className="mb-2 mt-4 text-xs font-bold uppercase tracking-wider text-neutral-400">
        {title}
      </h2>
      {rows.length === 0 ? (
        <p className="text-sm text-neutral-500">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((d) => (
            <li key={d.id} className="rounded-xl border border-neutral-200 bg-white p-3.5">
              <div className="flex flex-wrap items-center gap-2">
                <Countdown kind={d.kind} dueAt={d.due_at} satisfiedAt={d.satisfied_at} />
                <span className="min-w-0 flex-1 text-sm text-neutral-800">
                  {d.source_ref ?? d.source_type}
                </span>
              </div>
              <p className="mt-1.5 text-xs text-neutral-400">
                {d.regulation_ref ? `${d.regulation_ref} · ` : ''}due {formatDateTime(d.due_at)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}
