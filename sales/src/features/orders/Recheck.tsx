import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, formatTime, queryKeys, useNotices } from '@/platform-ui'
import { ordersClient, resolveReview, type OrderRead } from './ordersApi'

/** Re-check a Review order against today's Item Master (FR-OB-15;
 *  sales.order.operate). Always says that the check ran — in the list's or the
 *  sheet's status row: a reason that is still true reads the same as before.
 *  `compact`: the list's row action; `bare`: the button only (a sheet's action bar). */
export function Recheck({ o, compact, bare, onResolved }: { o: OrderRead; compact?: boolean; bare?: boolean; onResolved?: () => void }) {
  const qc = useQueryClient()
  const notices = useNotices()
  const m = useMutation({
    mutationFn: () => resolveReview(o.id),
    onSuccess: async (r) => {
      const at = formatTime(Date.now())
      const ref = o.source_reference ?? `#${o.order_number}`
      notices.notify(r.remaining.length > 0
        ? { key: `recheck:${o.id}`, tone: 'warning', text: `${ref} re-checked at ${at}: still ${r.remaining.length === 1 ? '1 problem' : `${r.remaining.length} problems`} — ${r.remaining.join('; ')}` }
        : { key: `recheck:${o.id}`, tone: 'success', text: `${ref} re-checked at ${at}: resolved` })
      await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
      await qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, o.id) })
      if (r.remaining.length === 0) onResolved?.()
    },
  })
  const button = (
    <ActionButton size="sm" variant={compact ? 'secondary' : undefined} permission="sales.order.operate"
      onAction={() => m.mutateAsync()}>Re-check</ActionButton>
  )
  if (bare) return button
  // In a row: clicking the button must not open the order.
  if (compact) return <div onClick={(e) => e.stopPropagation()}>{button}</div>
  return (
    <section aria-label="Resolve" className="flex flex-col gap-2 text-sm">
      <p className="text-neutral-600">Fix the cause in the Item Master (an item, a unit, a partner code, a DC route), then re-check.</p>
      {button}
    </section>
  )
}
