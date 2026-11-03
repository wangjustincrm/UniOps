import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { z } from 'zod'
import {
  ActionButton, Badge, Button, DateTimeCell, ErrorMessage, LocalTable, RowActions, SchemaForm, FormSheet, StatusCell, businessErrorOf,
  requestOpen, useCan, useToast, type ColumnDef, type FieldSpec,
} from '@/platform-ui'
import { RefreshCw } from 'lucide-react'
import { runConnectionNow } from '@/features/configuration/connections/connectionsApi'
import { followRun, runToast } from '@/features/operations/followRun'
import { runSystemNow } from '@/features/operations/operationsApi'
import { saveScheduler, type HealthRead, type JobRead } from './healthApi'

const schema = z.object({ paused: z.boolean(), system_minutes: z.number({ error: 'Enter minutes' }).int().min(1, 'At least 1').max(1440) })
const fields: FieldSpec[] = [
  { name: 'paused', label: 'Pause every job', type: 'checkbox',
    hint: 'The master switch: no connection job and no system job runs on its schedule (Run now still works).' },
  { name: 'system_minutes', label: 'System job: every (minutes)', type: 'number', required: true,
    hint: 'Queues 940s and 943s, sends the outbox, times out ACKs, moves invoices on, compares with Zoho in the shadow run.' },
]

/** Health → Scheduler: every job (each connection's, the system job) with its
 *  last and next run; overdue ones flagged. The system job's Run now, and the
 *  master switch and system interval in a sheet (sales.config). */
export function SchedulerSection({ health: h }: { health: HealthRead }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()
  const canConfig = useCan('sales.config')
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['sales', 'health'] })
  // A job's Run now: followed to its end, then a toast with its summary.
  const runJob = async (j: JobRead) => {
    try {
      const { run_id } = j.connection_id === null ? await runSystemNow() : await runConnectionNow(j.connection_id)
      toast(runToast(await followRun(run_id)))
    } catch (e) {
      const be = businessErrorOf(e)
      toast({ variant: 'error', title: `Not run: ${j.label}`,
        description: be?.code === 'run.already_running' ? `${j.label} is running; wait for it to finish.` : (be?.message ?? String(e)) })
    }
    await refresh()
  }
  const columns: ColumnDef<JobRead, unknown>[] = [
    { accessorKey: 'label', header: 'Job', cell: ({ row: { original: j } }) => j.connection_id === null ? j.label : (
      <Button size="sm" variant="ghost" onClick={() => { requestOpen('/config/connections', j.connection_id!); navigate('/config/connections') }}>
        {j.label}
      </Button>
    ) },
    { accessorKey: 'schedule', header: 'Schedule', cell: ({ row: { original: j } }) => (
      <span className="flex items-center gap-1">{j.schedule}{j.paused && <Badge variant="warning">Paused</Badge>}</span>
    ) },
    { accessorKey: 'last_run_at', header: 'Last run', cell: ({ row: { original: j } }) => !j.last_run_at ? 'never' : (
      <span className="flex flex-col">
        <span className="flex items-center gap-1"><DateTimeCell value={j.last_run_at} />{j.last_run_status && <StatusCell value={j.last_run_status} />}</span>
        {j.last_run_summary && <span className="text-xs text-neutral-500">{j.last_run_summary}</span>}
      </span>
    ) },
    { accessorKey: 'next_run_at', header: 'Next run', cell: ({ row: { original: j } }) => (
      <span className="flex items-center gap-1">{j.next_run_at ? <DateTimeCell value={j.next_run_at} /> : 'at the next tick'}
        {j.overdue && <Badge variant="danger">Overdue</Badge>}</span>
    ) },
    { id: 'actions', header: '', cell: ({ row: { original: j } }) => (
      <RowActions actions={[{ key: 'run', label: `Run now: ${j.label}`, icon: <RefreshCw className="h-4 w-4" />,
        permission: 'sales.file.retry', onAction: () => runJob(j) }]} />
    ) },
  ]
  return (
    <section aria-label="Scheduler" className="flex flex-col gap-2">
      <h2 className="font-semibold text-neutral-800">Scheduler</h2>
      <p className="text-sm text-neutral-600">
        {h.scheduler_paused ? 'Every job is paused (master switch).' : `System job every ${h.system_minutes} min; each connection on its own schedule.`}
        {!h.scheduler_enabled && ' The scheduler does not run in this environment: jobs run only with Run now.'}
      </p>
      <LocalTable<JobRead> ariaLabel="Scheduled jobs" columns={columns} rows={h.jobs} rowKey={(j) => j.scope} />
      <div className="flex flex-wrap gap-2">
        <ActionButton size="sm" permission="sales.file.retry" onAction={async () => {
          setError(null)
          try {
            const { run_id } = await runSystemNow()
            toast({ variant: 'success', title: `System job: run ${run_id} started` })
            await refresh()
          } catch (e) { setError(e) }
        }}>Run the system job now</ActionButton>
        {canConfig && <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>Scheduler settings</Button>}
      </div>
      <ErrorMessage error={error} messages={{ 'run.already_running': 'The system job is running; wait for it to finish.' }} />
      <FormSheet open={editing} onOpenChange={setEditing} title="Scheduler settings">
        {editing && (
          <SchemaForm schema={schema} fields={fields} defaultValues={{ paused: h.scheduler_paused, system_minutes: h.system_minutes }}
            onCancel={() => setEditing(false)}
            onSubmit={async (v) => { await saveScheduler(v as { paused: boolean; system_minutes: number }); setEditing(false); await refresh() }} />
        )}
      </FormSheet>
    </section>
  )
}
