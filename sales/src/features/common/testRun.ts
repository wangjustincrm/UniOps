import { businessErrorOf, useNotices } from '@/platform-ui'

// The server's refusals of the shadow run: an action shows as in production and is refused there.
const CODES = ['order.shadow', 'send940.shadow', 'send940.shadow_order', 'invoice.shadow', 'invoice.shadow_order', 'shipment.shadow']

/** For a form or a dialog: a test-run refusal goes to the status row and ends the action;
 *  anything else stays where that form shows its errors. Use as `.catch(refused)`. */
export function useTestRunRefusal() {
  const notices = useNotices()
  return (e: unknown): undefined => {
    const be = businessErrorOf(e)
    if (!be || !CODES.includes(be.code)) throw e
    notices.notify({ tone: 'error', text: be.message })
    return undefined
  }
}
