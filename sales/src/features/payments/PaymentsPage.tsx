import { useMemo } from 'react'
import { Callout, ListPage, useOpenRecord } from '@/platform-ui'
import { downloadPayments, paymentsClient, type PaymentRowRead } from './paymentsApi'
import { hiddenPaymentColumns, paymentColumns } from './paymentsColumns'

const NOTE = (
  <Callout label="Where these come from">
    Every payment and refund transaction Shopify has for an order, read-only. Sales, Refunds and Net count successful
    transactions only. Authorize.Net payments since Sep 18, 2025 (Shopify's Authorize.Net app) carry no Authorize.Net
    transaction ID in Shopify: their reference is the Shopify payment ID (a hidden column).
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
      columns={columns} defaultHidden={hiddenPaymentColumns} notice={NOTE} emptyTitle="No payments"
      onExport={downloadPayments}
    />
  )
}
