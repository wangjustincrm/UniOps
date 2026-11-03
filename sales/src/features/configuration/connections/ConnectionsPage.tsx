import { Badge, CrudPage, DateTimeCell, StatusCell, useToast, type ColumnDef } from '@/platform-ui'
import { AuditHistory } from '../AuditHistory'
import { ConnectionRowActions } from './ConnectionRowActions'
import { ConnectionSchedule } from './ConnectionSchedule'
import { ConnectionTools } from './ConnectionTools'
import { EdiSettingsPanel } from './EdiSettingsPanel'
import { ShadowPanel } from './ShadowPanel'
import {
  connectionsClient, describeSchedule, downloadConnections, fetchConnectionAudit,
  type ConnectionCreate, type ConnectionRead, type ConnectionUpdate,
} from './connectionsApi'
import {
  connectionCreateSchema, connectionEmptyValues, connectionFields, connectionToFormValues, connectionUpdateSchema,
} from './connectionSchema'

const columns: ColumnDef<ConnectionRead, unknown>[] = [
  { accessorKey: 'name', header: 'Name' },
  { accessorKey: 'kind', header: 'Kind' },
  { accessorKey: 'host', header: 'Host' },
  { accessorKey: 'mode', header: 'Mode' },
  { accessorKey: 'after_read', header: 'After read' },
  {
    id: 'state', header: 'State',
    cell: ({ row: { original: c } }) => !c.active ? <Badge variant="neutral">Inactive</Badge>
      : c.auth_failed_at ? <Badge variant="danger">Login refused</Badge>
        : c.kind === 'SFTP' && !c.host_key ? <Badge variant="warning">Key not pinned</Badge>
          : !c.secret_present ? <Badge variant="warning">Secret missing</Badge>
            : <Badge variant="success">Ready</Badge>,
  },
  { id: 'schedule', header: 'Schedule', cell: ({ row: { original: c } }) => (
    <span className="flex items-center gap-1">{describeSchedule(c)}{c.schedule_paused && <Badge variant="warning">Paused</Badge>}</span>
  ) },
  { accessorKey: 'last_run_at', header: 'Last run', cell: ({ row: { original: c } }) => !c.last_run_at ? '—' : (
    <span className="flex flex-col">
      <span className="flex items-center gap-1"><DateTimeCell value={c.last_run_at} />{c.last_run_status && <StatusCell value={c.last_run_status} />}</span>
      {c.last_run_summary && <span className="text-xs text-neutral-500">{c.last_run_summary}</span>}
    </span>
  ) },
  { accessorKey: 'next_run_at', header: 'Next run', cell: ({ row }) => <DateTimeCell value={row.original.next_run_at} /> },
]

const errorMessages: Record<string, string> = {
  'connection.name_taken': 'A connection with this name already exists.',
  'entity.stale': 'Someone else changed this connection. Close the form and open it again.',
  'auth.forbidden': 'You do not have permission to change connections.',
}

/** Configuration → Connections (design §3.1, §5.1, §5.3). */
export default function ConnectionsPage() {
  const toast = useToast()
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
      onExport={(path) => {
        downloadConnections(path).catch((e: unknown) =>
          toast({ variant: 'error', title: 'Export failed', description: e instanceof Error ? e.message : String(e) }))
      }}
      csvImport
      errorMessages={errorMessages}
      notice={<div className="flex flex-col gap-3"><ShadowPanel /><EdiSettingsPanel /></div>}
      renderDetail={(c) => (
        <div className="flex flex-col gap-6">
          <ConnectionSchedule connection={c} />
          <ConnectionTools connection={c} />
          <AuditHistory resource="connections" id={c.id} version={c.version} load={fetchConnectionAudit} />
        </div>
      )}
    />
  )
}
