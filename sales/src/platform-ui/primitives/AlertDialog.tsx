import * as AD from '@radix-ui/react-alert-dialog'
import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

/** shadcn's AlertDialog (Radix): a confirm that interrupts — role="alertdialog", no outside-click close, the focus on
 *  Cancel. Used by ConfirmAction for a destructive confirm. */
export function AlertDialog({ open, onOpenChange, title, description, children, className }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <AD.Root open={open} onOpenChange={onOpenChange}>
      <AD.Portal>
        <AD.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <AD.Content className={cn('fixed left-1/2 top-1/2 z-50 grid w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 gap-4 rounded-lg border border-neutral-200 bg-white p-6 shadow-lg', className)}>
          <AD.Title className="text-base font-semibold text-neutral-900">{title}</AD.Title>
          {description
            ? <AD.Description className="text-sm text-neutral-600">{description}</AD.Description>
            : <AD.Description className="sr-only">{title}</AD.Description>}
          {children}
        </AD.Content>
      </AD.Portal>
    </AD.Root>
  )
}

export const AlertDialogCancel = AD.Cancel
export const AlertDialogAction = AD.Action
