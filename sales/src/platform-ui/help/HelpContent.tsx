import * as RP from '@radix-ui/react-popover'
import type { ReactNode } from 'react'
import type { HelpEntry } from './types'

/** The popover's body, the same everywhere: a heading, the text, what to do next. Portalled, so a
 *  table's or a sheet's overflow never cuts it; it fits a phone (never wider than the screen). */
export function HelpContent({ entry, footer }: { entry: HelpEntry; footer?: ReactNode }) {
  return (
    <RP.Portal>
      <RP.Content data-help-content side="bottom" align="start" sideOffset={6} collisionPadding={12}
        className="z-50 w-[min(20rem,calc(100vw-1.5rem))] rounded-lg border border-neutral-200 bg-white p-3 text-left text-sm normal-case tracking-normal text-neutral-700 shadow-lg focus:outline-none">
        {entry.title && <p className="mb-1 font-semibold text-neutral-900">{entry.title}</p>}
        <p className="leading-snug">{entry.body}</p>
        {entry.next && <p className="mt-2 leading-snug"><span className="font-medium text-neutral-900">Next: </span>{entry.next}</p>}
        {footer}
        <RP.Arrow className="fill-white" />
      </RP.Content>
    </RP.Portal>
  )
}
