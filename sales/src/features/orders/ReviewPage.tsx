import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, ErrorMessage } from '@/platform-ui'
import { OrderList } from './OrdersPage'
import { resolveReview, type OrderRead } from './ordersApi'

function Recheck({ o, close }: { o: OrderRead; close: () => void }) {
  const qc = useQueryClient()
  const [remaining, setRemaining] = useState<string[] | null>(null)
  const m = useMutation({
    mutationFn: () => resolveReview(o.id),
    onSuccess: async (r) => {
      setRemaining(r.remaining)
      await qc.invalidateQueries({ queryKey: ['orders'] })
      await qc.invalidateQueries({ queryKey: ['order', o.id] })
      if (r.remaining.length === 0) close()
    },
  })
  return (
    <section aria-label="Resolve" className="flex flex-col gap-2 rounded-md border border-neutral-100 p-2 text-sm">
      <p className="text-neutral-600">Fix the cause in the Item Master (an item, a unit, a partner code, a DC route), then re-check.</p>
      <ActionButton size="sm" permission="sales.order.operate" onAction={() => m.mutateAsync().catch(() => {})}>Re-check</ActionButton>
      {remaining && remaining.length > 0 && (
        <ul className="text-xs text-danger-600">{remaining.map((r) => <li key={r}>{r}</li>)}</ul>
      )}
      <ErrorMessage error={m.error} />
    </section>
  )
}

/** Review worklist (FR-OB-15): orders that need a decision before any 940. */
export default function ReviewPage() {
  return (
    <OrderList
      title="Review"
      description="Orders held for a decision: an unknown item or partner, a unit without a conversion, a case-pack mismatch, a Walmart order without DC routing."
      emptyTitle="Nothing to review"
      fixed={[{ field: 'fulfillment_status', op: 'eq', value: 'Review' }]}
      detailExtra={(o, close) => <Recheck o={o} close={close} />}
    />
  )
}
