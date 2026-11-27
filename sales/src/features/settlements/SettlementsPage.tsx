import { useMemo } from 'react'
import { ListPage, RecordSheet, StatusCell, ViewFileButton, formatDateTime, formatMoney, useOpenRecord } from '@/platform-ui'
import { EMPTY_TEXT } from '@/features/help/pageHelp'
import { paymentsClient, type PaymentRowRead } from '@/features/payments/paymentsApi'
import { hiddenPaymentColumns, paymentColumns } from '@/features/payments/paymentsColumns'
import { settlementPdf, settlementsClient, settlementsReportPdf, type SettlementRowRead } from './settlementsApi'
import { hiddenSettlementColumns, settlementColumns } from './settlementsColumns'

/** One deposit: its figures, and the payments in it (the Payments list, kept to this batch). */
function SettlementDetail({ s }: { s: SettlementRowRead }) {
  const openRecord = useOpenRecord()
  const columns = useMemo(() => paymentColumns((id) => { openRecord('/orders', id) }), [openRecord])
  const money = (v: string | null) => (v == null ? '—' : formatMoney(v, s.currency))
  return (
    <RecordSheet
      title={`Settlement ${s.id}`}
      subtitle={s.transfer_amount != null ? `${s.gateway} · transfer ${s.batch_id} · ${formatDateTime(s.settled_at)}`
        : s.state === 'In PayPal balance' ? `${s.gateway} · in the balance, not transferred yet · last ${formatDateTime(s.settled_at)}`
          : `${s.gateway} · Batch ID ${s.batch_id} · settled ${formatDateTime(s.settled_at)}`}
      badges={<StatusCell value={s.issue} />}
      actions={<ViewFileButton source={settlementPdf(s)}>Settlement statement (PDF)</ViewFileButton>}
      facts={[
        ...(s.fee_amount != null ? [{ label: 'Fees', value: money(s.fee_amount) }] : []),
        { label: s.fee_amount != null ? 'Net after fees' : 'Net', value: money(s.net_amount) },
        ...(s.transfer_amount != null ? [
          { label: 'Transferred to the bank', value: money(s.transfer_amount) },
          { label: 'Carried balance', value: money(s.carried_amount), tone: Number(s.carried_amount) === 0 ? 'neutral' as const : 'warning' as const,
            help: 'term:Carried balance' as const },
        ] : []),
        ...(s.notes ? [{ label: 'Other events', value: s.notes }] : []),
        { label: 'Matched in Shopify', value: money(s.matched_net) },
        { label: 'Difference', value: money(s.difference), tone: Number(s.difference) === 0 ? 'neutral' : 'danger' },
        { label: 'Transactions', value: s.transaction_count },
        { label: 'Unmatched', value: s.unmatched_count, tone: s.unmatched_count ? 'danger' : 'neutral' },
        { label: 'Declined / voided', value: `${s.declined_count} / ${s.voided_count}` },
        { label: 'Chargebacks / returns', value: `${money(s.chargeback_amount)} / ${money(s.returned_amount)}`,
          tone: s.chargeback_count + s.returned_count + s.other_count ? 'danger' : 'neutral' },
      ]}
      tabs={[
        { id: 'payments', label: 'Payments', render: () => (
          <ListPage<PaymentRowRead> resource={paymentsClient} columns={columns} defaultHidden={hiddenPaymentColumns}
            fixed={[{ field: 'settlement_id', op: 'eq', value: String(s.id), label: `Settlement ${s.id}` }]} searchable={false}
            emptyTitle="No settled transactions read for this batch" />
        ) },
      ]}
    />
  )
}

/** Invoicing → Settlements (Finance, user 2026-11-17): the deposits, one list; by month and what needs a
 *  look are views of it. A batch opens with its payments. */
export default function SettlementsPage() {
  const columns = useMemo(() => settlementColumns(), [])
  return (
    <ListPage<SettlementRowRead>
      resource={settlementsClient} title="Settlements" help="page:settlements"
      
      columns={columns} defaultHidden={hiddenSettlementColumns} emptyTitle="No settlements yet"
      emptyText={EMPTY_TEXT.settlements} renderDetail={(s) => <SettlementDetail s={s} />}
      toolbar={({ spec }) => <ViewFileButton source={settlementsReportPdf(spec)}>Report (PDF)</ViewFileButton>}
    />
  )
}
