import { NEEDS_REVIEW } from '@/app/oldAddresses'
import { useNavigate } from 'react-router-dom'
import {
  Button, LocalTable, PageHeader, Section, Skeleton, StatCard, useOpenView, type ColumnDef, useRefresh, Hint, Muted, columnsOf, useLookup, QueryState, Stack, Cluster, Columns, Text,
} from '@/platform-ui'
import { fetchEvents } from '@/features/configuration/notifications/notificationsApi'
import { fetchDashboard, fetchHealth, type DashboardRead } from './healthApi'
import { HealthBadge } from './HealthBadge'
import { useOpenList } from './openList'

const colRunRow = columnsOf<RunRow>()

type RunRow = DashboardRead['runs'][number]
const runColumns: ColumnDef<RunRow, unknown>[] = [
  colRunRow.text('id', 'Run'),
  colRunRow.text('trigger', 'Trigger'),
  colRunRow.status('status', 'Status'),
  colRunRow.datetime('started_at', 'Started'),
  { accessorKey: 'error', header: 'Error', cell: ({ row }) => row.original.error ?? '' },
]

/** Sales → Home (FR-OB-52 / 52b): the order cards (each opens its list), what
 *  needs a person, the last runs and the health. Every number is the total of
 *  the list it opens. */
export function HomeDashboard() {
  const navigate = useNavigate()
  const dash = useLookup(['sales', 'dashboard'], fetchDashboard, { refetchInterval: 60_000, errors: 'inline' })
  const health = useLookup(['sales', 'health'], fetchHealth, { refetchInterval: 60_000 })
  useRefresh(dash, { queryKey: ['sales', 'dashboard'], compare: () => ({ text: null }) })
  const events = useLookup(['sales', 'notification-events'], fetchEvents, { staleTime: 300_000 })
  const label = (type: string) => events.data?.find((e) => `sales_${e.key}` === type)?.label
    ?? (type === 'sales_test' ? 'Test notification' : type)
  const go = (page: string) => navigate(page)
  const openList = useOpenList()
  const openView = useOpenView()

  if (!dash.data) return <QueryState q={dash} skeleton="h-60">{() => null}</QueryState>
  const d = dash.data
  const h = health.data
  return (
    <Stack gap={6}>
      <PageHeader title="Sales" description="Orders by state, what needs a person, the last runs and the health of the module." />
      <Columns as="section" aria-label="Orders" cols={2} md={5}>
        {d.cards.map((c) => (
          <StatCard key={c.key} label={c.label} count={c.count} tone={c.key === 'failed_files' && c.count ? 'danger' : 'neutral'}
            onOpen={() => openList(c.link, c.label)}>
            {c.parts.length > 0 && (
              <Hint>{c.parts.map((p) => `${p.label} ${p.count}`).join(' · ')}</Hint>
            )}
          </StatCard>
        ))}
      </Columns>
      <Section title="Needs a person" plain level={2}>
        <Columns cols={2} md={4}>
          <StatCard label="Orders in review" count={d.needs_review} tone={d.needs_review ? 'warning' : 'neutral'}
            onOpen={() => { openView('/orders', 'orders', NEEDS_REVIEW) }} />
          <StatCard label="Waiting for delivery confirmation" count={d.waiting_delivery} onOpen={() => go('/deliveries')} />
          {d.open_tasks.map((t) => (
            <StatCard key={t.type} label={label(t.type)} count={t.count} tone="warning">
              <Text tone="soft" size="xs">open in the Task Inbox</Text>
            </StatCard>
          ))}
        </Columns>
        {d.open_tasks.length === 0 && <Muted as="p" className="text-sm">No open Sales task.</Muted>}
      </Section>
      <Section title="Health" plain level={2}>
        <Cluster gap={3} align="center" size="sm">
          {h ? (
            <>
              <HealthBadge result={h.result} />
              <span>{h.cards.critical ?? 0} critical, {h.cards.warnings ?? 0} warnings</span>
            </>
          ) : <Skeleton className="h-5 w-40" />}
          <Button size="sm" variant="ghost" onClick={() => go('/health')}>Open the health page</Button>
        </Cluster>
      </Section>
      <Section title="Last runs" plain level={2}>
        <LocalTable<RunRow> ariaLabel="Last runs" columns={runColumns} rows={d.runs} rowKey={(r) => r.id} emptyTitle="No run yet" />
      </Section>
    </Stack>
  )
}
