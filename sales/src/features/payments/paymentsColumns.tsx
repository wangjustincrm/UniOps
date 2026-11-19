import { columnsOf, withHelp, type ColumnDef } from '@/platform-ui'

type Tone = 'success' | 'warning' | 'danger'
import type { PaymentRowRead } from './paymentsApi'

const col = columnsOf<PaymentRowRead>()

/** Shown where a view names them (or from the column menu). */
export const hiddenPaymentColumns = ['processed_week', 'processed_month', 'gateway', 'shopify_payment_id', 'discount', 'shipping',
  'order_total', 'order_cancelled', 'test', 'gateway_status', 'card_type', 'settled_at', 'voided', 'settlement_ref']

const issueTone = (v: string): Tone => (v === 'Pending over 7 days' || v === 'Waiting for the gateway' ? 'warning' : 'danger')
const matchTone = (v: string): Tone => (v.endsWith('(probable)') ? 'warning' : 'success')
const statusTone = (v: string): Tone => (v === 'success' ? 'success' : v === 'pending' ? 'warning' : 'danger')

/** The Payments list's columns; `openOrder` opens the payment's order. */
export function paymentColumns(openOrder: (orderId: number) => void): ColumnDef<PaymentRowRead, unknown>[] {
  return [
    col.datetime('processed_at', 'Processed'),
    col.record('order_ref', 'Order ref', { id: (r) => r.order_id, onOpen: (id) => openOrder(id) }),
    col.text('customer', 'Customer'),
    col.text('method', 'Method'),
    col.text('kind', 'Kind'),
    col.status('status', 'Status', { tone: statusTone }),
    col.money('amount', 'Amount', { currency: 'currency' }),
    withHelp(col.money('sales_amount', 'Sales', { currency: 'currency' }), 'term:Sales, Refunds, Net'),
    withHelp(col.money('refund_amount', 'Refunds', { currency: 'currency' }), 'term:Sales, Refunds, Net'),
    withHelp(col.money('net_amount', 'Net', { currency: 'currency' }), 'term:Sales, Refunds, Net'),
    col.money('gateway_fee', 'Gateway fee', { currency: 'currency' }),
    col.money('net_after_fee', 'Net after fee', { currency: 'currency' }),
    withHelp(col.reference('gateway_ref', 'Gateway transaction ID'), 'term:Gateway transaction ID'),
    col.text('order_source', 'Order source'),
    col.text('financial_status', 'Order financial status'),
    col.status('issue', 'Issue', { tone: issueTone }),
    withHelp(col.date('settled_date', 'Settlement date'), 'term:Settlement'),
    col.reference('settlement_id', 'Settlement no.'),
    col.reference('settlement_ref', 'Batch / transfer ID'),
    withHelp(col.status('match_method', 'Matched by', { tone: matchTone }), 'term:Matched by'),
    col.datetime('settled_at', 'Settled'),
    col.text('gateway_status', 'Gateway status'),
    col.text('card_type', 'Card type'),
    col.bool('voided', 'Voided in the gateway'),
    col.date('processed_week', 'Week'),
    col.date('processed_month', 'Month'),
    col.text('gateway', 'Shopify gateway'),
    col.reference('shopify_payment_id', 'Shopify payment ID'),
    col.money('discount', 'Order discount', { currency: 'currency' }),
    col.money('shipping', 'Order shipping', { currency: 'currency' }),
    col.money('order_total', 'Order total', { currency: 'currency' }),
    col.bool('order_cancelled', 'Order cancelled'),
    col.bool('test', 'Test'),
  ]
}
