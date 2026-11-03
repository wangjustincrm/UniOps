import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Button, DateTimeCell, ErrorView, LocalTable, StatusCell, queryKeys, requestView, type ColumnDef,
} from '@/platform-ui'
import { documentsClient, type DocumentRead } from '@/features/operations/operationsApi'
import type { ConnectionRead } from './connectionsApi'

const columns: ColumnDef<DocumentRead, unknown>[] = [
  { accessorKey: 'created_at', header: 'Received', cell: ({ row }) => <DateTimeCell value={row.original.created_at} /> },
  { accessorKey: 'kind', header: 'Kind' },
  { accessorKey: 'remote_name', header: 'File', cell: ({ row }) => <span className="break-all">{row.original.remote_name ?? '—'}</span> },
  { id: 'status', header: 'Status', cell: ({ row: { original: d } }) => <StatusCell value={d.processing_status ?? d.file_status} /> },
  { accessorKey: 'error', header: 'Problem', cell: ({ row }) => <span className="text-danger-600">{row.original.error ?? ''}</span> },
]

/** The last files this connection received or sent; all of them on the Files page. */
export function ConnectionFiles({ connection: c }: { connection: ConnectionRead }) {
  const navigate = useNavigate()
  const filters = [{ field: 'connection_id', op: 'eq' as const, value: String(c.id) }]
  const q = useQuery({
    queryKey: [...queryKeys.list(documentsClient), 'connection', c.id],
    queryFn: () => documentsClient.list({ page: 1, pageSize: 20, sort: [{ field: 'created_at', desc: true }], filters }),
  })
  if (q.error) return <ErrorView error={q.error} />
  return (
    <div className="flex flex-col gap-2">
      <LocalTable<DocumentRead> ariaLabel="Last files" columns={columns} rows={q.data?.items ?? []} rowKey={(d) => d.id}
        emptyTitle={q.isLoading ? 'Loading…' : 'No file yet'} />
      <span>
        <Button size="sm" variant="secondary" onClick={() => {
          requestView('documents', { name: c.name, state: { filters } })
          navigate('/documents')
        }}>All files of {c.name}{q.data ? ` (${q.data.total})` : ''}</Button>
      </span>
    </div>
  )
}
