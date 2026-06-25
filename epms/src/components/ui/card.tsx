import { cn } from '@/lib/utils'

interface CardProps {
  className?: string
  children: React.ReactNode
}

export function Card({ className, children }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)]',
        className
      )}
    >
      {children}
    </div>
  )
}

export function CardHeader({ className, children }: CardProps) {
  return (
    <div className={cn('border-b border-neutral-200 px-6 py-4', className)}>
      {children}
    </div>
  )
}

export function CardContent({ className, children }: CardProps) {
  return <div className={cn('p-6', className)}>{children}</div>
}

export function CardTitle({ className, children }: CardProps) {
  return (
    <h3 className={cn('text-lg font-semibold text-neutral-900', className)}>
      {children}
    </h3>
  )
}
