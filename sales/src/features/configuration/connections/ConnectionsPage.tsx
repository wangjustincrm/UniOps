import { factTone } from '@/features/common/statusTones'
import { Server, ShoppingBag } from 'lucide-react'
import { Badge, CrudPage, DateTimeCell, Hint, StatusCell, Tooltip, formatShortMoment, type ColumnDef } from '@/platform-ui'
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
  connectionCreateSchema, connectionEmptyValues, connectionFieldLabels, connectionFields, connectionSettingsFields, connectionToFormValues, connectionUpdateSchema,
} from './connectionSchema'

/** A folder on one line, cut with "…" when long; the whole path on hover. Off: where it would go. */
function FolderHint({ path, off = false }: { path: string; off?: boolean }) {
  return (
    <Tooltip tip={off ? `Would write to ${path} (Write is off)` : path}>
      <span className={`block max-w-[11rem] truncate text-xs ${off ? 'text-neutral-400' : 'text-neutral-600'}`}>{path}</span>
    </Tooltip>
  )
}

// Kind, Host and After read stay in the column chooser (fields of the list's /meta, hidden at first).
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
  // What Sales may do on it, and where (the connection's two ticks; user 2026-11-12).
  { accessorKey: 'can_read', header: 'Read', cell: ({ row: { original: c } }) => (
    <span className="flex flex-col items-start gap-0.5">
      <Badge variant={c.can_read ? 'success' : 'neutral'}>{c.can_read ? 'On' : 'Off'}</Badge>
      {c.can_read && c.kind === 'SFTP' && c.inbound_folder && <FolderHint path={c.inbound_folder} />}
    </span>
  ) },
  { accessorKey: 'can_write', header: 'Write', cell: ({ row: { original: c } }) => (
    <span className="flex flex-col items-start gap-0.5">
      <Badge variant={c.can_write ? 'success' : 'neutral'}>{c.can_write ? 'On' : 'Off'}</Badge>
      {c.kind === 'SFTP' && c.outbound_folder && <FolderHint path={c.outbound_folder} off={!c.can_write} />}
    </span>
  ) },
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
  'connection.go_live_gate': 'Writing can be turned on once every go-live notification has a recipient and was tested: Configuration → Notifications (Health lists what is missing).',
  'connection.folder_is_write_folder': 'The read, archive and error folders must not be the write folder.',
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
      editFields={connectionSettingsFields}
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
        { label: 'Read', value: c.can_read ? 'On' : 'Off', tone: c.can_read ? 'success' : 'neutral',
          hint: c.kind === 'SFTP' && c.can_read ? c.inbound_folder ?? undefined : undefined },
        { label: 'Write', value: c.can_write ? 'On' : 'Off', tone: c.can_write ? 'success' : 'neutral',
          hint: c.kind === 'SFTP' && c.can_write ? c.outbound_folder ?? undefined : undefined },
        { label: 'Schedule', value: c.schedule_paused ? 'Paused' : `Every ${c.schedule_minutes} min`, tone: c.schedule_paused ? 'warning' : 'neutral' },
        { label: 'Last run', value: c.last_run_status ?? '—', tone: factTone(c.last_run_status),
          hint: c.last_run_at ? formatShortMoment(c.last_run_at) : undefined },
        { label: 'Next run', value: <NextRun c={c} compact />, tone: c.overdue ? 'danger' : 'neutral' },
      ]}
      detailTabs={(c, details) => [
        { id: 'overview', label: 'Overview', tone: ['danger', 'warning'].includes(connectionHealth(c).tone) ? connectionHealth(c).tone as 'danger' | 'warning' : undefined,
          render: () => <ConnectionChecklist connection={c} /> },
        { ...details, label: 'Settings' },
        { id: 'schedule', label: 'Schedule', tone: c.last_run_status === 'FAILED' ? 'danger' : undefined,
          render: () => <><SchedulerOffBanner /><ConnectionSchedule connection={c} /></> },
        { id: 'files', label: 'Files', render: () => <ConnectionFiles connection={c} /> },
        historyTab('connections', c.id, c.version, fetchConnectionAudit),
      ]}
    />
  )
}
