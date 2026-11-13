import { DownloadButton, Section, ViewFileButton } from '@/platform-ui'
import { Attachments } from '@/features/common/Attachments'
import { RecordFilesTable } from '@/features/common/RecordFilesTable'
import { useOrderInvoice } from '../useOrderInvoice'
import { downloadPacket, invoicePdf, packingListPdf, poPdf, type OrderDetail } from '../ordersApi'

// Every document of the order in one place (user 2026-11-09): the printable ones as soon as they
// can be made (the PO always, a packing list per applied 945, the invoice once it is built, the
// zip of everything), the files received and sent — the sources (Shopify page, 850), the 940s
// with their 997 apart (Sent is not Accepted, FR-OB-22), the 945s, the 810 or the invoice email —
// and the files people attached (2026-11-13).
export function DocumentsTab({ o }: { o: OrderDetail }) {
  const inv = useOrderInvoice(o).data?.items[0]
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
      <Attachments type="order" id={o.id} />
      <Section title="Files received and sent">
        <RecordFilesTable documents={o.documents} empty="No file yet: entered by hand and not sent yet." />
      </Section>
    </>
  )
}
