import { cn } from '../lib/cn'

interface CardProps {
  className?: string
  children: React.ReactNode
}

/** Raised surface with a hairline. Padding comes from CardContent / CardHeader. */
export function Card({ className, children }: CardProps) {
  return (
    <div className={cn('rounded-xl border border-line-subtle bg-surface shadow-xs', className)}>
      {children}
    </div>
  )
}

export function CardHeader({ className, children, actions }: CardProps & { actions?: React.ReactNode }) {
  return (
    <div className={cn('flex items-center justify-between gap-3 border-b border-line-subtle px-5 py-4', className)}>
      <div className="min-w-0">{children}</div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}

export function CardContent({ className, children }: CardProps) {
  return <div className={cn('p-5', className)}>{children}</div>
}

export function CardFooter({ className, children }: CardProps) {
  return (
    <div className={cn('flex items-center justify-end gap-2 border-t border-line-subtle px-5 py-3', className)}>
      {children}
    </div>
  )
}

export function CardTitle({ className, children }: CardProps) {
  return <h3 className={cn('text-base font-semibold text-fg', className)}>{children}</h3>
}

export function CardDescription({ className, children }: CardProps) {
  return <p className={cn('mt-0.5 text-sm text-fg-muted', className)}>{children}</p>
}
