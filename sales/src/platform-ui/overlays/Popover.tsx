import * as RP from '@radix-ui/react-popover'
import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

export interface PopoverProps {
  trigger: ReactNode
  children: ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
  align?: 'start' | 'center' | 'end'
  className?: string
}

export function Popover({ trigger, children, open, onOpenChange, align = 'start', className }: PopoverProps) {
  return (
    <RP.Root open={open} onOpenChange={onOpenChange}>
      <RP.Trigger asChild>{trigger}</RP.Trigger>
      <RP.Portal>
        <RP.Content
          align={align}
          sideOffset={4}
          className={cn('z-50 rounded-lg border border-neutral-200 bg-white p-2 shadow-lg focus:outline-none', className)}
        >
          {children}
        </RP.Content>
      </RP.Portal>
    </RP.Root>
  )
}
