import { Badge, DateTimeCell, Tooltip, Callout, Muted } from '@/platform-ui'
import type { ConnectionRead } from './connectionsApi'
import { useSchedulerEnabled } from './schedulerEnabled'

const OFF_TIP = 'Automatic runs are off in this environment: no connection is read by itself. Use Run now.'

/** When nothing runs by itself: what to do instead. */
export function SchedulerOffBanner() {
  if (useSchedulerEnabled()) return null
  return (
    <Callout label="Automatic runs off">Automatic runs are off here: use Run now on a connection to read it.</Callout>
  )
}

/** A connection's next run: the time, "Overdue" when it should have run and did
 *  not, or — the scheduler off — a short Off (the card and the Callout say what to do). */
export function NextRun({ c }: { c: ConnectionRead }) {
  const enabled = useSchedulerEnabled()
  if (!enabled) {
    return <Tooltip tip={OFF_TIP}><Muted>Off</Muted></Tooltip>
  }
  if (!c.next_run_at) return <Muted>{c.active ? 'at the next tick' : '—'}</Muted>
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <DateTimeCell value={c.next_run_at} />
      {c.overdue && <Badge variant="danger">Overdue</Badge>}
    </span>
  )
}
