import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  ActionButton, Badge, Button, DateTimeCell, LocalTable, NoticeScope, RowActions, Section, StatusCell, businessErrorOf,
  useOpenRecord, useNotices, type ColumnDef, Hint,
} from '@/platform-ui'
import { RefreshCw } from 'lucide-react'
import { runConnectionNow } from '@/features/configuration/connections/connectionsApi'
import { useRunFollower } from '@/features/operations/useRunFollower'
import { runSystemNow } from '@/features/operations/operationsApi'
import type { HealthRead, JobRead } from './healthApi'

/** Health → Scheduler: every job (each connection's, the system job) with its
 *  last and next run; overdue ones flagged; the system job's Run now. Status
 *  only: Automatic runs are switched on Connections, its card. Health is no list:
 *  this section is its own notice scope, with its status row. */
export function SchedulerSection({ health }: { health: HealthRead }) {
  return <NoticeScope><Scheduler health={health} /></NoticeScope>
}

function Scheduler({ health: h }: { health: HealthRead }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const openRecord = useOpenRecord()
  const { notify } = useNotices()
  const follower = useRunFollower()
  const state = !h.scheduler_enabled ? 'unavailable' : h.scheduler_paused ? 'off' : 'on'
  const refresh = () => qc.invalidateQueries({ queryKey: ['sales', 'health'] })
  // A job's Run now: followed to its end, in the section's status row.
  const runJob = async (j: JobRead) => {
    try {
      await follower.run(() => (j.connection_id === null ? runSystemNow() : runConnectionNow(j.connection_id)))
    } catch (e) {
      const be = businessErrorOf(e)
      notify({ tone: 'error', text: be?.code === 'run.already_running' ? `${j.label} is running; wait for it to finish.`
        : `Not run: ${j.label} — ${be?.message ?? String(e)}` })
    }
    await refresh()
  }
  const columns: ColumnDef<JobRead, unknown>[] = [
    { accessorKey: 'label', header: 'Job', cell: ({ row: { original: j } }) => j.connection_id === null ? j.label : (
      <Button size="sm" variant="ghost" onClick={() => { openRecord('/config/connections', j.connection_id!) }}>
        {j.label}
      </Button>
    ) },
    { accessorKey: 'schedule', header: 'Schedule', cell: ({ row: { original: j } }) => (
      <span className="flex items-center gap-1">{j.schedule}{state === 'on' && j.paused && <Badge variant="warning">Paused</Badge>}</span>
    ) },
    { accessorKey: 'last_run_at', header: 'Last run', cell: ({ row: { original: j } }) => !j.last_run_at ? 'never' : (
      <span className="flex flex-col">
        <span className="flex items-center gap-1"><DateTimeCell value={j.last_run_at} />{j.last_run_status && <StatusCell value={j.last_run_status} />}</span>
        {j.last_run_summary && <Hint>{j.last_run_summary}</Hint>}
      </span>
    ) },
    { accessorKey: 'next_run_at', header: 'Next run', cell: ({ row: { original: j } }) => state !== 'on' ? 'Off' : (
      <span className="flex items-center gap-1">{j.next_run_at ? <DateTimeCell value={j.next_run_at} /> : 'at the next tick'}
        {j.overdue && <Badge variant="danger">Overdue</Badge>}</span>
    ) },
    { id: 'actions', header: '', cell: ({ row: { original: j } }) => (
      <RowActions actions={[{ key: 'run', label: `Run now: ${j.label}`, icon: <RefreshCw className="h-4 w-4" />,
        permission: 'sales.file.retry', onAction: () => runJob(j) }]} />
    ) },
  ]
  return (
    <Section title="Scheduler" plain level={2}>
      <p className="text-sm text-neutral-600">
        {state === 'on' ? `Automatic runs: On. System job every ${h.system_minutes} min; each connection on its own schedule.`
          : state === 'off' ? 'Automatic runs: Off. Jobs run only with Run now.'
            : 'Automatic runs: not available on this server. Jobs run only with Run now.'}
      </p>
      <LocalTable<JobRead> ariaLabel="Scheduled jobs" columns={columns} rows={h.jobs} rowKey={(j) => j.scope} />
      <div className="flex flex-wrap gap-2">
        <ActionButton size="sm" permission="sales.file.retry" done={(r) => `System job: run ${(r as { run_id: number }).run_id} started`}
          errorMessages={{ 'run.already_running': 'The system job is running; wait for it to finish.' }}
          onAction={async () => {
            const started = await runSystemNow()
            await refresh()
            return started
          }}>Run the system job now</ActionButton>
        <Button size="sm" variant="secondary" onClick={() => navigate('/config/connections')}>Open Connections</Button>
      </div>
    </Section>
  )
}
