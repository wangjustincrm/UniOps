import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

// One labelled field of a line item shown as a card on a phone or tablet
// (ISS-0255). The control itself is the one the desktop table cell renders.
export function CardField({ label, className, children }: {
  label: string
  className?: string
  children: ReactNode
}) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-0.5', className)}>
      <span className="text-[10px] font-medium uppercase tracking-wide text-neutral-500">{label}</span>
      {children}
    </div>
  )
}
