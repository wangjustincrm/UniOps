import { api } from '@/lib/api'
import { createResourceClient } from '@/platform-ui'

/** One Shopify payment or refund transaction (or a paid order with none), read-only (sales-api /payments). */
export interface PaymentRowRead {
  id: number
  payment_row_id: number | null
  /** null: a settled gateway transaction that is no Shopify payment (its own row). */
  order_id: number | null
  order_ref: string | null
  customer: string | null
  processed_at: string | null
  /** The Monday (Toronto) of its week / the first of its month: what the list groups by. */
  processed_week: string | null
  processed_month: string | null
  /** Authorize.Net (Shopify's old and new gateway as one), PayPal, AlphaPay, Manual. */
  method: string | null
  gateway: string | null
  /** sale · capture · authorization · refund · void; "none": an order paid with no transaction. */
  kind: string
  status: string | null
  amount: string
  currency: string
  /** Successful sales and captures / refunds, and sales − refunds: the list's totals. */
  sales_amount: string
  refund_amount: string
  net_amount: string
  /** The processor's own transaction id: from Shopify (old Authorize.Net gateway, PayPal) or from the gateway once matched. */
  gateway_ref: string | null
  shopify_payment_id: string | null
  order_source: string | null
  financial_status: string | null
  order_cancelled: boolean
  /** Why Finance should look at it (the Exceptions view). */
  issue: string | null
  discount: string | null
  shipping: string | null
  order_total: string | null
  test: boolean
  /** From the gateway's own data, once matched (Authorize.Net). */
  gateway_status: string | null
  card_type: string | null
  /** Transaction ID · Shopify payment ID · Order number · Amount and time (probable). */
  match_method: string | null
  settlement_id: number | null
  /** The deposit it was paid out in: the batch id, its time and its day (Toronto). */
  settlement_ref: string | null
  settled_at: string | null
  settled_date: string | null
}

export const paymentsClient = createResourceClient<PaymentRowRead, never>(api, '/payments')
export const downloadPayments = (path: string) => api.download(path, 'payments.csv')
