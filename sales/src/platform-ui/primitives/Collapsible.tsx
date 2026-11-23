import * as C from '@radix-ui/react-collapsible'
import { ChevronRight } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { cn } from '../lib/cn'

/** shadcn's Collapsible (Radix): a titled part that opens and closes (long JSON, a stack trace, details). */
export function Collapsible({ title, children, defaultOpen = false, className }: {
  title: ReactNode; children: ReactNode; defaultOpen?: boolean; className?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <C.Root open={open} onOpenChange={setOpen} className={cn('rounded-md border border-neutral-200', className)}>
      <C.Trigger className="flex w-full items-center gap-1 px-3 py-2 text-left text-sm font-medium text-neutral-800 hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
        <ChevronRight aria-hidden className={cn('h-4 w-4 transition-transform', open && 'rotate-90')} />{title}
      </C.Trigger>
      <C.Content className="border-t border-neutral-100 px-3 py-2">{children}</C.Content>
    </C.Root>
  )
}
