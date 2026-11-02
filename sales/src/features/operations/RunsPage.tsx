import { useState } from 'react'
import {
  ActionButton, DateTimeCell, ErrorMessage, ListPage, PageHeader, StatusCell, Tabs, formatDateTime, useToast, type ColumnDef,
  type QuerySpec,
} from '@/platform-ui'
import { logClient, runNow, runsClient, type LogRead, type PassReport, type RunRead } from './operationsApi'

const RETRY = 'sales.file.retry'
const PASS_COLUMNS: (keyof PassReport)[] = ['listed', 'too_new', 'already_here', 'not_ours', 'stored', 'duplicates', 'failed']

function total(r: RunRead, k: keyof PassReport): number {
  return Object.values(r.summary?.connections ?? {}).reduce((n, p) => n + Number(p[k] ?? 0), 0)
}

const runColumns: ColumnDef<RunRead, unknown>[] = [
  { accessorKey: 'started_at', header: 'Started', cell: ({ row }) => <DateTimeCell value={row.original.started_at} /> },
  { accessorKey: 'trigger', header: 'Trigger' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusCell value={row.original.status} /> },
  { id: 'stored', header: 'Stored', cell: ({ row }) => total(row.original, 'stored') },
  { id: 'failed', header: 'Failed', cell: ({ row }) => total(row.original, 'failed') },
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

function RunDetail({ run }: { run: RunRead }) {
  const passes = Object.entries(run.summary?.connections ?? {})
  return (
    <div className="flex flex-col gap-4 text-sm">
      <p>{run.trigger} · started {formatDateTime(run.started_at)} · finished {formatDateTime(run.finished_at)}</p>
      {run.error && <p role="alert" className="text-danger-600">{run.error}</p>}
      <section aria-label="Pass report">
        <h3 className="mb-1 font-semibold text-neutral-800">Pass report</h3>
        {passes.length === 0 ? <p className="text-neutral-500">No connection was read.</p> : (
          <table className="w-full text-xs">
            <thead><tr className="text-left text-neutral-500"><th>Connection</th>{PASS_COLUMNS.map((k) => <th key={k}>{k.replace('_', ' ')}</th>)}<th>State</th></tr></thead>
            <tbody>
              {passes.map(([name, p]) => (
                <tr key={name} className="border-t border-neutral-100">
                  <td className="py-1 font-medium">{name}</td>{PASS_COLUMNS.map((k) => <td key={k}>{p[k]}</td>)}<td>{p.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
      <PageHeader title="Runs and log" description="Each cycle with its pass report per connection, and everything the module logged." />
      <Tabs items={[{ value: 'runs', label: 'Runs', content: <Runs /> }, { value: 'log', label: 'Log', content: <LogTable /> }]} />
    </div>
  )
}
