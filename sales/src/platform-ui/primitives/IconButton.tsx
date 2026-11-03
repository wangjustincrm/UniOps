import * as RT from '@radix-ui/react-tooltip'
import { Loader2 } from 'lucide-react'
import type { MouseEvent, ReactNode } from 'react'
import { cn } from '../lib/cn'

/** A compact icon-only button: its label is the tooltip and the accessible
 *  name; `pending` shows a spinner and blocks it (an action in flight). */
export function IconButton({ label, icon, onClick, disabled, pending, className }: {
  label: string; icon: ReactNode; onClick: (e: MouseEvent<HTMLButtonElement>) => void; disabled?: boolean; pending?: boolean
  className?: string
}) {
  return (
    <RT.Provider delayDuration={300}>
      <RT.Root>
        <RT.Trigger asChild>
          {/* a disabled button fires no pointer events: the span keeps the tooltip */}
          <span className="inline-flex">
            <button type="button" aria-label={label} aria-busy={pending || undefined} disabled={disabled || pending} onClick={onClick}
              className={cn('inline-flex h-7 w-7 items-center justify-center rounded-md text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600 disabled:cursor-not-allowed disabled:opacity-40',
                className)}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : icon}
            </button>
          </span>
        </RT.Trigger>
        <RT.Portal>
          <RT.Content side="top" sideOffset={4} className="z-50 rounded bg-neutral-900 px-2 py-1 text-xs text-white shadow">
            {label}
          </RT.Content>
        </RT.Portal>
      </RT.Root>
    </RT.Provider>
  )
}
