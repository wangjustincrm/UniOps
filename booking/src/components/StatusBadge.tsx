import { cn } from '@/lib/utils'

export type BookingStatus =
  | 'pending'
  | 'confirmed'
  | 'cancelled'
  | 'completed'
  | 'no_show'

const STATUS_LABEL: Record<BookingStatus, string> = {
  pending:    'Pending',
  confirmed:  'Confirmed',
  cancelled:  'Cancelled',
  completed:  'Completed',
  no_show:    'No Show',
}

const STATUS_STYLE: Record<BookingStatus, string> = {
  pending:    'bg-warning-soft text-warning-fg ring-warning-line',
  confirmed:  'bg-primary-50 text-primary-700 ring-primary-200',
  cancelled:  'bg-surface-sunken text-fg-muted ring-line',
  completed:  'bg-success-soft text-success-fg ring-success-line',
  no_show:    'bg-danger-soft text-danger-fg ring-danger-line',
}

export function StatusBadge({ status }: { status: BookingStatus }) {
  return (
    <span className={cn(
      'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
      STATUS_STYLE[status],
    )}>
      {STATUS_LABEL[status]}
    </span>
  )
}
