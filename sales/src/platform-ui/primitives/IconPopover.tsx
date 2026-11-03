import * as RP from '@radix-ui/react-popover'
import * as RT from '@radix-ui/react-tooltip'
import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

export interface IconPopoverProps {
  /** The icon's accessible name and its tooltip. */
  label: string
  icon: ReactNode
  /** A small count on the icon (e.g. the active search conditions). */
  badge?: number
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
  align?: 'start' | 'center' | 'end'
  className?: string
  disabled?: boolean
}

/** An icon button (tooltip, focus ring, optional count badge) that opens a
 *  popover — the toolbar's compact controls (Radix: portal, focus, Escape). */
export function IconPopover({ label, icon, badge, open, onOpenChange, children, align = 'end', className, disabled }: IconPopoverProps) {
  return (
    <RP.Root open={open} onOpenChange={onOpenChange}>
      <RT.Provider delayDuration={300}>
        <RT.Root>
          <RT.Trigger asChild>
            <RP.Trigger asChild>
              <button type="button" aria-label={badge ? `${label} (${badge})` : label} disabled={disabled}
                className={cn('relative inline-flex h-8 w-8 items-center justify-center rounded-lg border border-neutral-200 bg-white text-neutral-600',
                  'hover:bg-neutral-50 hover:text-neutral-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600',
                  'disabled:cursor-not-allowed disabled:opacity-40 data-[state=open]:bg-neutral-100', badge ? 'border-primary-200 text-primary-700' : '')}>
                {icon}
                {!!badge && (
                  <span aria-hidden className="absolute -right-1.5 -top-1.5 min-w-[1rem] rounded-full bg-primary-600 px-1 text-center text-[10px] font-semibold leading-4 text-white">
                    {badge}
                  </span>
                )}
              </button>
            </RP.Trigger>
          </RT.Trigger>
          <RT.Portal>
            <RT.Content side="top" sideOffset={4} className="z-50 rounded bg-neutral-900 px-2 py-1 text-xs text-white shadow">{label}</RT.Content>
          </RT.Portal>
        </RT.Root>
      </RT.Provider>
      <RP.Portal>
        <RP.Content align={align} sideOffset={6} aria-label={label}
          className={cn('z-50 rounded-lg border border-neutral-200 bg-white p-3 shadow-lg focus:outline-none', className)}>
          {children}
        </RP.Content>
      </RP.Portal>
    </RP.Root>
  )
}
