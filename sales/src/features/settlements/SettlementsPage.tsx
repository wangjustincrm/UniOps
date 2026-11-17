import { useMemo } from 'react'
import { Callout, ListPage, RecordSheet, StatusCell, ViewFileButton, formatDateTime, formatMoney, useOpenRecord } from '@/platform-ui'
import { EMPTY_TEXT } from '@/features/help/pageHelp'
import { paymentsClient, type PaymentRowRead } from '@/features/payments/paymentsApi'
import { hiddenPaymentColumns, paymentColumns } from '@/features/payments/paymentsColumns'
import { downloadSettlements, settlementPdf, settlementsClient, settlementsReportPdf, type SettlementRowRead } from './settlementsApi'
import { hiddenSettlementColumns, settlementColumns } from './settlementsColumns'

const NOTE = (
  <Callout label="Where these come from">
    Each deposit a payment provider paid to the bank, read from the provider (Authorize.Net: one settled batch, about one a
    day). Sales, Refunds and Net are the provider's own figures; Matched in Shopify adds up the Shopify payments matched to
    the deposit's transactions. Chargebacks and returned items are shown on their own, not in Net. Authorize.Net reports no
    fees per deposit: it bills them monthly.
  </Callout>
)

/** One deposit: its figures, and the payments in it (the Payments list, kept to this batch). */
function SettlementDetail({ s }: { s: SettlementRowRead }) {
  const openRecord = useOpenRecord()
  const columns = useMemo(() => paymentColumns((id) => { openRecord('/orders', id) }), [openRecord])
  const money = (v: string | null) => (v == null ? '—' : formatMoney(v, s.currency))
  return (
    <RecordSheet
      title={`Batch ${s.batch_id}`}
      subtitle={`${s.gateway} · settled ${formatDateTime(s.settled_at)}`}
      badges={<StatusCell value={s.issue} />}
      actions={<ViewFileButton source={settlementPdf(s)}>Settlement statement (PDF)</ViewFileButton>}
      facts={[
        { label: 'Net', value: money(s.net_amount) },
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
            fixed={[{ field: 'settlement_id', op: 'eq', value: String(s.id) }]} searchable={false}
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
      resource={settlementsClient} title="Settlements"
      description="The deposits each payment provider paid to the bank, with the Shopify payments in them. Use the views by month and for what needs a look."
      columns={columns} defaultHidden={hiddenSettlementColumns} notice={NOTE} emptyTitle="No settlements yet"
      emptyText={EMPTY_TEXT.settlements} onExport={downloadSettlements} renderDetail={(s) => <SettlementDetail s={s} />}
      toolbar={({ spec }) => <ViewFileButton source={settlementsReportPdf(spec)}>Report (PDF)</ViewFileButton>}
    />
  )
}
