import * as RT from '@radix-ui/react-tooltip'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

/** A button whose accessible name is its tooltip (an icon control with its own
 *  look: the refresh split button). Plain icon buttons use IconButton. */
export function TipButton({ label, children, ...rest }: { label: string; children: ReactNode } & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'>) {
  return (
    <RT.Provider delayDuration={300}>
      <RT.Root>
        <RT.Trigger asChild>
          <button type="button" aria-label={label} {...rest}>{children}</button>
        </RT.Trigger>
        <RT.Portal>
          <RT.Content side="top" sideOffset={4} className="z-50 rounded bg-neutral-900 px-2 py-1 text-xs text-white shadow">{label}</RT.Content>
        </RT.Portal>
      </RT.Root>
    </RT.Provider>
  )
}
