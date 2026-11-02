import { useState } from 'react'
import {
  ActionButton, DateTimeCell, ErrorMessage, ListPage, LocalTable, PageHeader, StatusCell, Tabs, formatDateTime, useToast,
  type ColumnDef, type QuerySpec,
} from '@/platform-ui'
import { logClient, runFor, runNow, runsClient, type LogRead, type PassReport, type RunRead } from './operationsApi'

const RETRY = 'sales.file.retry'
const PASS_COLUMNS: (keyof PassReport)[] = ['listed', 'too_new', 'already_here', 'not_ours', 'stored', 'duplicates', 'failed']

/** Each connection's pass report: one in a connection's run, all of them in an old cycle. */
function passesOf(r: RunRead): Record<string, PassReport> {
  if (r.summary?.pass) return { [r.summary.connection ?? r.scope ?? 'connection']: r.summary.pass }
  return r.summary?.connections ?? {}
}

function total(r: RunRead, k: keyof PassReport): number {
  return Object.values(passesOf(r)).reduce((n, p) => n + Number(p[k] ?? 0), 0)
}

const runColumns: ColumnDef<RunRead, unknown>[] = [
  { accessorKey: 'started_at', header: 'Started', cell: ({ row }) => <DateTimeCell value={row.original.started_at} /> },
  { accessorKey: 'scope', header: 'For', cell: ({ row }) => runFor(row.original) },
  { accessorKey: 'trigger', header: 'Trigger' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusCell value={row.original.status} /> },
  { id: 'stored', header: 'Stored', cell: ({ row }) => total(row.original, 'stored') },
  { id: 'failed', header: 'Failed', cell: ({ row }) => total(row.original, 'failed') },
  { id: 'summary', header: 'Summary', cell: ({ row }) => row.original.summary?.text ?? '' },
  { accessorKey: 'finished_at', header: 'Finished', cell: ({ row }) => <DateTimeCell value={row.original.finished_at} /> },
]

const logColumns: ColumnDef<LogRead, unknown>[] = [
  { accessorKey: 'at', header: 'At', cell: ({ row }) => <DateTimeCell value={row.original.at} /> },
  { accessorKey: 'level', header: 'Level', cell: ({ row }) => <StatusCell value={row.original.level} /> },
  { accessorKey: 'step', header: 'Step' },
  { accessorKey: 'message', header: 'Message', cell: ({ row }) => <span className="break-words">{row.original.message}</span> },
  { accessorKey: 'run_id', header: 'Run' },
  { accessorKey: 'document_id', header: 'Document' },
]

function LogTable({ fixed, searchable = true }: { fixed?: QuerySpec['filters']; searchable?: boolean }) {
  // Inside a run: that run's log (fixed filter, no views).
  return <ListPage<LogRead> resource={logClient} fixed={fixed} searchable={searchable} columns={logColumns} emptyTitle="Nothing logged" />
}

type Pass = PassReport & { name: string }

const passColumns: ColumnDef<Pass, unknown>[] = [
  { accessorKey: 'name', header: 'Connection', cell: ({ row }) => <span className="font-medium">{row.original.name}</span> },
  ...PASS_COLUMNS.map((k): ColumnDef<Pass, unknown> => ({ accessorKey: k, header: k.replace('_', ' ') })),
  { accessorKey: 'status', header: 'State' },
]

function RunDetail({ run }: { run: RunRead }) {
  const passes: Pass[] = Object.entries(passesOf(run)).map(([name, p]) => ({ ...p, name }))
  return (
    <div className="flex flex-col gap-4 text-sm">
      <p>{runFor(run)} · {run.trigger} · started {formatDateTime(run.started_at)} · finished {formatDateTime(run.finished_at)}</p>
      {run.summary?.text && <p>{run.summary.text}</p>}
      {run.error && <p role="alert" className="text-danger-600">{run.error}</p>}
      <section aria-label="Pass report">
        <h3 className="mb-1 font-semibold text-neutral-800">Pass report</h3>
        {passes.length === 0 ? <p className="text-neutral-500">{run.scope === 'system' ? 'The system job reads no connection.' : 'No connection was read.'}</p> : (
          <LocalTable<Pass> ariaLabel="Pass report" columns={passColumns} rows={passes} rowKey={(p) => p.name} />
        )}
      </section>
      <section aria-label="Run log">
        <h3 className="mb-1 font-semibold text-neutral-800">Log</h3>
        <LogTable fixed={[{ field: 'run_id', op: 'eq', value: String(run.id) }]} searchable={false} />
      </section>
    </div>
  )
}

function Runs() {
  const toast = useToast()
  const [error, setError] = useState<unknown>(null)
  return (
    <ListPage<RunRead>
      resource={runsClient}
      columns={runColumns}
      searchable={false}
      emptyTitle="No runs yet"
      notice={<ErrorMessage error={error} />}
      refetchInterval={(rows) => (rows.some((r) => r.status === 'RUNNING') ? 3000 : false)}
      detailTitle={(r) => `Run ${r.id}`}
      detailClassName="max-w-3xl"
      renderDetail={(r) => <RunDetail run={r} />}
      toolbar={({ refresh }) => (
        <ActionButton size="sm" permission={RETRY} onAction={async () => {
          setError(null)
          try {
            const { run_id } = await runNow()
            toast({ variant: 'success', title: `Run ${run_id} started` })
            await refresh()
          } catch (e) {
            setError(e)
          }
        }}>
          Run now
        </ActionButton>
      )}
    />
  )
}

/** Operations → Runs and log (FR-OB-07, -53, -76). */
export default function RunsPage() {
  return (
    <div className="flex flex-col gap-4">
      <PageHeader title="Runs and log" description="Each connection's runs (its pass report) and the system job's, and everything the module logged. Run now runs every active connection, then the system job." />
      <Tabs items={[{ value: 'runs', label: 'Runs', content: <Runs /> }, { value: 'log', label: 'Log', content: <LogTable /> }]} />
    </div>
  )
}
