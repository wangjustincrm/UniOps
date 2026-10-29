import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  ActionButton, Badge, ErrorMessage, QueryTable, Sheet, defaultSpec, type ColumnDef, type QuerySpec,
} from '@/platform-ui'
import { DOCUMENT_KINDS, INBOUND_STATUSES, documentsClient, downloadOriginal, type DocumentRead } from './operationsApi'

const statusVariant: Record<string, 'success' | 'warning' | 'danger' | 'neutral' | 'info'> = {
  Pending: 'info', Processing: 'info', Processed: 'success', Failed: 'danger', Skipped: 'neutral', Duplicate: 'neutral',
}
const status = (d: DocumentRead) => d.processing_status ?? d.file_status ?? '—'
const when = (s: string | null) => (s ? new Date(s).toLocaleString() : '—')

const columns: ColumnDef<DocumentRead, unknown>[] = [
  { accessorKey: 'created_at', header: 'Received', cell: ({ row }) => when(row.original.created_at) },
  { accessorKey: 'remote_name', header: 'File' },
  { accessorKey: 'kind', header: 'Kind' },
  { id: 'status', header: 'Status', cell: ({ row }) => <Badge variant={statusVariant[status(row.original)] ?? 'neutral'}>{status(row.original)}</Badge> },
  { accessorKey: 'size', header: 'Bytes' },
  { accessorKey: 'error', header: 'Note', cell: ({ row }) => <span className="line-clamp-1 text-neutral-500">{row.original.error ?? ''}</span> },
]

function Detail({ d }: { d: DocumentRead }) {
  const [error, setError] = useState<unknown>(null)
  const rows: [string, string | number | null][] = [
    ['Direction / origin', `${d.direction} · ${d.origin}`], ['System', d.system], ['Kind', d.kind],
    ['Status', status(d)], ['File', d.remote_name], ['Bytes', d.size], ['Remote modified', when(d.remote_mtime)],
    ['Received', `${when(d.created_at)} · ${d.created_by_name}`], ['SHA-256', d.sha256],
    ['ISA13 / GS06 / ST02', [d.isa13, d.gs06, d.st02].filter(Boolean).join(' / ') || null], ['Note', d.error],
  ]
  return (
    <div className="flex flex-col gap-4 text-sm">
      <dl className="grid grid-cols-[10rem_1fr] gap-x-3 gap-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="contents"><dt className="text-neutral-500">{k}</dt><dd className="break-all">{v ?? '—'}</dd></div>
        ))}
      </dl>
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
  const [spec, setSpec] = useState<QuerySpec>(defaultSpec())
  const [open, setOpen] = useState<DocumentRead | null>(null)
  const list = useQuery({ queryKey: ['documents', spec], queryFn: () => documentsClient.list(spec), placeholderData: keepPreviousData })
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold text-neutral-900">Documents</h1>
        <p className="text-sm text-neutral-500">Every file read from or sent to a partner, with its original bytes. Nothing is ever deleted.</p>
      </div>
      <QueryTable<DocumentRead>
        columns={columns}
        rows={list.data?.items ?? []}
        total={list.data?.total ?? 0}
        spec={spec}
        onSpecChange={setSpec}
        sortable={['created_at', 'remote_name', 'kind', 'size']}
        searchable
        filters={[
          { field: 'kind', label: 'Kind', options: DOCUMENT_KINDS.map((k) => ({ value: k, label: k })) },
          { field: 'processing_status', label: 'Status', options: INBOUND_STATUSES.map((k) => ({ value: k, label: k })) },
        ]}
        loading={list.isLoading}
        error={list.error}
        onRetry={() => list.refetch()}
        rowKey={(d) => d.id}
        onRowClick={setOpen}
        emptyTitle="No documents yet"
      />
      <Sheet open={open !== null} onOpenChange={(o) => { if (!o) setOpen(null) }} title={open?.remote_name ?? `Document ${open?.id ?? ''}`}>
        {open && <Detail d={open} />}
      </Sheet>
    </div>
  )
}
