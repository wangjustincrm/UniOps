import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button, DetailList, LocalTable, SchemaForm, Section, formatDateTime, queryKeys,
  useOpenView, useCan, useNotices, type ColumnDef, columnsOf
 } from '@/platform-ui'
import { runsClient, type RunRead } from '@/features/operations/operationsApi'
import { connectionsClient, type ConnectionRead } from './connectionsApi'
import { connectionScheduleSchema, connectionToFormValues, scheduleFields } from './connectionSchema'
import { RunNowButton } from './RunNowButton'
import { NextRun } from './SchedulerState'

const colRunRead = columnsOf<RunRead>()

const runColumns: ColumnDef<RunRead, unknown>[] = [
  colRunRead.datetime('started_at', 'Started'),
  colRunRead.text('trigger', 'Trigger'),
  colRunRead.status('status', 'Status'),
  { id: 'summary', header: 'Summary', cell: ({ row }) => row.original.summary?.text ?? row.original.error ?? '' },
]

// The fields without their group: the tab is the group.
const fields = scheduleFields.map((f) => ({ ...f, section: undefined }))
const errorMessages = { 'entity.stale': 'Someone else changed this connection. Close it and open it again.' }

/** A connection's one place for its schedule: its last and next run, the form
 *  with its live sentence (sales.config; read-only otherwise), "Run now"
 *  (sales.file.retry) and its last five runs, which open in Runs. */
const scheduleValues = (c: ConnectionRead) => {
  const all = connectionToFormValues(c)
  return Object.fromEntries(fields.filter((f) => f.type !== 'note').map((f) => [f.name, all[f.name]]))
}

export function ConnectionSchedule({ connection: c }: { connection: ConnectionRead }) {
  const openView = useOpenView()
  const qc = useQueryClient()
  const { notify } = useNotices()
  const canEdit = useCan('sales.config')
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
        ['Last run', c.last_run_at ? `${formatDateTime(c.last_run_at)} · ${c.last_run_status ?? ''}${c.last_run_summary ? ` · ${c.last_run_summary}` : ''}` : 'never'],
        ['Last error', c.last_error],
        ['Next run', <NextRun key="n" c={c} />],
      ]} />
      <SchemaForm key={`${c.id}:${c.version}`} schema={connectionScheduleSchema} fields={fields} changesOnly readOnly={!canEdit}
        submitLabel="Save schedule" errorMessages={errorMessages} defaultValues={scheduleValues(c)}
        onSubmit={async (values) => {
          await connectionsClient.update(c.id, values, c.version)
          await qc.invalidateQueries({ queryKey: queryKeys.list(connectionsClient) })
          notify({ tone: 'success', text: 'Schedule saved' })
        }} />
      <div className="flex flex-wrap items-center gap-2">
        <RunNowButton connection={c} />
      </div>
      <LocalTable<RunRead> ariaLabel="Last runs" columns={runColumns} rows={runs.data?.items ?? []} rowKey={(r) => r.id}
        emptyTitle="No run yet" />
      <span>
        <Button size="sm" variant="secondary" onClick={() => {
          openView('/runs', 'runs', { name: c.name, state: { filters: [{ field: 'scope', op: 'eq', value: scope }] } })
        }}>All runs of {c.name}</Button>
      </span>
    </Section>
  )
}
