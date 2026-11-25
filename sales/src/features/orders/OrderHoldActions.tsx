import { orderMoved } from '@/features/common/moved'
import { isTestRunRefusal } from '@/features/common/testRun'
import { useState } from 'react'
import {
  ActionButton, ConfirmAction, DiffTable, FormSheet, Hint, Stack, useActionSends,
} from '@/platform-ui'
import { applyLater945, cancelOrder, ordersClient, releaseHold, type OrderDetail } from './ordersApi'
import { applied945, compare945, laterChoice } from './orderStatus'

const OPERATE = 'sales.order.operate'
const NOTHING_WRITTEN = 'This changes Sales only: nothing is written to Shopify, the warehouse or any partner. Tell them yourself if they need to know.'
const errorMessages: Record<string, string> = {
  'order.not_cancellable': 'This order cannot be cancelled now: reload it.',
  'order.sending': 'Its 940 is being sent right now: try again in a moment.',
  'order.hold_exit': 'That choice is no longer open for this order: reload it.',
  'entity.stale': 'Someone else changed this order meanwhile: it is reloaded, check it and decide again.',
  'auth.forbidden': 'You do not have permission to do this.',
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
  const [cancelling, setCancelling] = useState(false)
  const [sheet, setSheet] = useState(false)
  const [choice, setChoice] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const releaseSends = useActionSends(ordersClient, 'release_hold')
  const addSends = useActionSends(ordersClient, 'apply_part_945')  // the same for both
  const name = o.source_reference ?? o.order_number
  const exits = o.hold_exits ?? []
  const conflict = o.on_hold_reason === '945 Conflict'
  const applied = applied945(o.shipments)
  const later = conflict ? o.later_945 ?? null : null  // the server's: which 945 waits, and what adding it would be
  const second = o.shipments.find((s) => s.id === later?.shipment_id) ?? [...o.shipments].reverse().find((s) => !s.applied && !s.shadow)
  const add = later && laterChoice(later)
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
        confirmLabel="Cancel order" reason={{ label: 'Why it is cancelled' }} errorMessages={errorMessages} done={{ key: `cancel:${o.id}`, tone: 'success', text: `Order ${name} cancelled.` }} invalidates={orderMoved(o.id)}
        record={o} toStatusRow={isTestRunRefusal} onConfirm={(why) => cancelOrder(o.id, o.version, why)} />
      <FormSheet open={sheet} onOpenChange={setSheet} title={conflict ? 'Two 945s for this order' : 'Release hold'}
        description={conflict ? 'The applied 945 set the quantities; the second is stored and changed nothing.'
          : 'This order was cancelled at source after its 940. Decide where it goes.'}>
        <Stack gap={3}>
          {conflict && applied && second && (
            <DiffTable ariaLabel="The two 945s" leftLabel={`Applied: 945 ${o.shipments.filter((s) => s.applied).map((s) => `#${s.document_id}`).join(', ')}`} rightLabel={`Second: 945 #${second.document_id}`}
              rows={compare945(applied, second)} />
          )}
          {add && (
            <Stack gap={1} align="start">
              <ActionButton size="sm" permission={OPERATE} onAction={() => setAdding(true)}>{add.label}</ActionButton>
              <Hint as="p">{add.text}</Hint>
            </Stack>
          )}
          {later?.refused && <Hint as="p">The second 945 cannot be added here: {later.refused}.</Hint>}
          {exits.map((to) => {
            const e = exitOf(to, conflict)
            return (
              <Stack key={to} gap={1} align="start">
                <ActionButton size="sm" variant={e.destructive ? 'secondary' : undefined} permission={OPERATE} onAction={() => setChoice(to)}>{e.label}</ActionButton>
                <Hint as="p">{e.text}</Hint>
              </Stack>
            )
          })}
        </Stack>
      </FormSheet>
      {add && later && (
        <ConfirmAction open={adding} onOpenChange={setAdding} title={`${add.label}?`} description={add.confirm} confirmLabel={add.label}
          reason={{ label: 'Why' }} errorMessages={errorMessages} done={{ key: `later945:${o.id}`, tone: 'success', text: `Order ${name}: the second 945 was added.` }}
          invalidates={orderMoved(o.id)} record={o} sends={addSends} toStatusRow={isTestRunRefusal} after={() => setSheet(false)}
          onConfirm={(why) => applyLater945(o.id, o.version, later.kind!, later.shipment_id, why)} />
      )}
      <ConfirmAction open={choice !== null} onOpenChange={(v) => { if (!v) setChoice(null) }} title={picked ? `${picked.label}?` : ''} description={picked?.text}
        destructive={picked?.destructive} confirmLabel={picked?.label} reason={{ label: 'Why' }} errorMessages={errorMessages}
        done={{ key: `release:${o.id}`, tone: 'success', text: `Order ${name}: hold released to ${choice}.` }} invalidates={orderMoved(o.id)}
        record={o} sends={choice === 'Shipped' ? releaseSends : undefined} toStatusRow={isTestRunRefusal} after={() => setSheet(false)} onConfirm={(why) => releaseHold(o.id, o.version, choice!, why)} />
    </>
  )
}
