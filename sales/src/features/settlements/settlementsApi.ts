import { api } from '@/lib/api'
import { createResourceClient, type FileSource, type QuerySpec } from '@/platform-ui'

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
  /** From the batch statistics, not in Net: they explain a difference with the bank deposit. */
  chargeback_amount: string
  chargeback_count: number
  returned_amount: string
  returned_count: number
  /** Chargeback, returned-item … transactions in the batch. */
  other_count: number
  /** Settled transactions whose details are not read yet (matching continues next pass). */
  pending_count: number
  transaction_count: number
  /** The Shopify payments matched to its settled transactions (sales − refunds), and Net − that. */
  matched_net: string
  difference: string
  unmatched_count: number
  probable_count: number
  transactions_read_at: string | null
  /** PayPal (a settlement is a transfer to the bank): what went to the bank, that less the period's Net (a part of
   *  the balance, a hold, a deposit from the bank …), and the period's other events in words. */
  transfer_amount: string | null
  carried_amount: string | null
  notes: string | null
  /** Why Finance should look at it. */
  issue: string | null
}

export const settlementsClient = createResourceClient<SettlementRowRead, never>(api, '/settlements')

/** One deposit's settlement statement (PDF), in the viewer (Download / Print there). */
export const settlementPdf = (s: Pick<SettlementRowRead, 'id' | 'batch_id'>): FileSource => ({
  key: `settlement-${s.id}`, title: `Settlement statement · ${s.id}`,
  load: () => api.file(`/settlements/${s.id}/statement.pdf`, `settlement-${s.id}.pdf`) })

/** The list as it is filtered and sorted now, as one PDF report (the CSV export's query), in the viewer. */
export const settlementsReportPdf = (spec: QuerySpec): FileSource => {
  const path = settlementsClient.exportPath(spec, 'report/settlements.pdf')
  return { key: `settlements-report-${path}`, title: 'Settlements report', load: () => api.file(path, 'settlements-report.pdf') }
}
