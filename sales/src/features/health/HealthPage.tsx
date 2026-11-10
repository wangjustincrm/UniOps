import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  ActionButton, Badge, Button, DetailList, ErrorMessage, ErrorView, LocalTable, PageHeader, Section, Skeleton, StatCard, formatDateTime,
  useOpenRecord, type ColumnDef,
  useRefresh, columnsOf
 } from '@/platform-ui'
import { testConnection, type ConnectionTest } from '@/features/configuration/connections/connectionsApi'
import { fetchHealth, type CheckRead, type GateEventRead, type HealthRead } from './healthApi'
import { HealthBadge } from './HealthBadge'
import { useOpenList } from './openList'
import { SchedulerSection } from './SchedulerSection'

const colCheckRead = columnsOf<CheckRead>()
const colGateEventRead = columnsOf<GateEventRead>()

// FR-OB-52a's count cards, in its order.
const CARDS: [string, string, string?][] = [
  ['critical', 'Critical issues'], ['warnings', 'Warnings'], ['failed_inbound', 'Failed inbound', 'failed_inbound'],
  ['failed_outbound', 'Failed outbound', 'failed_outbound'], ['waiting_ack', 'Waiting ACK'],
  ['rejected_ack', 'Rejected ACK', 'rejected_ack'], ['ack_timeout', 'ACK timeout', 'ack_timeout'],
  ['exceptions_944', '944 exceptions', 'exceptions_944'], ['exceptions_945', '945 exceptions', 'exceptions_945'],
  ['transfer_discrepancies', 'Transfer discrepancies', 'transfer_discrepancies'],
]

/** "Test" on a connection's check: reads only (the connection test of Configuration → Connections). */
function TestConnection({ id }: { id: number }) {
  const [result, setResult] = useState<ConnectionTest | null>(null)
  const m = useMutation({ mutationFn: () => testConnection(id), onSuccess: setResult })
  const ok = result && !result.error && result.folders.every((f) => f.ok) && (result.host_key?.matches ?? true)
  return (
    <span className="flex flex-col gap-1">
      <ActionButton size="sm" variant="secondary" permission="sales.config" onAction={() => m.mutateAsync().catch(() => {})}>
        Test
      </ActionButton>
      {result && (
        <span role="status" className={ok ? 'text-success-700' : 'text-danger-600'}>
          {ok ? 'reachable' : (result.error ?? result.folders.find((f) => !f.ok)?.error ?? 'host key does not match the pinned one')}
        </span>
      )}
      <ErrorMessage error={m.error} />
    </span>
  )
}

/** Operations → Health (FR-OB-52a): the checks (PASSED / WARNING / FAILED,
 *  the worst decides), the count cards, the switches, and the go-live gate. */
export default function HealthPage() {
  const navigate = useNavigate()
  const openRecord = useOpenRecord()
  const openList = useOpenList()
  const q = useQuery({ queryKey: ['sales', 'health'], queryFn: fetchHealth, refetchInterval: 60_000 })
  const refresh = useRefresh(q, { queryKey: ['sales', 'health'], compare: () => ({ text: null }) })
  if (q.isLoading) return <Skeleton className="h-60 w-full" />
  if (q.error || !q.data) return <ErrorView error={q.error} onRetry={() => { void refresh.refresh() }} />
  const h: HealthRead = q.data
  const byKey = Object.fromEntries(h.checks.map((c) => [c.key, c]))
  const open = (c: CheckRead) => {
    if (!c.link) return
    if (c.key.startsWith('connection:')) openRecord('/config/connections', Number(c.key.split(':')[1]))
    else openList(c.link, c.label)
  }
  const columns: ColumnDef<CheckRead, unknown>[] = [
    { accessorKey: 'result', header: 'Result', cell: ({ row }) => <HealthBadge result={row.original.result} /> },
    colCheckRead.text('label', 'Check'),
    colCheckRead.text('detail', 'Detail'),
    { id: 'open', header: '', cell: ({ row: { original: c } }) => (
      <span className="flex gap-2">
        {c.link && <Button size="sm" variant="secondary" onClick={() => open(c)}>Open</Button>}
        {c.key.startsWith('connection:') && <TestConnection id={Number(c.key.split(':')[1])} />}
      </span>
    ) },
  ]
  const gateColumns: ColumnDef<GateEventRead, unknown>[] = [
    { accessorKey: 'ok', header: 'Ready', cell: ({ row }) => <Badge variant={row.original.ok ? 'success' : 'danger'}>{row.original.ok ? 'Ready' : 'Not ready'}</Badge> },
    colGateEventRead.text('label', 'Event'),
    colGateEventRead.text('recipients', 'Recipients'),
    { accessorKey: 'tested_at', header: 'Last test', cell: ({ row }) => (row.original.tested_at ? formatDateTime(row.original.tested_at) : '—') },
    { accessorKey: 'problem', header: 'Missing', cell: ({ row }) => row.original.problem ?? '' },
  ]
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Health" description="Checks of the settings, connections, scheduler, files, ACKs and sends. Each one opens the records behind it." />
      <div className="flex items-center gap-3 text-sm">
        <HealthBadge result={h.result} /><span>checked {formatDateTime(h.checked_at)}</span>
      </div>
      <section aria-label="Counts" className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {CARDS.map(([key, label, check]) => {
          const n = h.cards[key] ?? 0
          const c = check ? byKey[check] : undefined
          return <StatCard key={key} label={label} count={n} tone={key === 'critical' && n ? 'danger' : n && key !== 'waiting_ack' ? 'warning' : 'neutral'}
            onOpen={c?.link ? () => open(c) : undefined} />
        })}
      </section>
      <section aria-label="Switches">
        <DetailList items={[
          ['Shadow run', h.shadow_mode ? 'On — nothing is sent, Zoho is live' : 'Off'],
          ['Partner writes (SALES_PARTNER_WRITES_ENABLED)', h.partner_writes_enabled ? 'On' : 'Off — files and Shopify writes are refused'],
          ['External email (SALES_EXTERNAL_EMAIL_ENABLED)', h.external_email_enabled ? 'On' : 'Off — emails are refused and logged'],
          ['Automatic runs (SALES_SCHEDULER_ENABLED)', !h.scheduler_enabled ? 'Not available on this server' : h.scheduler_paused ? 'Off' : 'On'],
        ]} />
      </section>
      <SchedulerSection health={h} />
      <Section title="Checks" plain level={2}>
        <LocalTable<CheckRead> ariaLabel="Health checks" columns={columns} rows={h.checks} rowKey={(c) => c.key} />
      </Section>
      <Section label="Go-live gate" plain level={2}
        title={<>Go-live gate {h.gate_ok ? <Badge variant="success">Ready</Badge> : <Badge variant="danger">Not ready</Badge>}</>}>
        <p className="text-sm text-neutral-600">
          A channel goes LIVE only when each of these events has recipients and a test notification was sent (Configuration → Notifications).
        </p>
        <LocalTable<GateEventRead> ariaLabel="Go-live gate" columns={gateColumns} rows={h.gate} rowKey={(g) => g.event_type} />
        <span><Button size="sm" variant="secondary" onClick={() => navigate('/config/notifications')}>Open the notification rules</Button></span>
      </Section>
    </div>
  )
}
