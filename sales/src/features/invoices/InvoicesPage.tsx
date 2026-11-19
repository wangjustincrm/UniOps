import { ListPage, formatDateTime, type ColumnDef, columnsOf } from '@/platform-ui'
import { invoicesClient, type InvoiceRead } from './invoicesApi'
import { PartnerCell } from '@/features/common/PartnerCell'
import { EMPTY_TEXT } from '@/features/help/pageHelp'
import { InvoiceSheet } from './InvoiceSheet'

const colInvoiceRead = columnsOf<InvoiceRead>()
const columns: ColumnDef<InvoiceRead, unknown>[] = [
  colInvoiceRead.text('invoice_number', 'Invoice'),
  { accessorKey: 'order_ref', header: 'Order ref', cell: ({ row }) => row.original.order_ref ?? '' },
  colInvoiceRead.text('order_number', 'Internal no.'),
  { accessorKey: 'partner_name', header: 'Partner', cell: ({ row }) => <PartnerCell name={row.original.partner_name} code={row.original.partner_code} /> },
  colInvoiceRead.text('po', 'PO'),
  colInvoiceRead.status('status', 'Status'),
  colInvoiceRead.text('method', 'Method'),
  colInvoiceRead.date('invoice_date', 'Date'),
  colInvoiceRead.money('total', 'Amount', { currency: 'currency' }),
  { accessorKey: 'send_count', header: 'Sent', cell: ({ row }) => row.original.sent_at ? `${row.original.send_count}× · ${formatDateTime(row.original.sent_at)}` : '—' },
]
/** Invoicing → Invoices: built at delivery confirmation (FR-OB-31), edited
 *  before or after sending (FR-OB-35), sent by the partner's method (FR-OB-30). */
export default function InvoicesPage() {
  return (
    <ListPage<InvoiceRead>
      resource={invoicesClient}
      title="Invoices"
      description="One per confirmed order, priced per case at the 850 price. Edit before sending; send by the partner's method (810 or email with PDFs)."
      columns={columns}
      emptyTitle="No invoices yet"
      emptyText={EMPTY_TEXT.invoices}
      help="page:invoices"
      renderDetail={(i) => <InvoiceSheet row={i} />}
    />
  )
}
