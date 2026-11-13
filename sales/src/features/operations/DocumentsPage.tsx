import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, ConfirmAction, DetailList, ListPage, LocalTable, RecordSheet, RefsCell, Section, StatusCell, formatDateTime, queryKeys,
  useOpenRecord, type ColumnDef, type RecordRef, Muted, ViewFileButton, columnsOf,
} from '@/platform-ui'
import { documentFile, documentsClient, downloadOriginal, ignoreDocument, readAgain, sendAgain, type DocumentRead } from './operationsApi'

const colDocumentRead = columnsOf<DocumentRead>()

const status = (d: DocumentRead) => d.processing_status ?? d.file_status

/** Opening an order or a transfer from a document: its list, its sheet (the document's closes). */
function useOpenRef() {
  const openRecord = useOpenRecord()
  return useMemo(() => ({
    order: (r: RecordRef) => { openRecord('/orders', r.id) },
    transfer: (r: RecordRef) => { openRecord('/transfers', r.id) },
  }), [openRecord])
}

/** Orders first, then a transfer; none: why. */
const belongsTo = (d: DocumentRead) => (d.orders_total || !d.transfers.length ? d.orders : d.transfers)

function useColumns(): ColumnDef<DocumentRead, unknown>[] {
  const open = useOpenRef()
  return useMemo(() => [
    colDocumentRead.datetime('created_at', 'Received'),
    colDocumentRead.text('remote_name', 'File'),
    colDocumentRead.text('kind', 'Kind'),
    { id: 'status', header: 'Status', cell: ({ row }) => <StatusCell value={status(row.original)} /> },
    colDocumentRead.refs('orders', 'Orders', {
      refs: belongsTo, total: (d) => (d.orders_total || d.transfers.length), noun: (d) => (d.orders_total || !d.transfers.length ? 'order' : 'transfer'),
      onOpen: (r, d) => (d.orders_total ? open.order(r) : open.transfer(r)), empty: (d) => d.no_order_reason }),
    colDocumentRead.text('size', 'Bytes'),
    { accessorKey: 'error', header: 'Note', cell: ({ row }) => <Muted className="line-clamp-1">{row.original.error ?? ''}</Muted> },
  ], [open])
}

/** The record sheet's tab: every order (and transfer) of the document, each opening its record. */
function BelongsTo({ d }: { d: DocumentRead }) {
  const open = useOpenRef()
  const cols: ColumnDef<RecordRef, unknown>[] = useMemo(() => [
    { id: 'ref', header: 'Order', cell: ({ row }) => <RefsCell refs={[row.original]} onOpen={open.order} noun="order" /> },
  ], [open])
  return (
    <>
      {d.transfers.length > 0 && (
        <Section title="Transfer"><RefsCell refs={d.transfers} onOpen={open.transfer} noun="transfer" max={10} /></Section>
      )}
      <Section title={`Orders (${d.orders_total})`}>
        <LocalTable<RecordRef> ariaLabel="Orders of this document" columns={cols} rows={d.orders} rowKey={(r) => r.id} />
        {d.orders_total > d.orders.length && (
          <Muted as="p">The first {d.orders.length} of {d.orders_total}: search Documents by an order's ref to find this file.</Muted>
        )}
      </Section>
    </>
  )
}

const RETRY = 'sales.file.retry'
const RESENDABLE = ['940_EDI', '943_EDI', '810_JSON', 'INVOICE_EMAIL']
const ERRORS = {
  'document.not_failed': 'Only a received file that failed can be read again or ignored.',
  'document.not_resendable': 'This kind of file is not sent again from here.',
  'send940.live': 'A 940 for this order is live: replace it from the order.',
  'auth.forbidden': 'You do not have permission for this.',
}

function Detail({ d }: { d: DocumentRead }) {
  const st = status(d)
  const qc = useQueryClient()
  const [ignoring, setIgnoring] = useState(false)
  const refresh = () => qc.invalidateQueries({ queryKey: queryKeys.list(documentsClient) })
  const stuck = d.direction === 'IN' && (d.processing_status === 'Failed' || (d.processing_status === 'Pending' && d.kind === 'OTHER'))
  return (
    <>
    <ConfirmAction open={ignoring} onOpenChange={setIgnoring} title="Ignore this file?" confirmLabel="Ignore"
      description="It stays stored and is never read; its task is closed." reason={{ label: 'Why' }} errorMessages={ERRORS}
      onConfirm={async (why) => { await ignoreDocument(d.id, why ?? ''); await refresh() }} />
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
        { label: 'Orders', value: d.orders_total || (d.transfers.length ? d.transfers.map((t) => t.label).join(', ') : 'None'),
          tone: d.orders_total || d.transfers.length ? 'neutral' : 'warning' },
      ]}
      actions={d.has_content ? (
        <>
          <ViewFileButton source={documentFile(d.id, d.remote_name ?? `document-${d.id}`, `${d.kind}: ${d.remote_name ?? d.id}`)}>View</ViewFileButton>
          <ActionButton size="sm" variant="secondary" onAction={() => downloadOriginal(d)}>Download original</ActionButton>
          {stuck && <ActionButton size="sm" permission={RETRY} errorMessages={ERRORS} done="Read again: the next pass reads it"
            onAction={async () => { await readAgain(d.id); await refresh() }}>Read again</ActionButton>}
          {stuck && <ActionButton size="sm" variant="secondary" permission={RETRY} onAction={() => setIgnoring(true)}>Ignore</ActionButton>}
          {d.direction === 'OUT' && RESENDABLE.includes(d.kind) && <ActionButton size="sm" variant="secondary" permission={RETRY}
            errorMessages={ERRORS} done={(r) => (r as { result: string }).result} onAction={async () => { const r = await sendAgain(d.id); await refresh(); return r }}>
            Send again</ActionButton>}
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
        ...(d.no_order_reason ? [['No order', d.no_order_reason] as [string, string]] : []),
      ]} />
      {!d.has_content && <Muted as="p">This record keeps no bytes of its own (see the note).</Muted>}
        </Section>
      ) }, ...(d.orders_total || d.transfers.length ? [{
        id: 'orders', label: 'Orders', count: d.orders_total || d.transfers.length, render: () => <BelongsTo d={d} />,
      }] : []), ...(d.shipments_without_order > 0 ? [{
        id: 'no-order', label: 'No order', tone: 'warning' as const, render: () => (
          <Muted as="p">{d.shipments_without_order} shipment(s) in this 945 match no order in Sales. The rows are kept with the file;
            find the order (its number in the 945) or tell the warehouse.</Muted>),
      }] : [])]}
    />
    </>
  )
}

/** Operations → Documents: every file read or written, its original bytes kept (FR-OB-05). */
export default function DocumentsPage() {
  const columns = useColumns()
  return (
    <ListPage<DocumentRead>
      resource={documentsClient}
      title="Documents"
      description="Every file read from or sent to a partner, with its original bytes and the orders it belongs to. Search by an order's ref to find its files. Nothing is ever deleted."
      columns={columns}
      emptyTitle="No documents yet"
      renderDetail={(d) => <Detail d={d} />}
    />
  )
}
