import { columnsOf, withHelp, type ColumnDef } from '@/platform-ui'
import type { SettlementRowRead } from './settlementsApi'

type Tone = 'success' | 'warning' | 'danger'
const col = columnsOf<SettlementRowRead>()

/** Shown where a view names them (or from the column menu). */
export const hiddenSettlementColumns = ['batch_id', 'settled_week', 'settled_month', 'settled_at', 'state', 'payment_method', 'fee_amount',
  'sales_count', 'refund_count', 'declined_count', 'voided_count', 'transactions_read_at', 'chargeback_count', 'returned_count',
  'other_count', 'pending_count']

const issueTone = (v: string): Tone => (v === 'Transactions not read yet' || v === 'Still matching' ? 'warning' : 'danger')

/** The Settlements list's columns (a row opens the batch with its payments). */
export function settlementColumns(): ColumnDef<SettlementRowRead, unknown>[] {
  return [
    withHelp(col.date('settled_date', 'Date'), 'term:Settlement'),
    col.text('gateway', 'Gateway'),
    col.reference('id', 'Settlement no.'),
    col.reference('batch_id', 'Batch ID'),
    col.money('sales_amount', 'Sales', { currency: 'currency' }),
    col.money('refund_amount', 'Refunds', { currency: 'currency' }),
    col.money('net_amount', 'Net', { currency: 'currency' }),
    col.money('matched_net', 'Matched in Shopify', { currency: 'currency' }),
    withHelp(col.money('difference', 'Difference', { currency: 'currency' }), 'term:Difference'),
    col.number('transaction_count', 'Transactions'),
    col.number('unmatched_count', 'Unmatched'),
    col.number('probable_count', 'Probable matches'),
    col.money('chargeback_amount', 'Chargebacks', { currency: 'currency' }),
    col.money('returned_amount', 'Returned items', { currency: 'currency' }),
    col.status('issue', 'Issue', { tone: issueTone }),
    col.date('settled_week', 'Week'),
    col.date('settled_month', 'Month'),
    col.datetime('settled_at', 'Settled'),
    col.text('state', 'State'),
    col.text('payment_method', 'Payment method'),
    col.money('fee_amount', 'Fees', { currency: 'currency' }),
    col.number('sales_count', 'Sales (count)'),
    col.number('refund_count', 'Refunds (count)'),
    col.number('declined_count', 'Declined'),
    col.number('voided_count', 'Voided'),
    col.datetime('transactions_read_at', 'Transactions read'),
    col.number('chargeback_count', 'Chargebacks (count)'),
    col.number('returned_count', 'Returned items (count)'),
    col.number('other_count', 'Other transactions'),
    col.number('pending_count', 'Still matching'),
  ]
}
