import { Badge, type BadgeTone } from '@uniops/shell'

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

const STATUS_TONE: Record<BookingStatus, BadgeTone> = {
  pending:    'warning',
  confirmed:  'accent',
  cancelled:  'outline',
  completed:  'success',
  no_show:    'danger',
}

export function StatusBadge({ status }: { status: BookingStatus }) {
  return (
    <Badge variant={STATUS_TONE[status]}>
      {STATUS_LABEL[status]}
    </Badge>
  )
}
