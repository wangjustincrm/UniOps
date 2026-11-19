import {
  Button, LocalTable, StatusCell, useOpenView, type ColumnDef, columnsOf, useRecordList, QueryState, Text, Stack,
} from '@/platform-ui'
import { documentsClient, type DocumentRead } from '@/features/operations/operationsApi'
import type { ConnectionRead } from './connectionsApi'

const colDocumentRead = columnsOf<DocumentRead>()

const columns: ColumnDef<DocumentRead, unknown>[] = [
  colDocumentRead.datetime('created_at', 'Received'),
  colDocumentRead.text('kind', 'Kind'),
  { accessorKey: 'remote_name', header: 'File', cell: ({ row }) => <Text wrap="break">{row.original.remote_name ?? '—'}</Text> },
  { id: 'status', header: 'Status', meta: { card: 'badge' }, cell: ({ row: { original: d } }) => <StatusCell value={d.processing_status ?? d.file_status} /> },
  { accessorKey: 'error', header: 'Problem', cell: ({ row }) => <Text tone="danger">{row.original.error ?? ''}</Text> },
]

/** The last files this connection received or sent; all of them on the Files page. */
export function ConnectionFiles({ connection: c }: { connection: ConnectionRead }) {
  const openView = useOpenView()
  const filters = [{ field: 'connection_id', op: 'eq' as const, value: String(c.id) }]
  const q = useRecordList(documentsClient, { page: 1, pageSize: 20, sort: [{ field: 'created_at', desc: true }], filters }, { errors: 'inline' })
  if (q.isError) return <QueryState q={q}>{() => null}</QueryState>
  return (
    <Stack gap={2}>
      <LocalTable<DocumentRead> ariaLabel="Last files" columns={columns} rows={q.data?.items ?? []} rowKey={(d) => d.id}
        emptyTitle={q.isLoading ? 'Loading…' : 'No file yet'} />
      <span>
        <Button size="sm" variant="secondary" onClick={() => {
          openView('/documents', 'documents', { name: c.name, state: { filters } })
        }}>All files of {c.name}{q.data ? ` (${q.data.total})` : ''}</Button>
      </span>
    </Stack>
  )
}
