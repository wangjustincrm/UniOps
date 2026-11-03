import {
  ActionButton, DateTimeCell, DetailList, ListPage, RecordSheet, Section, StatusCell, formatDateTime, type ColumnDef,
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
  const st = status(d)
  return (
    <RecordSheet
      title={d.remote_name ?? `Document ${d.id}`}
      subtitle={[`#${d.id}`, d.kind, d.direction === 'IN' ? 'received' : 'sent', d.system].filter(Boolean).join(' · ')}
      badges={<StatusCell value={st} />}
      facts={[
        { label: 'Kind', value: d.kind },
        { label: 'Status', value: st ?? '—', tone: st === 'Failed' ? 'danger' : 'neutral' },
        { label: 'Bytes', value: d.size ?? '—' },
        { label: 'Received', value: formatDateTime(d.created_at) },
        ...(d.ack_status ? [{ label: '997', value: d.ack_status }] : []),
      ]}
      actions={d.has_content ? (
        <>
          <ActionButton size="sm" variant="secondary" onAction={() => downloadOriginal(d)}>Download original</ActionButton>
        </>
      ) : undefined}
      tabs={[{ id: 'overview', label: 'Overview', tone: d.error ? 'warning' : undefined, render: () => (
        <Section title="File">
      <DetailList items={[
        ['Direction / origin', `${d.direction} · ${d.origin}`], ['System', d.system], ['Kind', d.kind],
        ['Status', <StatusCell key="s" value={status(d)} />], ['File', <span key="f" className="break-all">{d.remote_name}</span>],
        ['Bytes', d.size], ['Remote modified', formatDateTime(d.remote_mtime)],
        ['Received', `${formatDateTime(d.created_at)} · ${d.created_by_name}`], ['SHA-256', <span key="h" className="break-all">{d.sha256}</span>],
        ['ISA13 / GS06 / ST02', [d.isa13, d.gs06, d.st02].filter(Boolean).join(' / ') || null], ['Note', d.error],
      ]} />
      {!d.has_content && <p className="text-neutral-500">This record keeps no bytes of its own (see the note).</p>}
        </Section>
      ) }]}
    />
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
      renderDetail={(d) => <Detail d={d} />}
    />
  )
}
