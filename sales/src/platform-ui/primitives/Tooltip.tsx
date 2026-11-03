import * as RT from '@radix-ui/react-tooltip'
import type { ReactNode } from 'react'

/** Text with a hint on hover or focus (e.g. a partner's code behind its name).
 *  Focusable, so the hint is reachable by keyboard. */
export function Tooltip({ tip, children }: { tip: string; children: ReactNode }) {
  return (
    <RT.Provider delayDuration={300}>
      <RT.Root>
        <RT.Trigger asChild>
          <span tabIndex={0} className="cursor-default underline decoration-dotted decoration-neutral-300 underline-offset-2">
            {children}
          </span>
        </RT.Trigger>
        <RT.Portal>
          <RT.Content side="top" sideOffset={4} className="z-50 rounded bg-neutral-900 px-2 py-1 text-xs text-white shadow">
            {tip}
          </RT.Content>
        </RT.Portal>
      </RT.Root>
    </RT.Provider>
  )
}
