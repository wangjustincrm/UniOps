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
  pending:    'bg-amber-50 text-amber-700 ring-amber-200',
  confirmed:  'bg-primary-50 text-primary-700 ring-primary-200',
  cancelled:  'bg-neutral-100 text-neutral-400 ring-neutral-200',
  completed:  'bg-success-50 text-success-600 ring-emerald-200',
  no_show:    'bg-danger-50 text-danger-600 ring-red-200',
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
