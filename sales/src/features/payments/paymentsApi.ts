import { api } from '@/lib/api'
import { createResourceClient } from '@/platform-ui'

/** One Shopify payment or refund transaction (or a paid order with none), read-only (sales-api /payments). */
export interface PaymentRowRead {
  id: number
  payment_row_id: number | null
  order_id: number
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
  /** The processor's own transaction id, where Shopify gives it (old Authorize.Net gateway, PayPal). */
  gateway_ref: string | null
  shopify_payment_id: string | null
  order_source: string
  financial_status: string | null
  order_cancelled: boolean
  /** Why Finance should look at it (the Exceptions view). */
  issue: string | null
  discount: string | null
  shipping: string | null
  order_total: string | null
  test: boolean
}

export const paymentsClient = createResourceClient<PaymentRowRead, never>(api, '/payments')
export const downloadPayments = (path: string) => api.download(path, 'payments.csv')
