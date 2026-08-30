/**
 * Who is missing which required training.
 *
 * Usually the first record a Ministry inspector asks for. It separates never
 * took it from took it and it lapsed, because those need different action.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/formatDate'

interface Gap {
  user_id: string; user_name: string; course_code: string
  course_name: string; reason: string; expired_on: string | null
}
interface Report {
  as_of: string; workers_considered: number; workers_compliant: number
  compliance_rate: number; gaps: Gap[]
}

export default function TrainingGapsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['training', 'gaps'],
    queryFn: () => api.get<Report>('/api/v1/training/gaps'),
  })

  if (isLoading) return <p className="p-4 text-sm text-neutral-500">Loading…</p>
  const r = data!

  const byWorker = new Map<string, Gap[]>()
  for (const gap of r.gaps) {
    const list = byWorker.get(gap.user_id) ?? []
    list.push(gap)
    byWorker.set(gap.user_id, list)
  }

  return (
    <div className="p-4">
      <h1 className="text-lg font-bold tracking-tight text-neutral-900">Training compliance</h1>
      <p className="mb-4 text-xs text-neutral-500">As of {formatDate(r.as_of)}</p>

      <div className="mb-4 flex flex-wrap gap-2">
        <Stat label="Compliant" value={`${r.workers_compliant} of ${r.workers_considered}`} />
        <Stat label="Rate" value={`${Math.round(r.compliance_rate * 100)}%`}
              tone={r.compliance_rate < 0.9 ? 'warn' : 'ok'} />
        <Stat label="Gaps" value={String(r.gaps.length)}
              tone={r.gaps.length ? 'warn' : 'ok'} />
      </div>

      {byWorker.size === 0 ? (
        <p className="rounded-xl border border-success-500 bg-success-50 p-4 text-sm text-success-700">
          Everyone is up to date on their required training.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {[...byWorker.entries()].map(([userId, gaps]) => (
            <li key={userId} className="rounded-xl border border-neutral-200 bg-white p-3.5">
              <p className="text-sm font-semibold text-neutral-900">{gaps[0].user_name}</p>
              <ul className="mt-1.5 flex flex-col gap-1">
                {gaps.map((g) => (
                  <li key={g.course_code} className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-mono text-neutral-500">{g.course_code}</span>
                    <span className="min-w-0 flex-1 text-neutral-700">{g.course_name}</span>
                    {g.reason === 'expired' ? (
                      <span className="rounded-full bg-warning-50 px-2 py-0.5 font-semibold text-warning-700">
                        Expired {g.expired_on ? formatDate(g.expired_on) : ''}
                      </span>
                    ) : (
                      <span className="rounded-full bg-danger-50 px-2 py-0.5 font-semibold text-danger-700">
                        Never taken
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'ok' | 'warn' }) {
  const colour = tone === 'warn' ? 'text-warning-700' : tone === 'ok' ? 'text-success-700' : 'text-neutral-900'
  return (
    <div className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2.5">
      <p className="text-xs text-neutral-500">{label}</p>
      <p className={`font-mono text-lg font-bold tabular-nums ${colour}`}>{value}</p>
    </div>
  )
}
