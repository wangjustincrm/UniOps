import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, ConfirmAction, DiffTable, FormSheet, Hint, queryKeys, useNotices } from '@/platform-ui'
import { useTestRunRefusal } from '@/features/common/testRun'
import { cancelOrder, ordersClient, releaseHold, type OrderDetail } from './ordersApi'
import { compare945 } from './orderStatus'

const OPERATE = 'sales.order.operate'
const NOTHING_WRITTEN = 'This changes Sales only: nothing is written to Shopify, the warehouse or any partner. Tell them yourself if they need to know.'
const errorMessages: Record<string, string> = {
  'order.not_cancellable': 'This order cannot be cancelled now: reload it.',
  'order.sending': 'Its 940 is being sent right now: try again in a moment.',
  'order.hold_exit': 'That choice is no longer open for this order: reload it.',
  'auth.forbidden': 'You do not have permission to do this.',
}

function useRefreshOrder(id: number) {
  const qc = useQueryClient()
  return async () => {
    await qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, id) })
    await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
  }
}

// What each way out of a hold is called, and what it does.
function exitOf(to: string, conflict: boolean): { label: string; text: string; destructive?: boolean } {
  if (to === 'Cancelled') return { label: 'Cancel the order', text: NOTHING_WRITTEN, destructive: true }
  if (to === 'Shipped' && conflict) return { label: 'Keep the applied 945', text: 'The order goes back to Shipped. The second 945 stays stored and changes no quantity.' }
  if (to === 'Shipped') return { label: 'Mark as shipped', text: 'The order goes to Shipped as the 945 says. For a Shopify order the fulfillment is sent now.' }
  return { label: `Back to ${to}`, text: 'The order goes back to the status it held and goes on from there.' }
}

/** Cancel order, and Release hold or the 945 comparison (PRD 9.1, FR-OB-18, FR-OB-25). What is
 *  open comes from the server, as in production; a shadow order is refused there. */
export function OrderHoldActions({ o }: { o: OrderDetail }) {
  const refresh = useRefreshOrder(o.id)
  const notices = useNotices()
  const refused = useTestRunRefusal()
  const [cancelling, setCancelling] = useState(false)
  const [sheet, setSheet] = useState(false)
  const [choice, setChoice] = useState<string | null>(null)
  const name = o.source_reference ?? o.order_number
  const cancel = useMutation({ mutationFn: (why: string) => cancelOrder(o.id, why), onSuccess: async () => {
    notices.notify({ key: `cancel:${o.id}`, tone: 'success', text: `Order ${name} cancelled.` })
    await refresh()
  } })
  const release = useMutation({ mutationFn: ({ to, why }: { to: string; why: string }) => releaseHold(o.id, to, why), onSuccess: async (_, v) => {
    notices.notify({ key: `release:${o.id}`, tone: 'success', text: `Order ${name}: hold released to ${v.to}.` })
    setSheet(false)
    await refresh()
  } })
  const exits = o.hold_exits ?? []
  const conflict = o.on_hold_reason === '945 Conflict'
  const applied = o.shipments.find((s) => s.applied)
  const second = [...o.shipments].reverse().find((s) => !s.applied && !s.shadow)
  const picked = choice ? exitOf(choice, conflict) : null
  return (
    <>
      {o.cancellable && exits.length === 0 && (
        <ActionButton size="sm" variant="secondary" permission={OPERATE} onAction={() => setCancelling(true)}>Cancel order</ActionButton>
      )}
      {exits.length > 0 && (
        <ActionButton size="sm" permission={OPERATE} onAction={() => setSheet(true)}>{conflict ? 'Compare the 945s' : 'Release hold'}</ActionButton>
      )}
      <ConfirmAction open={cancelling} onOpenChange={setCancelling} title={`Cancel order ${name}?`} description={NOTHING_WRITTEN} destructive
        confirmLabel="Cancel order" reason={{ label: 'Why it is cancelled' }} errorMessages={errorMessages} onConfirm={(why) => cancel.mutateAsync(why).catch(refused)} />
      <FormSheet open={sheet} onOpenChange={setSheet} title={conflict ? 'Two 945s for this order' : 'Release hold'}
        description={conflict ? 'The applied 945 set the quantities; the second is stored and changed nothing.'
          : 'This order was cancelled at source after its 940. Decide where it goes.'}>
        <div className="flex flex-col gap-3">
          {conflict && applied && second && (
            <DiffTable ariaLabel="The two 945s" leftLabel={`Applied: 945 #${applied.document_id}`} rightLabel={`Second: 945 #${second.document_id}`}
              rows={compare945(applied, second)} />
          )}
          {exits.map((to) => {
            const e = exitOf(to, conflict)
            return (
              <div key={to} className="flex flex-col items-start gap-1">
                <ActionButton size="sm" variant={e.destructive ? 'secondary' : undefined} permission={OPERATE} onAction={() => setChoice(to)}>{e.label}</ActionButton>
                <Hint as="p">{e.text}</Hint>
              </div>
            )
          })}
        </div>
      </FormSheet>
      <ConfirmAction open={choice !== null} onOpenChange={(v) => { if (!v) setChoice(null) }} title={picked ? `${picked.label}?` : ''} description={picked?.text}
        destructive={picked?.destructive} confirmLabel={picked?.label} reason={{ label: 'Why' }} errorMessages={errorMessages}
        onConfirm={(why) => release.mutateAsync({ to: choice!, why }).catch(refused)} />
    </>
  )
}
