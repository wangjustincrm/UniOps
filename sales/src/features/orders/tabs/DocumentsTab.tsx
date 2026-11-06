import { LocalTable, StatusCell, columnsOf, useNotices, type ColumnDef } from '@/platform-ui'
import { downloadDocument } from '@/features/operations/operationsApi'
import type { OrderDocument } from '../ordersApi'

const colOrderDocument = columnsOf<OrderDocument>()

// The order's files: the sources (Shopify page, 850), the 940s with their 997
// apart (Sent is not Accepted, FR-OB-22), each downloadable.
export function DocumentsTab({ docs }: { docs: OrderDocument[] }) {
  const notices = useNotices()
  const failed = (e: unknown) => notices.notify({ tone: 'error', text: `Download failed: ${e instanceof Error ? e.message : String(e)}` })
  const columns: ColumnDef<OrderDocument, unknown>[] = [
    colOrderDocument.datetime('created_at', 'Created'),
    { accessorKey: 'kind', header: 'Kind', cell: ({ row: { original: d } }) => <span className="whitespace-nowrap">{d.kind}{d.direction === 'OUT' ? ' (sent)' : ''}</span> },
    colOrderDocument.file('remote_name', 'File', { label: (d) => `Download ${d.remote_name ?? d.kind}`,
      onDownload: (d) => { downloadDocument(d.id, d.remote_name ?? `document-${d.id}`).catch(failed) } }),
    { accessorKey: 'st02', header: 'Control', cell: ({ row }) => row.original.st02 ?? '' },
    { accessorKey: 'file_status', header: 'File status', cell: ({ row }) => (row.original.file_status ? <StatusCell value={row.original.file_status} /> : '') },
    { accessorKey: 'ack_status', header: '997', cell: ({ row: { original: d } }) => (
      d.direction !== 'OUT' ? '' : d.file_status === 'Sent' || d.ack_status !== 'Waiting' ? <StatusCell value={d.ack_status} /> : '—') },
    colOrderDocument.datetime('sent_at', 'Sent'),
    { id: 'note', header: 'Note', cell: ({ row }) => <span className="break-words text-xs">{row.original.error ?? row.original.ack_detail ?? ''}</span> },
  ]
  return (
    <>
      <LocalTable<OrderDocument> ariaLabel="Documents" columns={columns} rows={docs} rowKey={(d) => d.id} initiallyHidden={['st02']} />
    </>
  )
}
