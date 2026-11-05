import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Button, DateTimeCell, ErrorView, LocalTable, PageHeader, Section, Skeleton, StatCard, StatusCell, requestView, type ColumnDef,
  useRefresh, Hint, Muted,
} from '@/platform-ui'
import { fetchEvents } from '@/features/configuration/notifications/notificationsApi'
import { fetchDashboard, fetchHealth, type DashboardRead } from './healthApi'
import { HealthBadge } from './HealthBadge'
import { openList } from './openList'

type RunRow = DashboardRead['runs'][number]
const runColumns: ColumnDef<RunRow, unknown>[] = [
  { accessorKey: 'id', header: 'Run' },
  { accessorKey: 'trigger', header: 'Trigger' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusCell value={row.original.status} /> },
  { accessorKey: 'started_at', header: 'Started', cell: ({ row }) => <DateTimeCell value={row.original.started_at} /> },
  { accessorKey: 'error', header: 'Error', cell: ({ row }) => row.original.error ?? '' },
]

/** Sales → Home (FR-OB-52 / 52b): the order cards (each opens its list), what
 *  needs a person, the last runs and the health. Every number is the total of
 *  the list it opens. */
export function HomeDashboard() {
  const navigate = useNavigate()
  const dash = useQuery({ queryKey: ['sales', 'dashboard'], queryFn: fetchDashboard, refetchInterval: 60_000 })
  const health = useQuery({ queryKey: ['sales', 'health'], queryFn: fetchHealth, refetchInterval: 60_000 })
  const refresh = useRefresh(dash, { queryKey: ['sales', 'dashboard'], compare: () => ({ text: null }) })
  const events = useQuery({ queryKey: ['sales', 'notification-events'], queryFn: fetchEvents, staleTime: 300_000 })
  const label = (type: string) => events.data?.find((e) => `sales_${e.key}` === type)?.label
    ?? (type === 'sales_test' ? 'Test notification' : type)
  const go = (page: string) => navigate(page)

  if (dash.isLoading) return <Skeleton className="h-60 w-full" />
  if (dash.error || !dash.data) return <ErrorView error={dash.error} onRetry={() => { void refresh.refresh() }} />
  const d = dash.data
  const h = health.data
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Sales" description="Orders by state, what needs a person, the last runs and the health of the module." />
      <section aria-label="Orders" className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {d.cards.map((c) => (
          <StatCard key={c.key} label={c.label} count={c.count} tone={c.key === 'failed_files' && c.count ? 'danger' : 'neutral'}
            onOpen={() => go(openList(c.link, c.label))}>
            {c.parts.length > 0 && (
              <Hint>{c.parts.map((p) => `${p.label} ${p.count}`).join(' · ')}</Hint>
            )}
          </StatCard>
        ))}
      </section>
      <Section title="Needs a person" plain level={2}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatCard label="Orders in review" count={d.needs_review} tone={d.needs_review ? 'warning' : 'neutral'}
            onOpen={() => { requestView('orders', 'needs_review'); go('/orders') }} />
          <StatCard label="Waiting for delivery confirmation" count={d.waiting_delivery} onOpen={() => go('/deliveries')} />
          {d.open_tasks.map((t) => (
            <StatCard key={t.type} label={label(t.type)} count={t.count} tone="warning">
              <Hint>open in the Task Inbox</Hint>
            </StatCard>
          ))}
        </div>
        {d.open_tasks.length === 0 && <Muted as="p" className="text-sm">No open Sales task.</Muted>}
      </Section>
      <Section title="Health" plain level={2}>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          {h ? (
            <>
              <HealthBadge result={h.result} />
              <span>{h.cards.critical ?? 0} critical, {h.cards.warnings ?? 0} warnings</span>
            </>
          ) : <Skeleton className="h-5 w-40" />}
          <Button size="sm" variant="ghost" onClick={() => go('/health')}>Open the health page</Button>
        </div>
      </Section>
      <Section title="Last runs" plain level={2}>
        <LocalTable<RunRow> ariaLabel="Last runs" columns={runColumns} rows={d.runs} rowKey={(r) => r.id} emptyTitle="No run yet" />
      </Section>
    </div>
  )
}
