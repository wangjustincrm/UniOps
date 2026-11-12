import { DownloadButton, LocalTable, Section, StatusCell, ViewFileButton, columnsOf, useFileViewer, useNotices, type ColumnDef } from '@/platform-ui'
import { documentFile, downloadDocument } from '@/features/operations/operationsApi'
import { useOrderInvoice } from '../useOrderInvoice'
import { downloadPacket, invoicePdf, packingListPdf, poPdf, type OrderDetail, type OrderDocument } from '../ordersApi'

const colOrderDocument = columnsOf<OrderDocument>()

// Every document of the order in one place (user 2026-11-09): the printable ones as soon as they
// can be made (the PO always, a packing list per applied 945, the invoice once it is built, the
// zip of everything), then the files received and sent — the sources (Shopify page, 850), the 940s
// with their 997 apart (Sent is not Accepted, FR-OB-22), the 945s, the 810 or the invoice email.
export function DocumentsTab({ o }: { o: OrderDetail }) {
  const notices = useNotices()
  const inv = useOrderInvoice(o).data?.items[0]
  const { view, viewer } = useFileViewer()
  const failed = (e: unknown) => notices.notify({ tone: 'error', text: `Download failed: ${e instanceof Error ? e.message : String(e)}` })
  const columns: ColumnDef<OrderDocument, unknown>[] = [
    colOrderDocument.datetime('created_at', 'Created'),
    { accessorKey: 'kind', header: 'Kind', cell: ({ row: { original: d } }) => <span className="whitespace-nowrap">{d.kind}{d.direction === 'OUT' ? ' (sent)' : ''}</span> },
    colOrderDocument.file('remote_name', 'File', { label: (d) => `Download ${d.remote_name ?? d.kind}`,
      onDownload: (d) => { downloadDocument(d.id, d.remote_name ?? `document-${d.id}`).catch(failed) },
      onView: (d) => view(documentFile(d.id, d.remote_name ?? `document-${d.id}`, `${d.kind}${d.direction === 'OUT' ? ' (sent)' : ''}: ${d.remote_name ?? d.id}`)) }),
    { accessorKey: 'st02', header: 'Control', cell: ({ row }) => row.original.st02 ?? '', meta: { hidden: true } },
    { accessorKey: 'file_status', header: 'File status', cell: ({ row }) => (row.original.file_status ? <StatusCell value={row.original.file_status} /> : '') },
    { accessorKey: 'ack_status', header: '997', cell: ({ row: { original: d } }) => (
      d.direction !== 'OUT' ? '' : d.file_status === 'Sent' || d.ack_status !== 'Waiting' ? <StatusCell value={d.ack_status} /> : '—') },
    colOrderDocument.datetime('sent_at', 'Sent'),
    { id: 'note', header: 'Note', cell: ({ row }) => <span className="break-words text-xs">{row.original.error ?? row.original.ack_detail ?? ''}</span> },
  ]
  const shipped = o.shipments.filter((s) => s.applied)
  return (
    <>
      <Section title="Printable">
        <div className="flex flex-wrap gap-2">
          <ViewFileButton source={poPdf(o)}>Purchase order (PDF)</ViewFileButton>
          {shipped.map((s) => (
            <ViewFileButton key={s.id} source={packingListPdf(o.id, s.id, shipped.length > 1 ? String(s.ship_date ?? s.id) : '')}>
              Packing list{shipped.length > 1 ? ` ${s.ship_date ?? s.id}` : ''} (PDF)
            </ViewFileButton>
          ))}
          {inv && (
            <ViewFileButton source={invoicePdf(inv.id, inv.invoice_number)}>
              Invoice {inv.invoice_number} (PDF)
            </ViewFileButton>
          )}
          <DownloadButton onAction={() => downloadPacket(o)}>Everything (zip)</DownloadButton>
        </div>
        <p className="mt-1 text-xs text-neutral-500">
          {shipped.length ? '' : 'The packing list comes with the 945. '}{inv ? '' : 'The invoice comes at delivery confirmation.'}
        </p>
      </Section>
      <Section title="Files received and sent">
        {o.documents.length
          ? <LocalTable<OrderDocument> ariaLabel="Documents" columns={columns} rows={o.documents} rowKey={(d) => d.id} />
          : <p className="text-sm text-neutral-500">No file yet: entered by hand and not sent yet.</p>}
      </Section>
      {viewer}
    </>
  )
}
