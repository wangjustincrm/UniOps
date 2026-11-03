import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  ActionButton, Button, DateTimeCell, DetailList, ErrorMessage, LocalTable, StatusCell, formatDateTime, queryKeys,
  requestView, type ColumnDef,
} from '@/platform-ui'
import { runsClient, type RunRead } from '@/features/operations/operationsApi'
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
  const [started, setStarted] = useState<number | null>(null)
  const spec = { page: 1, pageSize: 5, filters: [{ field: 'scope', op: 'eq' as const, value: scope }], sort: [{ field: 'started_at', desc: true }] }
  const finished = (items: RunRead[] | undefined, id: number | null) =>
    id !== null && !!items?.some((r) => r.id === id && r.status !== 'RUNNING')
  const runs = useQuery({
    queryKey: [...queryKeys.list(runsClient), 'connection', c.id], queryFn: () => runsClient.list(spec),
    // While the run started here (or any) is going, follow it until it ends.
    refetchInterval: (q) => (q.state.data?.items.some((r) => r.status === 'RUNNING')
      || (started !== null && !finished(q.state.data?.items, started)) ? 2000 : false),
  })
  const mine = started !== null ? runs.data?.items.find((r) => r.id === started) : undefined
  const running = started !== null && (!mine || mine.status === 'RUNNING')
  const done = mine && mine.status !== 'RUNNING' ? mine : undefined
  // When it ends, the list's last run / next run columns are refreshed.
  useEffect(() => {
    if (done) void qc.invalidateQueries({ queryKey: queryKeys.list(connectionsClient) })
  }, [done, qc])
  const run = useMutation({
    mutationFn: () => runConnectionNow(c.id),
    onSuccess: async ({ run_id }) => {
      setStarted(run_id)
      await qc.invalidateQueries({ queryKey: [...queryKeys.list(runsClient), 'connection', c.id] })  // follow it now
    },
  })
  return (
    <section aria-label="Schedule" className="flex flex-col gap-3 text-sm">
      <h3 className="font-semibold text-neutral-800">Schedule</h3>
      <DetailList items={[
        ['Runs', `${describeSchedule(c)}${c.schedule_paused ? ' — paused' : ''}`],
        ['Last run', c.last_run_at ? `${formatDateTime(c.last_run_at)} · ${c.last_run_status ?? ''}${c.last_run_summary ? ` · ${c.last_run_summary}` : ''}` : 'never'],
        ['Last error', c.last_error],
        ['Next run', <NextRun key="n" c={c} />],
      ]} />
      <div className="flex flex-wrap items-center gap-2">
        <ActionButton size="sm" permission={RETRY} disabled={!c.active || running}
          onAction={() => run.mutateAsync().catch(() => {})}>Run now</ActionButton>
        {running && <span role="status">Run {started} is running…</span>}
        {done && (
          <span role="status">Run {done.id}: {done.status}{done.summary?.text ? ` — ${done.summary.text}` : done.error ? ` — ${done.error}` : ''}</span>
        )}
      </div>
      <ErrorMessage error={run.error} messages={{ 'run.already_running': `${c.name} is running; wait for it to finish.` }} />
      <LocalTable<RunRead> ariaLabel="Last runs" columns={runColumns} rows={runs.data?.items ?? []} rowKey={(r) => r.id}
        emptyTitle="No run yet" />
      <span>
        <Button size="sm" variant="secondary" onClick={() => {
          requestView('runs', { name: c.name, state: { filters: [{ field: 'scope', op: 'eq', value: scope }] } })
          navigate('/runs')
        }}>All runs of {c.name}</Button>
      </span>
    </section>
  )
}
