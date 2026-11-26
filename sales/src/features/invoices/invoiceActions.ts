import { defineActions } from '@/platform-ui'
import { ordersAndInvoicesMoved } from '@/features/common/moved'
import type { InvoiceRead } from './invoicesApi'

// The invoices' record actions (user 2026-11-26, future item 18): Send on the selected invoices. The server (sales-api
// INVOICE_ACTIONS) decides which go; its dry run says, before anything is sent, which will not and why, and how each goes (an
// 810 file to TrueCommerce or an email to the partner's address). Send again stays on the invoice itself (it needs a reason).
export const INVOICE_ACTIONS = defineActions<InvoiceRead>([
  {
    id: 'send', label: 'Send', permission: 'sales.invoice.manage', scope: 'group', placement: ['bulk'],
    eligible: (i) => (i.status === 'Ready' || i.status === 'Failed' ? true
      : i.status === 'Sent' ? 'Sent already: Send again on the invoice, with why' : `Its status is ${i.status ?? 'unknown'}`),
    title: (n) => (n === 1 ? 'Send this invoice' : `Send ${n} invoices`),
    description: "Each goes by its partner's method. The company's copy goes too.",
    will: (n) => `${n} will be sent`, done: (n) => `${n} queued to send`,
    // An invoice's send moves its order: both lists and every open record of them are read again.
    invalidates: ordersAndInvoicesMoved,
  },
])

export const invoiceName = (i: InvoiceRead) => i.invoice_number
