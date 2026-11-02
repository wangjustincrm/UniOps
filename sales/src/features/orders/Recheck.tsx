import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, ErrorMessage, formatTime, queryKeys } from '@/platform-ui'
import { ordersClient, resolveReview, type OrderRead } from './ordersApi'

/** Re-check a Review order against today's Item Master (FR-OB-15;
 *  sales.order.operate). Always says that the check ran: a reason that is
 *  still true reads the same as before. `compact` is the list's row action. */
export function Recheck({ o, compact, onResolved }: { o: OrderRead; compact?: boolean; onResolved?: () => void }) {
  const qc = useQueryClient()
  const [remaining, setRemaining] = useState<string[] | null>(null)
  const [checkedAt, setCheckedAt] = useState<number | null>(null)
  const m = useMutation({
    mutationFn: () => resolveReview(o.id),
    onSuccess: async (r) => {
      setRemaining(r.remaining)
      setCheckedAt(Date.now())
      await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
      await qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, o.id) })
      if (r.remaining.length === 0) onResolved?.()
    },
  })
  const status = checkedAt && remaining && (
    remaining.length > 0 ? (
      <div role="status" className="text-xs text-danger-600">
        <p className="font-medium">Re-checked at {formatTime(checkedAt)}: still {remaining.length === 1 ? '1 problem' : `${remaining.length} problems`}</p>
        {!compact && <ul>{remaining.map((r) => <li key={r}>{r}</li>)}</ul>}
      </div>
    ) : <p role="status" className="text-xs text-success-700">Re-checked at {formatTime(checkedAt)}: resolved</p>
  )
  const button = (
    <ActionButton size="sm" variant={compact ? 'secondary' : undefined} permission="sales.order.operate"
      onAction={() => m.mutateAsync().catch(() => {})}>Re-check</ActionButton>
  )
  if (compact) {
    // In a row: clicking the button must not open the order.
    return <div className="flex flex-col gap-1" onClick={(e) => e.stopPropagation()}>{button}{status}<ErrorMessage error={m.error} /></div>
  }
  return (
    <section aria-label="Resolve" className="flex flex-col gap-2 rounded-md border border-neutral-100 p-2 text-sm">
      <p className="text-neutral-600">Fix the cause in the Item Master (an item, a unit, a partner code, a DC route), then re-check.</p>
      {button}
      {status}
      <ErrorMessage error={m.error} />
    </section>
  )
}
