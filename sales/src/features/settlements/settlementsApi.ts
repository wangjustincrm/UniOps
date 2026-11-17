import { api } from '@/lib/api'
import { createResourceClient } from '@/platform-ui'

/** One deposit a payment gateway paid out — an Authorize.Net settled batch — beside the Shopify
 *  payments matched to it (sales-api /settlements; read-only). */
export interface SettlementRowRead {
  id: number
  connection_id: number
  gateway: string
  batch_id: string
  settled_at: string
  /** Its day, Monday of its week, first of its month (Toronto): what the list groups by. */
  settled_date: string
  settled_week: string
  settled_month: string
  state: string | null
  payment_method: string | null
  currency: string
  /** As the gateway counts them (its batch statistics). */
  sales_amount: string
  sales_count: number
  refund_amount: string
  refund_count: number
  /** Where the gateway reports fees (Authorize.Net does not: it bills them monthly). */
  fee_amount: string | null
  net_amount: string
  declined_count: number
  voided_count: number
  transaction_count: number
  /** The Shopify payments matched to its settled transactions (sales − refunds), and Net − that. */
  matched_net: string
  difference: string
  unmatched_count: number
  probable_count: number
  transactions_read_at: string | null
  /** Why Finance should look at it. */
  issue: string | null
}

export const settlementsClient = createResourceClient<SettlementRowRead, never>(api, '/settlements')
export const downloadSettlements = (path: string) => api.download(path, 'settlements.csv')
