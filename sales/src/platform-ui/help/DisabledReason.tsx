import * as RP from '@radix-ui/react-popover'
import { useId, type ReactNode } from 'react'
import { HelpContent } from './HelpContent'

/** Why a control can't be used, readable on every device: a disabled button fires no events (no
 *  hover, no tap, no focus), so it sits in a focusable wrapper that takes the click and the keys and
 *  opens the reason; a screen reader hears it as the control's description. No reason: the control alone. */
export function DisabledReason({ reason, children }: { reason?: string | null; children: ReactNode }) {
  const descId = useId()
  if (!reason) return <>{children}</>
  return (
    <>
    <RP.Root>
      <RP.Trigger asChild>
        <span role="button" tabIndex={0} aria-describedby={descId} aria-disabled="true" data-disabled-reason
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); (e.currentTarget as HTMLElement).click() } }}
          className="inline-flex cursor-not-allowed rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600 [&>*]:pointer-events-none">
          {children}
        </span>
      </RP.Trigger>
      <HelpContent entry={{ title: 'Not available now', body: reason }} />
    </RP.Root>
    <span id={descId} className="sr-only">Unavailable: {reason}</span>
    </>
  )
}
