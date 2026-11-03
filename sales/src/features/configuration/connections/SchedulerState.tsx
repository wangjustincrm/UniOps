import { Badge, DateTimeCell, Tooltip } from '@/platform-ui'
import type { ConnectionRead } from './connectionsApi'
import { useSchedulerEnabled } from './schedulerEnabled'

const OFF_TIP = 'SALES_SCHEDULER_ENABLED is off in this environment: no connection is read by itself, '
  + 'so the next run time is only what it would be. Use Run now.'

/** On top of Connections when nothing runs by itself. */
export function SchedulerOffBanner() {
  if (useSchedulerEnabled()) return null
  return (
    <p role="status" className="rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-700">
      Automatic runs are off in this environment (SALES_SCHEDULER_ENABLED). The next run times below never come:
      use Run now on a connection to read it.
    </p>
  )
}

/** A connection's next run: the time, "Overdue" when it should have run and did
 *  not, or — the scheduler off — that nothing runs by itself. */
export function NextRun({ c }: { c: ConnectionRead }) {
  const enabled = useSchedulerEnabled()
  if (!enabled) {
    return <Tooltip tip={OFF_TIP}><span className="text-neutral-500">Automatic runs off: use Run now</span></Tooltip>
  }
  if (!c.next_run_at) return <span className="text-neutral-500">{c.active ? 'at the next tick' : '—'}</span>
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <DateTimeCell value={c.next_run_at} />
      {c.overdue && <Badge variant="danger">Overdue</Badge>}
    </span>
  )
}
