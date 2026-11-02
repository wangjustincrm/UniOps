import { useState } from 'react'
import {
  ActionButton, DateTimeCell, DetailList, ErrorMessage, ListPage, StatusCell, formatDateTime, type ColumnDef,
} from '@/platform-ui'
import { documentsClient, downloadOriginal, type DocumentRead } from './operationsApi'

const status = (d: DocumentRead) => d.processing_status ?? d.file_status

const columns: ColumnDef<DocumentRead, unknown>[] = [
  { accessorKey: 'created_at', header: 'Received', cell: ({ row }) => <DateTimeCell value={row.original.created_at} /> },
  { accessorKey: 'remote_name', header: 'File' },
  { accessorKey: 'kind', header: 'Kind' },
  { id: 'status', header: 'Status', cell: ({ row }) => <StatusCell value={status(row.original)} /> },
  { accessorKey: 'size', header: 'Bytes' },
  { accessorKey: 'error', header: 'Note', cell: ({ row }) => <span className="line-clamp-1 text-neutral-500">{row.original.error ?? ''}</span> },
]

function Detail({ d }: { d: DocumentRead }) {
  const [error, setError] = useState<unknown>(null)
  return (
    <div className="flex flex-col gap-4 text-sm">
      <DetailList items={[
        ['Direction / origin', `${d.direction} · ${d.origin}`], ['System', d.system], ['Kind', d.kind],
        ['Status', <StatusCell key="s" value={status(d)} />], ['File', <span key="f" className="break-all">{d.remote_name}</span>],
        ['Bytes', d.size], ['Remote modified', formatDateTime(d.remote_mtime)],
        ['Received', `${formatDateTime(d.created_at)} · ${d.created_by_name}`], ['SHA-256', <span key="h" className="break-all">{d.sha256}</span>],
        ['ISA13 / GS06 / ST02', [d.isa13, d.gs06, d.st02].filter(Boolean).join(' / ') || null], ['Note', d.error],
      ]} />
      {d.has_content ? (
        <ActionButton size="sm" variant="secondary" onAction={() => downloadOriginal(d).catch(setError)}>Download original</ActionButton>
      ) : (
        <p className="text-neutral-500">This record keeps no bytes of its own (see the note).</p>
      )}
      <ErrorMessage error={error} />
    </div>
  )
}

/** Operations → Documents: every file read or written, its original bytes kept (FR-OB-05). */
export default function DocumentsPage() {
  return (
    <ListPage<DocumentRead>
      resource={documentsClient}
      title="Documents"
      description="Every file read from or sent to a partner, with its original bytes. Nothing is ever deleted."
      columns={columns}
      emptyTitle="No documents yet"
      detailTitle={(d) => d.remote_name ?? `Document ${d.id}`}
      renderDetail={(d) => <Detail d={d} />}
    />
  )
}
