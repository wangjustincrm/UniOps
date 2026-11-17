import { useMemo } from 'react'
import { Callout, ListPage, useOpenRecord } from '@/platform-ui'
import { downloadPayments, paymentsClient, type PaymentRowRead } from './paymentsApi'
import { hiddenPaymentColumns, paymentColumns } from './paymentsColumns'
import { EMPTY_TEXT } from '@/features/help/pageHelp'

const NOTE = (
  <Callout label="Where these come from">
    Every payment and refund transaction Shopify has for an order, read-only. Sales, Refunds and Net count successful
    transactions only. With an Authorize.Net connection, each Authorize.Net payment also shows the batch it was settled
    in and its Authorize.Net transaction ID, read from Authorize.Net and matched to the Shopify payment. A settled
    Authorize.Net transaction that is no Shopify payment is a row of its own (Exceptions).
  </Callout>
)

/** Invoicing → Payments (Finance, user 2026-11-16): one list; each use (a method, by week or month,
 *  the exceptions) is a view of it. */
export default function PaymentsPage() {
  const openRecord = useOpenRecord()
  const columns = useMemo(() => paymentColumns((id) => { openRecord('/orders', id) }), [openRecord])
  return (
    <ListPage<PaymentRowRead>
      resource={paymentsClient} title="Payments"
      description="Shopify payments and refunds, one row per transaction. Use the views for each method, by week or month, and the exceptions."
      columns={columns} defaultHidden={hiddenPaymentColumns} notice={NOTE} emptyTitle="No payments yet" emptyText={EMPTY_TEXT.payments}
      onExport={downloadPayments}
    />
  )
}
