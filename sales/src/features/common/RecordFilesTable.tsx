import { LocalTable, StatusCell, columnsOf, useFileViewer, useNotices, type ColumnDef } from '@/platform-ui'
import { downloadRecordFile, recordFile, type RecordDocument } from './recordFiles'

const col = columnsOf<RecordDocument>()

/** The files a record received and sent (EDI, JSON, emails): each viewed or downloaded; the sent ones with their
 *  file status and 997 apart (Sent is not Accepted, FR-OB-22). Orders and transfers. */
export function RecordFilesTable({ documents, empty }: { documents: RecordDocument[]; empty: string }) {
  const notices = useNotices()
  const { view, viewer } = useFileViewer()
  const failed = (e: unknown) => notices.notify({ tone: 'error', text: `Download failed: ${e instanceof Error ? e.message : String(e)}` })
  const columns: ColumnDef<RecordDocument, unknown>[] = [
    col.datetime('created_at', 'Created'),
    { accessorKey: 'kind', header: 'Kind', cell: ({ row: { original: d } }) => <span className="whitespace-nowrap">{d.kind}{d.direction === 'OUT' ? ' (sent)' : ''}</span> },
    col.file('remote_name', 'File', { label: (d) => `Download ${d.remote_name ?? d.kind}`,
      onDownload: (d) => { downloadRecordFile(d).catch(failed) }, onView: (d) => view(recordFile(d)) }),
    { accessorKey: 'st02', header: 'Control', cell: ({ row }) => row.original.st02 ?? '', meta: { hidden: true } },
    { accessorKey: 'file_status', header: 'File status', cell: ({ row }) => (row.original.file_status ? <StatusCell value={row.original.file_status} /> : '') },
    { accessorKey: 'ack_status', header: '997', cell: ({ row: { original: d } }) => (
      d.direction !== 'OUT' ? '' : d.file_status === 'Sent' || d.ack_status !== 'Waiting' ? <StatusCell value={d.ack_status} /> : '—') },
    col.datetime('sent_at', 'Sent'),
    { id: 'note', header: 'Note', cell: ({ row }) => <span className="break-words text-xs">{row.original.error ?? row.original.ack_detail ?? ''}</span> },
  ]
  const files = documents.filter((d) => d.kind !== 'ATTACHMENT')
  return (
    <>
      {files.length
        ? <LocalTable<RecordDocument> ariaLabel="Documents" columns={columns} rows={files} rowKey={(d) => d.id} />
        : <p className="text-sm text-neutral-500">{empty}</p>}
      {viewer}
    </>
  )
}
