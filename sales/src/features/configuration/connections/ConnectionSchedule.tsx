import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Button, DateTimeCell, DetailList, LocalTable, Section, StatusCell, formatDateTime, queryKeys,
  requestView, type ColumnDef,
} from '@/platform-ui'
import { runsClient, type RunRead } from '@/features/operations/operationsApi'
import { describeSchedule, type ConnectionRead } from './connectionsApi'
import { RunNowButton } from './RunNowButton'
import { NextRun } from './SchedulerState'

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
  const navigate = useNavigate()
  const scope = `connection:${c.id}`
  const spec = { page: 1, pageSize: 5, filters: [{ field: 'scope', op: 'eq' as const, value: scope }], sort: [{ field: 'started_at', desc: true }] }
  const runs = useQuery({
    queryKey: [...queryKeys.list(runsClient), 'connection', c.id], queryFn: () => runsClient.list(spec),
    // While a run is going, the table follows it.
    refetchInterval: (q) => (q.state.data?.items.some((r) => r.status === 'RUNNING') ? 2000 : false),
  })
  return (
    <Section title="Schedule" plain className="gap-3 text-sm">
      <DetailList items={[
        ['Runs', `${describeSchedule(c)}${c.schedule_paused ? ' — paused' : ''}`],
        ['Last run', c.last_run_at ? `${formatDateTime(c.last_run_at)} · ${c.last_run_status ?? ''}${c.last_run_summary ? ` · ${c.last_run_summary}` : ''}` : 'never'],
        ['Last error', c.last_error],
        ['Next run', <NextRun key="n" c={c} />],
      ]} />
      <div className="flex flex-wrap items-center gap-2">
        <RunNowButton connection={c} />
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
