import { InvoiceList } from './InvoiceList'

/** Invoicing → Invoices: built at delivery confirmation (FR-OB-31), edited
 *  before or after sending (FR-OB-35), sent by the partner's method (FR-OB-30). */
export default function InvoicesPage() {
  return <InvoiceList help="page:invoices" title="Invoices" />
}
