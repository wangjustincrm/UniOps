import { useMemo } from 'react'
import { ListPage, useOpenRecord } from '@/platform-ui'
import { paymentsClient, type PaymentRowRead } from './paymentsApi'
import { hiddenPaymentColumns, paymentColumns } from './paymentsColumns'
import { EMPTY_TEXT } from '@/features/help/pageHelp'

/** Invoicing → Payments (Finance, user 2026-11-16): one list; each use (a method, by week or month,
 *  the exceptions) is a view of it. */
export default function PaymentsPage() {
  const openRecord = useOpenRecord()
  const columns = useMemo(() => paymentColumns((id) => { openRecord('/orders', id) }), [openRecord])
  return (
    <ListPage<PaymentRowRead>
      resource={paymentsClient} title="Payments" help="page:payments"
      columns={columns} defaultHidden={hiddenPaymentColumns} emptyTitle="No payments yet" emptyText={EMPTY_TEXT.payments}
    />
  )
}
