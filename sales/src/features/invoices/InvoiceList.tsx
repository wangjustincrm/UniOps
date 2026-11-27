import { ListPage, formatDateTime, type ColumnDef, type ListScope, type HelpId, columnsOf } from '@/platform-ui'
import { invoicesClient, type InvoiceRead } from './invoicesApi'
import { PartnerCell } from '@/features/common/PartnerCell'
import { EMPTY_TEXT } from '@/features/help/pageHelp'
import { InvoiceSheet } from './InvoiceSheet'
import { INVOICE_ACTIONS, invoiceName } from './invoiceActions'

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
/** The invoices as a list: the Invoices page, or a record's own (an item's Invoices: its `scope`). */
export function InvoiceList({ scope, title, help }: { scope?: ListScope; title?: string; help?: HelpId }) {
  return (
    <ListPage<InvoiceRead>
      resource={invoicesClient}
      title={title}
      description={title ? "One per confirmed order, priced per case at the 850 price. Edit before sending; send by the partner's method (810 or email with PDFs)." : undefined}
      columns={columns}
      scope={scope}
      fit={scope ? 560 : undefined}
      emptyTitle={scope ? 'No invoice has this item' : 'No invoices yet'}
      emptyText={scope ? undefined : EMPTY_TEXT.invoices}
      help={help}
      renderDetail={(i) => <InvoiceSheet row={i} />}
      actions={INVOICE_ACTIONS}
      recordName={invoiceName}
    />
  )
}
