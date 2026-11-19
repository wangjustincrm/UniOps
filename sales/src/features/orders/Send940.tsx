import { isTestRunRefusal } from '@/features/common/testRun'
import { useState } from 'react'
import {
  ActionButton, ConfirmAction, queryKeys, type ActionDone, Stack, Text,
} from '@/platform-ui'
import { ordersClient, send940, type OrderDetail, type Send940Result } from './ordersApi'

const OPERATE = 'sales.order.operate'

const errorMessages: Record<string, string> = {
  'send940.historical': 'A historical order is never sent to the warehouse.',
  'send940.in_progress': 'A 940 for this order is already on its way.',
  'send940.live': 'A 940 for this order is live. Use Replace 940 to send a replacement.',
  'send940.nothing_to_replace': 'There is no live 940 to replace.',
  'auth.forbidden': 'You do not have permission to send 940s.',
}

/** Send 940 now / Replace 940 (FR-OB-03c; sales.order.operate). The 940 is
 *  queued and put by the next outbox pass; its file and 997 status show
 *  under Warehouse 940s. */
export function Send940({ o }: { o: OrderDetail }) {
  const [confirming, setConfirming] = useState(false)
  // What happened, in the sheet's (or list's) status row: queued, or the checks it failed; the order reads again.
  const sent = {
    done: (res: unknown): ActionDone => {
      const r = res as Send940Result
      return r.errors.length > 0
        ? { key: `send940:${o.id}`, tone: 'error', text: `The 940 failed its checks and was not sent: ${r.errors.join('; ')}` }
        : { key: `send940:${o.id}`, tone: 'success', text: `940 ${r.control} queued: it is sent on the next outbox pass.` }
    },
    invalidates: [queryKeys.list(ordersClient), queryKeys.detail(ordersClient, o.id)],
  }
  // sales-api says what is offered (app/domain/order_rules.py send_940_action): never once a 945 came.
  if (!o.send_940) return null
  const replace = o.send_940 === 'replace'
  return (
    <Stack aria-label="Send 940" as="section" gap={2} size="sm">
      {replace ? (
        <>
          <Text as="p" tone="soft">The warehouse has a live 940 for this order. A change is sent only as a confirmed replacement.</Text>
          <ActionButton size="sm" variant="secondary" permission={OPERATE} onAction={() => setConfirming(true)}>Replace 940</ActionButton>
          <ConfirmAction
            open={confirming}
            onOpenChange={setConfirming}
            title="Replace the live 940?"
            description="The live 940 is marked Replaced and a new 940 with a new control number is sent. The warehouse then holds two 940s for this order: make sure they cancel the first."
            confirmLabel="Replace 940"
            destructive
            errorMessages={errorMessages}
            toStatusRow={isTestRunRefusal} {...sent} onConfirm={() => send940(o.id, true)}
          />
        </>
      ) : (
        <ActionButton size="sm" permission={OPERATE} errorMessages={errorMessages} {...sent} onAction={() => send940(o.id, false)}>Send 940 now</ActionButton>
      )}
    </Stack>
  )
}
