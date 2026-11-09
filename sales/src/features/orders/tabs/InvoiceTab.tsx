import { useNavigate } from 'react-router-dom'
import { Download } from 'lucide-react'
import {
  Button, DetailList, ErrorMessage, Section, StatusCell, formatDate, formatDateTime, formatMoney, requestOpen, useNotices,
} from '@/platform-ui'
import { downloadDocument } from '@/features/operations/operationsApi'
import { downloadInvoicePdf, type OrderDetail } from '../ordersApi'
import { useOrderInvoice } from '../useOrderInvoice'

/** The order's invoice: status, number, method, total, the file it was sent as; opened on Invoices. */
export function InvoiceTab({ o }: { o: OrderDetail }) {
  const navigate = useNavigate()
  const notices = useNotices()
  const q = useOrderInvoice(o)
  const inv = q.data?.items[0]
  return (
    <Section title="Invoice">
      <DetailList items={[
        ['Invoice status', <StatusCell key="s" value={o.invoice_status} />],
        ['Invoice', inv ? inv.invoice_number : o.has_invoice ? (q.isLoading ? 'Loading…' : '—') : 'Not built yet (built at delivery confirmation)'],
        ['Method', inv?.method ?? null], ['Invoice date', inv ? formatDate(inv.invoice_date) : null],
        ['Total', inv ? formatMoney(inv.total, inv.currency) : null], ['Delivery number', inv?.delivery_number ?? null],
        ['Sent', inv?.sent_at ? `${inv.send_count}× · last ${formatDateTime(inv.sent_at)}` : inv ? 'Not yet' : null],
        ['Problem', inv?.error ?? null],
      ]} />
      {inv && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={() => { requestOpen('/invoices', inv.id); navigate('/invoices') }}>Open invoice {inv.invoice_number}</Button>
          <Button size="sm" variant="secondary" onClick={() => { downloadInvoicePdf(inv.id, inv.invoice_number).catch((e: unknown) =>
            notices.notify({ tone: 'error', text: `Download failed: ${e instanceof Error ? e.message : String(e)}` })) }}>
            <Download className="mr-1 h-3.5 w-3.5" />Invoice PDF
          </Button>
          {inv.document_id != null && (
            <Button size="sm" variant="secondary" onClick={() => { downloadDocument(inv.document_id!, `${inv.invoice_number}-${inv.method}`).catch((e: unknown) =>
              notices.notify({ tone: 'error', text: `Download failed: ${e instanceof Error ? e.message : String(e)}` })) }}>
              <Download className="mr-1 h-3.5 w-3.5" />The file sent ({inv.method === '810_JSON' ? '810' : inv.method})
            </Button>
          )}
        </div>
      )}
      <ErrorMessage error={q.error} />
    </Section>
  )
}
