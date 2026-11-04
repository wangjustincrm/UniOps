import { Server, ShoppingBag } from 'lucide-react'
import { Badge, CrudPage, DateTimeCell, Hint, StatusCell, Tooltip, formatDateTime, type ColumnDef } from '@/platform-ui'
import { historyTab } from '../historyTab'
import { ConnectionFiles } from './ConnectionFiles'
import { NextRun, SchedulerOffBanner } from './SchedulerState'
import { ConnectionRowActions } from './ConnectionRowActions'
import { ConnectionChecklist } from './ConnectionChecklist'
import { ConnectionSchedule } from './ConnectionSchedule'
import { ConnectionSummary } from './ConnectionSummary'
import { HealthBadge } from './HealthBadge'
import { RunNowButton } from './RunNowButton'
import { connectionHealth } from './connectionHealth'
import {
  connectionsClient, downloadConnections, fetchConnectionAudit, scheduleLabel,
  type ConnectionCreate, type ConnectionRead, type ConnectionUpdate,
} from './connectionsApi'
import {
  connectionCreateSchema, connectionEmptyValues, connectionFieldLabels, connectionFields, connectionToFormValues, connectionUpdateSchema,
} from './connectionSchema'

// Kind, Host, Mode and After read stay in the column chooser (fields of the list's /meta, hidden at first).
const columns: ColumnDef<ConnectionRead, unknown>[] = [
  { accessorKey: 'name', header: 'Connection', cell: ({ row: { original: c } }) => (
    <span className="flex items-center gap-2">
      <Tooltip tip={c.kind === 'SFTP' ? 'SFTP' : 'Shopify'}>
        <span>{c.kind === 'SFTP' ? <Server aria-label="SFTP" className="h-4 w-4 text-primary-700" /> : <ShoppingBag aria-label="Shopify" className="h-4 w-4 text-primary-700" />}</span>
      </Tooltip>
      <span className="flex min-w-0 flex-col"><span className="font-medium">{c.name}</span>{c.host && <Hint className="break-all">{c.host}</Hint>}</span>
    </span>
  ) },
  { id: 'health', header: 'Health', cell: ({ row }) => <HealthBadge c={row.original} /> },
  { id: 'schedule', header: 'Schedule', cell: ({ row: { original: c } }) => c.schedule_paused
    ? <Badge variant="warning">Paused</Badge> : scheduleLabel(c) },
  { accessorKey: 'last_run_at', header: 'Last run', cell: ({ row: { original: c } }) => !c.last_run_at ? '—' : (
    <span className="flex flex-col">
      <span className="flex items-center gap-1"><DateTimeCell value={c.last_run_at} />{c.last_run_status && <StatusCell value={c.last_run_status} />}</span>
      {c.last_run_summary && <Hint>{c.last_run_summary}</Hint>}
    </span>
  ) },
  { accessorKey: 'next_run_at', header: 'Next run', cell: ({ row }) => <NextRun c={row.original} /> },
]

const errorMessages: Record<string, string> = {
  'connection.name_taken': 'A connection with this name already exists.',
  'entity.stale': 'Someone else changed this connection. Close the form and open it again.',
  'auth.forbidden': 'You do not have permission to change connections.',
}

/** Configuration → Connections (design §3.1, §5.1, §5.3). */
export default function ConnectionsPage() {
  return (
    <CrudPage<ConnectionRead, ConnectionCreate, ConnectionUpdate>
      title="Connections"
      singular="connection"
      description="SFTP and Shopify connections. Passwords stay on the server; only their names are kept here."
      client={connectionsClient}
      columns={columns}
      rowActions={(c) => <ConnectionRowActions connection={c} />}
      writePermission="sales.config"
      createSchema={connectionCreateSchema}
      updateSchema={connectionUpdateSchema}
      fields={connectionFields}
      immutableOnEdit={['name']}
      emptyValues={connectionEmptyValues}
      toFormValues={connectionToFormValues}
      onExport={(path) => downloadConnections(path)}
      csvImport
      errorMessages={errorMessages}
      fieldLabels={connectionFieldLabels}
      notice={<div className="flex flex-col gap-2"><ConnectionSummary /><SchedulerOffBanner /></div>}
      openAfterCreate="overview"
      detailSize="lg"
      recordActions={(c) => <RunNowButton connection={c} />}
      recordTitle={(c) => c.name}
      recordSubtitle={(c) => [c.kind, c.host, c.partner_code].filter(Boolean).join(' · ')}
      recordFacts={(c) => [
        { label: 'Health', value: connectionHealth(c).label, tone: connectionHealth(c).tone },
        { label: 'Mode', value: c.mode === 'LIVE' ? 'Live' : 'Test', tone: c.mode === 'LIVE' ? 'success' : 'neutral' },
        { label: 'Schedule', value: c.schedule_paused ? 'Paused' : `Every ${c.schedule_minutes} min`, tone: c.schedule_paused ? 'warning' : 'neutral' },
        { label: 'Last run', value: c.last_run_status ?? '—', tone: c.last_run_status === 'FAILED' ? 'danger' : c.last_run_status === 'PARTIAL' ? 'warning' : 'neutral',
          hint: c.last_run_at ? formatDateTime(c.last_run_at) : undefined },
        { label: 'Next run', value: <NextRun c={c} />, tone: c.overdue ? 'danger' : 'neutral' },
      ]}
      detailTabs={(c, details) => [
        { id: 'overview', label: 'Overview', tone: ['danger', 'warning'].includes(connectionHealth(c).tone) ? connectionHealth(c).tone as 'danger' | 'warning' : undefined,
          render: () => <ConnectionChecklist connection={c} /> },
        { ...details, label: 'Settings' },
        { id: 'schedule', label: 'Schedule & runs', tone: c.last_run_status === 'FAILED' ? 'danger' : undefined,
          render: () => <><SchedulerOffBanner /><ConnectionSchedule connection={c} /></> },
        { id: 'files', label: 'Files', render: () => <ConnectionFiles connection={c} /> },
        historyTab('connections', c.id, c.version, fetchConnectionAudit),
      ]}
    />
  )
}
