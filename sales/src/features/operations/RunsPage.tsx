import { useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, Badge, ErrorMessage, QueryTable, Sheet, Tabs, defaultSpec, useToast, type ColumnDef, type QuerySpec,
} from '@/platform-ui'
import { logClient, runNow, runsClient, type LogRead, type PassReport, type RunRead } from './operationsApi'

const RETRY = 'sales.file.retry'
const runVariant = { RUNNING: 'info', SUCCEEDED: 'success', PARTIAL: 'warning', FAILED: 'danger' } as const
const levelVariant = { ERROR: 'danger', WARNING: 'warning', INFO: 'neutral' } as const
const when = (s: string | null) => (s ? new Date(s).toLocaleString() : '—')
const PASS_COLUMNS: (keyof PassReport)[] = ['listed', 'too_new', 'already_here', 'not_ours', 'stored', 'duplicates', 'failed']

function total(r: RunRead, k: keyof PassReport): number {
  return Object.values(r.summary?.connections ?? {}).reduce((n, p) => n + Number(p[k] ?? 0), 0)
}

const runColumns: ColumnDef<RunRead, unknown>[] = [
  { accessorKey: 'started_at', header: 'Started', cell: ({ row }) => when(row.original.started_at) },
  { accessorKey: 'trigger', header: 'Trigger' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <Badge variant={runVariant[row.original.status]}>{row.original.status}</Badge> },
  { id: 'stored', header: 'Stored', cell: ({ row }) => total(row.original, 'stored') },
  { id: 'failed', header: 'Failed', cell: ({ row }) => total(row.original, 'failed') },
  { accessorKey: 'finished_at', header: 'Finished', cell: ({ row }) => when(row.original.finished_at) },
]

const logColumns: ColumnDef<LogRead, unknown>[] = [
  { accessorKey: 'at', header: 'At', cell: ({ row }) => when(row.original.at) },
  { accessorKey: 'level', header: 'Level', cell: ({ row }) => <Badge variant={levelVariant[row.original.level]}>{row.original.level}</Badge> },
  { accessorKey: 'step', header: 'Step' },
  { accessorKey: 'message', header: 'Message', cell: ({ row }) => <span className="break-words">{row.original.message}</span> },
  { accessorKey: 'run_id', header: 'Run' },
  { accessorKey: 'document_id', header: 'Document' },
]

function LogTable({ fixed, searchable = true }: { fixed?: QuerySpec['filters']; searchable?: boolean }) {
  const [spec, setSpec] = useState<QuerySpec>({ ...defaultSpec(), filters: fixed ?? [] })
  const list = useQuery({ queryKey: ['log', spec], queryFn: () => logClient.list(spec), placeholderData: keepPreviousData })
  return (
    <QueryTable<LogRead>
      columns={logColumns}
      resource={logClient}
      locked={(fixed ?? []).map((f) => f.field)}
      rows={list.data?.items ?? []}
      total={list.data?.total ?? 0}
      groups={list.data?.groups}
      groupsTruncated={list.data?.groups_truncated}
      spec={spec}
      onSpecChange={(s) => setSpec({ ...s, filters: [...(fixed ?? []), ...(s.filters ?? []).filter((f) => !fixed?.some((x) => x.field === f.field))] })}
      sortable={['at', 'level']}
      searchable={searchable}
      loading={list.isLoading}
      error={list.error}
      onRetry={() => list.refetch()}
      rowKey={(e) => e.id}
      emptyTitle="Nothing logged"
    />
  )
}

function RunDetail({ run }: { run: RunRead }) {
  const passes = Object.entries(run.summary?.connections ?? {})
  return (
    <div className="flex flex-col gap-4 text-sm">
      <p>{run.trigger} · started {when(run.started_at)} · finished {when(run.finished_at)}</p>
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
  const qc = useQueryClient()
  const toast = useToast()
  const [spec, setSpec] = useState<QuerySpec>(defaultSpec())
  const [open, setOpen] = useState<RunRead | null>(null)
  const [error, setError] = useState<unknown>(null)
  const list = useQuery({
    queryKey: ['runs', spec], queryFn: () => runsClient.list(spec), placeholderData: keepPreviousData,
    refetchInterval: (q) => (q.state.data?.items.some((r) => r.status === 'RUNNING') ? 3000 : false),
  })
  const current = open ? (list.data?.items.find((r) => r.id === open.id) ?? open) : null
  return (
    <>
      <ErrorMessage error={error} />
      <QueryTable<RunRead>
        columns={runColumns}
        resource={runsClient}
        rows={list.data?.items ?? []}
        total={list.data?.total ?? 0}
        groups={list.data?.groups}
        groupsTruncated={list.data?.groups_truncated}
        spec={spec}
        onSpecChange={setSpec}
        sortable={['started_at', 'status']}
        loading={list.isLoading}
        error={list.error}
        onRetry={() => list.refetch()}
        rowKey={(r) => r.id}
        onRowClick={setOpen}
        emptyTitle="No runs yet"
        toolbar={
          <ActionButton size="sm" permission={RETRY} onAction={async () => {
            setError(null)
            try {
              const { run_id } = await runNow()
              toast({ variant: 'success', title: `Run ${run_id} started` })
              await qc.invalidateQueries({ queryKey: ['runs'] })
            } catch (e) {
              setError(e)
            }
          }}>
            Run now
          </ActionButton>
        }
      />
      <Sheet open={current !== null} onOpenChange={(o) => { if (!o) setOpen(null) }} title={current ? `Run ${current.id}` : ''} className="max-w-3xl">
        {current && <RunDetail run={current} />}
      </Sheet>
    </>
  )
}

/** Operations → Runs and log (FR-OB-07, -53, -76). */
export default function RunsPage() {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold text-neutral-900">Runs and log</h1>
        <p className="text-sm text-neutral-500">Each cycle with its pass report per connection, and everything the module logged.</p>
      </div>
      <Tabs items={[{ value: 'runs', label: 'Runs', content: <Runs /> }, { value: 'log', label: 'Log', content: <LogTable /> }]} />
    </div>
  )
}
