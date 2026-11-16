import * as RP from '@radix-ui/react-popover'
import { useId, type ReactElement } from 'react'
import { HelpContent } from './HelpContent'

/** Why a control can't be used, readable on every device. A natively disabled button fires no
 *  events (no tap, no focus, no hover), so the control stays enabled but `aria-disabled`: it keeps
 *  its focus and its place (full width in a phone's ActionBar too), a tap / click / Enter opens the
 *  reason, and a screen reader hears it as the control's description. The child must be one
 *  control that forwards its ref and does nothing on click while a reason is shown (ActionButton
 *  does this). No reason: the control as it is. */
export function DisabledReason({ reason, children }: { reason?: string | null; children: ReactElement }) {
  const descId = useId()
  if (!reason) return children
  return (
    <>
      <RP.Root>
        <RP.Trigger asChild aria-disabled="true" aria-describedby={descId} data-disabled-reason="">
          {children}
        </RP.Trigger>
        <HelpContent entry={{ title: 'Not available now', body: reason }} />
      </RP.Root>
      <span id={descId} className="sr-only">Unavailable: {reason}</span>
    </>
  )
}
