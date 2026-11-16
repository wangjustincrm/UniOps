import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, queryKeys, useNotices } from '@/platform-ui'
import { useTestRunRefusal } from '@/features/common/testRun'
import { fulfilInShopify, ordersClient, type OrderDetail } from './ordersApi'

const OPERATE = 'sales.order.operate'

const errorMessages: Record<string, string> = {
  'order.not_accepted': 'Only an order whose 940 Radiant accepted is fulfilled before its 945.',
  'order.not_shopify': 'Only a Shopify order is fulfilled in Shopify.',
  'auth.forbidden': 'You do not have permission to do this.',
}

/** Fulfil in Shopify now (user 2026-11-16; sales.order.operate): for a Shopify order whose 940 Radiant
 *  accepted — e.g. when the store's Write was off at the 997. The whole order is fulfilled without tracking
 *  (the customer is not told) and archived; its 945 adds the tracking. Never a second fulfillment. */
export function FulfilInShopify({ o }: { o: OrderDetail }) {
  const qc = useQueryClient()
  const notices = useNotices()
  const refused = useTestRunRefusal()
  const m = useMutation({
    mutationFn: () => fulfilInShopify(o.id),
    onSuccess: async () => {
      notices.notify({ key: `fulfil:${o.id}`, tone: 'success', text: 'Queued: Shopify is fulfilled and the order archived on the next outbox pass.' })
      await qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, o.id) })
    },
  })
  if (o.source_type !== 'SHOPIFY' || o.fulfillment_status !== '940 Accepted') return null
  return (
    <ActionButton size="sm" variant="secondary" permission={OPERATE} errorMessages={errorMessages}
      onAction={() => m.mutateAsync().catch(refused)}>
      {o.shopify_fulfillment_id ? 'Archive in Shopify now' : 'Fulfil in Shopify now'}
    </ActionButton>
  )
}
