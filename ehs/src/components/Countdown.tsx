/**
 * The statutory countdown.
 *
 * Ticks in the browser from the deadline rather than polling the server, and
 * always names the clock and the remaining time in words — the colour is a
 * second signal, never the only one.
 */
import { useEffect, useState } from 'react'
import { countdown } from '@/lib/formatDate'

const TONES: Record<string, string> = {
  calm: 'bg-info-50 text-info-700',
  soon: 'bg-warning-50 text-warning-700',
  urgent: 'bg-danger-50 text-danger-700 border border-danger-300',
  over: 'bg-danger-600 text-white',
  done: 'bg-success-50 text-success-700',
}

const KIND_LABELS: Record<string, string> = {
  mol_48h: 'MOL',
  wsib_form7: 'WSIB',
  jhsc_reply_21d: 'JHSC',
  cert_expiry: 'Certificate',
  policy_annual: 'Policy review',
  sds_3y: 'SDS review',
}

export function Countdown({
  kind,
  dueAt,
  satisfiedAt,
}: {
  kind: string
  dueAt: string
  satisfiedAt?: string | null
}) {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    if (satisfiedAt) return
    const t = setInterval(() => setNow(new Date()), 60_000)
    return () => clearInterval(t)
  }, [satisfiedAt])

  const name = KIND_LABELS[kind] ?? kind.replace(/_/g, ' ')

  if (satisfiedAt) {
    return (
      <span className={`inline-flex items-center gap-2 rounded-md px-2.5 py-1 text-xs font-medium ${TONES.done}`}>
        <span className="h-1.5 w-1.5 rounded-full bg-success-500" aria-hidden />
        {name} · filed
      </span>
    )
  }

  const c = countdown(dueAt, now)
  if (!c) return null

  return (
    <span
      className={`inline-flex items-center gap-2 rounded-md px-2.5 py-1 text-xs font-medium tabular-nums ${TONES[c.tone]}`}
      title={`Due ${new Date(dueAt).toLocaleString()}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${c.tone === 'over' ? 'bg-white' : 'bg-current'} ${c.tone === 'urgent' ? 'animate-pulse motion-reduce:animate-none' : ''}`}
        aria-hidden
      />
      {name} · {c.label}
    </span>
  )
}
