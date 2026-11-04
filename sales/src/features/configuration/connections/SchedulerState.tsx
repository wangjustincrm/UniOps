import { Badge, DateTimeCell, Tooltip, Callout, Muted } from '@/platform-ui'
import type { ConnectionRead } from './connectionsApi'
import { useAutomaticRuns } from './connectionSettings'

const WORDS = {
  off: 'Automatic runs are off: a connection is read only with Run now.',
  unavailable: 'Automatic runs are not available on this server: a connection is read only with Run now.',
} as const

/** When nothing runs by itself, and why. */
export function SchedulerOffBanner() {
  const { state } = useAutomaticRuns()
  if (state !== 'off' && state !== 'unavailable') return null
  return <Callout tone="neutral" label="Automatic runs">{WORDS[state]}</Callout>
}

/** A connection's next run: the time, "Overdue" when it should have run and did
 *  not, or a short Off while nothing runs by itself (the card and the Callout say why). */
export function NextRun({ c }: { c: ConnectionRead }) {
  const { state } = useAutomaticRuns()
  if (state === 'off' || state === 'unavailable') return <Tooltip tip={WORDS[state]}><Muted>Off</Muted></Tooltip>
  if (!c.next_run_at) return <Muted>{c.active ? 'at the next tick' : '—'}</Muted>
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <DateTimeCell value={c.next_run_at} />
      {c.overdue && <Badge variant="danger">Overdue</Badge>}
    </span>
  )
}
