import { Badge, DateTimeCell, Tooltip, Callout, Muted } from '@/platform-ui'
import type { ConnectionRead } from './connectionsApi'
import { useSchedulerEnabled } from './schedulerEnabled'

const OFF_TIP = 'SALES_SCHEDULER_ENABLED is off in this environment: no connection is read by itself, '
  + 'so the next run time is only what it would be. Use Run now.'

/** On top of Connections when nothing runs by itself. */
export function SchedulerOffBanner() {
  if (useSchedulerEnabled()) return null
  return (
    <Callout label="Automatic runs off">
      Automatic runs are off in this environment (SALES_SCHEDULER_ENABLED). The next run times below never come:
      use Run now on a connection to read it.
    </Callout>
  )
}

/** A connection's next run: the time, "Overdue" when it should have run and did
 *  not, or — the scheduler off — that nothing runs by itself. */
export function NextRun({ c }: { c: ConnectionRead }) {
  const enabled = useSchedulerEnabled()
  if (!enabled) {
    return <Tooltip tip={OFF_TIP}><Muted>Automatic runs off: use Run now</Muted></Tooltip>
  }
  if (!c.next_run_at) return <Muted>{c.active ? 'at the next tick' : '—'}</Muted>
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <DateTimeCell value={c.next_run_at} />
      {c.overdue && <Badge variant="danger">Overdue</Badge>}
    </span>
  )
}
