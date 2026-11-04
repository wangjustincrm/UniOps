import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  ActionButton, Button, DateTimeCell, DetailList, LocalTable, Section, StatusCell, formatDateTime, queryKeys,
  requestView, type ColumnDef,
} from '@/platform-ui'
import { runsClient, type RunRead } from '@/features/operations/operationsApi'
import { useRunFollower } from '@/features/operations/useRunFollower'
import { connectionsClient, describeSchedule, runConnectionNow, type ConnectionRead } from './connectionsApi'
import { NextRun } from './SchedulerState'

const RETRY = 'sales.file.retry'

const runColumns: ColumnDef<RunRead, unknown>[] = [
  { accessorKey: 'started_at', header: 'Started', cell: ({ row }) => <DateTimeCell value={row.original.started_at} /> },
  { accessorKey: 'trigger', header: 'Trigger' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusCell value={row.original.status} /> },
  { id: 'summary', header: 'Summary', cell: ({ row }) => row.original.summary?.text ?? row.original.error ?? '' },
]

/** A connection's schedule in its sheet: how often, the last and next run,
 *  "Run now" (sales.file.retry; its result shows when it finishes) and its
 *  last five runs, which open in Runs. The schedule itself is edited in the
 *  form above. */
export function ConnectionSchedule({ connection: c }: { connection: ConnectionRead }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const scope = `connection:${c.id}`
  const follower = useRunFollower()
  const spec = { page: 1, pageSize: 5, filters: [{ field: 'scope', op: 'eq' as const, value: scope }], sort: [{ field: 'started_at', desc: true }] }
  const runs = useQuery({
    queryKey: [...queryKeys.list(runsClient), 'connection', c.id], queryFn: () => runsClient.list(spec),
    // While the run started here (or any) is going, the table follows it too.
    refetchInterval: (q) => (follower.running || q.state.data?.items.some((r) => r.status === 'RUNNING') ? 2000 : false),
  })
  // Its progress in the sheet's status row; when it ends, the list's last run / next run columns are refreshed.
  const runNow = async () => {
    await follower.run(() => runConnectionNow(c.id))
    await Promise.all([qc.invalidateQueries({ queryKey: queryKeys.list(connectionsClient) }),
      qc.invalidateQueries({ queryKey: [...queryKeys.list(runsClient), 'connection', c.id] })])
  }
  return (
    <Section title="Schedule" plain className="gap-3 text-sm">
      <DetailList items={[
        ['Runs', `${describeSchedule(c)}${c.schedule_paused ? ' — paused' : ''}`],
        ['Last run', c.last_run_at ? `${formatDateTime(c.last_run_at)} · ${c.last_run_status ?? ''}${c.last_run_summary ? ` · ${c.last_run_summary}` : ''}` : 'never'],
        ['Last error', c.last_error],
        ['Next run', <NextRun key="n" c={c} />],
      ]} />
      <div className="flex flex-wrap items-center gap-2">
        <ActionButton size="sm" permission={RETRY} disabled={!c.active}
          errorMessages={{ 'run.already_running': `${c.name} is running; wait for it to finish.` }} onAction={runNow}>Run now</ActionButton>
      </div>
      <LocalTable<RunRead> ariaLabel="Last runs" columns={runColumns} rows={runs.data?.items ?? []} rowKey={(r) => r.id}
        emptyTitle="No run yet" />
      <span>
        <Button size="sm" variant="secondary" onClick={() => {
          requestView('runs', { name: c.name, state: { filters: [{ field: 'scope', op: 'eq', value: scope }] } })
          navigate('/runs')
        }}>All runs of {c.name}</Button>
      </span>
    </Section>
  )
}
