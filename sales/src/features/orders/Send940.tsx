import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, ConfirmAction, queryKeys, useNotices } from '@/platform-ui'
import { ordersClient, send940, type OrderRead, type Send940Result } from './ordersApi'

const OPERATE = 'sales.order.operate'
// Pending: now, without the delay and for a Manual-mode partner; the others: a resend (FR-OB-03c).
const SENDABLE = ['Pending', '940 Rejected', '940 Timeout', '940 Failed']
// A live 940: only as a confirmed replacement.
const REPLACEABLE = ['940 Sent', '940 Accepted']

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
export function Send940({ o }: { o: OrderRead }) {
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const notices = useNotices()
  const m = useMutation({
    mutationFn: (replace: boolean) => send940(o.id, replace),
    onSuccess: async (r: Send940Result) => {
      // What happened, in the sheet's (or list's) status row.
      notices.notify(r.errors.length > 0
        ? { key: `send940:${o.id}`, tone: 'error', text: `The 940 failed its checks and was not sent: ${r.errors.join('; ')}` }
        : { key: `send940:${o.id}`, tone: 'success', text: `940 ${r.control} queued: it is sent on the next outbox pass.` })
      await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
      await qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, o.id) })
    },
  })
  const replace = REPLACEABLE.includes(o.fulfillment_status)
  if (!replace && !SENDABLE.includes(o.fulfillment_status)) return null
  return (
    <section aria-label="Send 940" className="flex flex-col gap-2 text-sm">
      {replace ? (
        <>
          <p className="text-neutral-600">The warehouse has a live 940 for this order. A change is sent only as a confirmed replacement.</p>
          <ActionButton size="sm" variant="secondary" permission={OPERATE} onAction={() => setConfirming(true)}>Replace 940</ActionButton>
          <ConfirmAction
            open={confirming}
            onOpenChange={setConfirming}
            title="Replace the live 940?"
            description="The live 940 is marked Replaced and a new 940 with a new control number is sent. The warehouse then holds two 940s for this order: make sure they cancel the first."
            confirmLabel="Replace 940"
            destructive
            errorMessages={errorMessages}
            onConfirm={() => m.mutateAsync(true)}
          />
        </>
      ) : (
        <ActionButton size="sm" permission={OPERATE} errorMessages={errorMessages} onAction={() => m.mutateAsync(false)}>Send 940 now</ActionButton>
      )}
    </section>
  )
}
