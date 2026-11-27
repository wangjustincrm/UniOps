import { orderMoved } from '@/features/common/moved'
import { isTestRunRefusal } from '@/features/common/testRun'
import { useState, type ReactNode } from 'react'
import {
  ConfirmAction, actionDone, useActionSends, type ActionDone, type ActionResultRow, type BarItem,
} from '@/platform-ui'
import { ordersClient, replace940, send940, type OrderDetail } from './ordersApi'

const OPERATE = 'sales.order.operate'

const errorMessages: Record<string, string> = {
  'send940.historical': 'A historical order is never sent to the warehouse.',
  'entity.stale': 'The order changed since you opened it: it is read again here. Look, then decide again.',
  'send940.in_progress': 'A 940 for this order is already on its way.',
  'send940.live': 'A 940 for this order is live. Use Replace 940 to send a replacement.',
  'send940.nothing_to_replace': 'There is no live 940 to replace.',
  'auth.forbidden': 'You do not have permission to send 940s.',
}

/** Send 940 now / Replace 940 (FR-OB-03c; sales.order.operate), as the order's action bar shows them: Send 940 now
 *  a button, Replace 940… in "⋯" (it asks first). The 940 is queued and put by the next outbox pass; its file and
 *  997 status show under Warehouse 940s. sales-api says what is offered (app/domain/order_rules.py send_940_action):
 *  never once a 945 came. `node`: the replace confirmation. */
export function useSend940Items(o: OrderDetail | undefined): { items: BarItem[]; node: ReactNode } {
  const [confirming, setConfirming] = useState(false)
  const sends = useActionSends(ordersClient, 'send_940')
  const replaceSends = useActionSends(ordersClient, 'replace_940')
  if (!o?.send_940) return { items: [], node: null }
  // What happened, in the sheet's (or page's) status row: queued, or the checks it failed; the order reads again.
  const sent = {
    done: (res: unknown): ActionDone => {
      const r = res as ActionResultRow
      return actionDone({ ...r, note: `${r.note ?? '940 queued'}: it is sent on the next outbox pass.` }, '', `send940:${o.id}`)
    },
    invalidates: orderMoved(o.id),
  }
  if (o.send_940 !== 'replace') {
    return { items: [{ id: 'send-940', label: 'Send 940 now', permission: OPERATE, primary: 10, sends, errorMessages, ...sent,
      onSelect: () => send940(o.id, o.version) }], node: null }
  }
  return {
    items: [{ id: 'replace-940', label: 'Replace 940…', permission: OPERATE, order: 20, sends: replaceSends, opensDialog: true, refreshes: false,
      hint: 'The warehouse has a live 940: a change is sent only as a confirmed replacement', onSelect: () => setConfirming(true) }],
    node: (
      <ConfirmAction
        open={confirming}
        onOpenChange={setConfirming}
        title="Replace the live 940?"
        description="The live 940 is marked Replaced and a new 940 with a new control number is sent. The warehouse then holds two 940s for this order: make sure they cancel the first."
        confirmLabel="Replace 940"
        destructive
        reason={{ label: 'Why it is replaced' }}
        record={o}
        sends={replaceSends}
        errorMessages={errorMessages}
        toStatusRow={isTestRunRefusal} {...sent} onConfirm={(why) => replace940(o.id, o.version, why)}
      />
    ),
  }
}
