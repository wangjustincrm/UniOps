import * as RP from '@radix-ui/react-popover'
import { cloneElement, useId, type ReactElement } from 'react'
import { HelpContent } from './HelpContent'

/** Why a control can't be used, readable on every device. A natively disabled button fires no
 *  events (no tap, no focus, no hover), so the control stays enabled but `aria-disabled`: it keeps
 *  its focus and its place (full width in a phone's ActionBar too), a tap / click / Enter opens the
 *  reason, and a screen reader hears it as the control's description. The child is one control that
 *  forwards its ref; while a reason is shown its own onClick is dropped and it can't submit a form,
 *  so nothing runs whatever it is. No reason: the control as it is. */
export function DisabledReason({ reason, children }: { reason?: string | null; children: ReactElement }) {
  const descId = useId()
  if (!reason) return children
  return (
    <>
      <RP.Root>
        <RP.Trigger asChild aria-disabled="true" aria-describedby={descId} data-disabled-reason="">
          {cloneElement(children as ReactElement<{ onClick?: unknown; type?: string }>, { onClick: undefined, type: 'button' })}
        </RP.Trigger>
        <HelpContent entry={{ title: 'Not available now', body: reason }} />
      </RP.Root>
      <span id={descId} className="sr-only">Unavailable: {reason}</span>
    </>
  )
}
