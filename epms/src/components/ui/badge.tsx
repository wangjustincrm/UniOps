import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'
import type { DocumentStatus } from '@/types'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium',
  {
    variants: {
      variant: {
        neutral: 'bg-neutral-100 text-neutral-500',
        warning: 'bg-warning-50 text-warning-600',
        info: 'bg-primary-100 text-primary-700',
        success: 'bg-success-50 text-success-700',
        danger: 'bg-danger-50 text-danger-600',
        dark: 'bg-neutral-600 text-white',
      },
    },
    defaultVariants: { variant: 'neutral' },
  }
)

interface BadgeProps extends VariantProps<typeof badgeVariants> {
  className?: string
  children: React.ReactNode
}

export function Badge({ variant, className, children }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant }), className)}>
      {children}
    </span>
  )
}

const STATUS_CONFIG: Record<
  DocumentStatus,
  { label: string; variant: BadgeProps['variant']; dot: string }
> = {
  draft: { label: 'Draft', variant: 'neutral', dot: 'bg-neutral-400' },
  submitted: { label: 'Submitted', variant: 'warning', dot: 'bg-warning-500' },
  in_review: { label: 'In Review', variant: 'info', dot: 'bg-primary-500' },
  approved: { label: 'Approved', variant: 'success', dot: 'bg-success-600' },
  returned: { label: 'Returned', variant: 'warning', dot: 'bg-warning-500' },
  rejected: { label: 'Rejected', variant: 'danger', dot: 'bg-danger-600' },
  cancelled: { label: 'Cancelled', variant: 'danger', dot: 'bg-danger-600' },
  issued: { label: 'Issued', variant: 'success', dot: 'bg-success-600' },
  partially_received: { label: 'Partial Receipt', variant: 'info', dot: 'bg-primary-500' },
  fully_received: { label: 'Fully Received', variant: 'success', dot: 'bg-success-600' },
  confirmed: { label: 'Confirmed', variant: 'success', dot: 'bg-success-600' },
  collected: { label: 'Collected', variant: 'success', dot: 'bg-success-600' },
  matched: { label: 'Matched', variant: 'success', dot: 'bg-success-600' },
  paid: { label: 'Paid', variant: 'dark', dot: 'bg-neutral-400' },
  closed: { label: 'Closed', variant: 'dark', dot: 'bg-neutral-400' },
}

export function StatusBadge({ status, label }: { status: DocumentStatus; label?: string }) {
  const config = STATUS_CONFIG[status]
  return (
    <Badge variant={config.variant}>
      <span className={cn('size-1.5 rounded-full', config.dot)} />
      {label ?? config.label}
    </Badge>
  )
}
