import { cn } from '@/lib/utils'
import type { VisitStatus, AccessArea } from '@/services/api'

const STATUS_LABEL: Record<VisitStatus, string> = {
  pending_approval: 'Pending Approval',
  confirmed:        'Confirmed',
  checked_in:       'On-Site',
  checked_out:      'Departed',
  cancelled:        'Cancelled',
  no_show:          'No Show',
}

const STATUS_STYLE: Record<VisitStatus, string> = {
  pending_approval: 'bg-amber-50 text-amber-700 ring-amber-200',
  confirmed:        'bg-primary-50 text-primary-700 ring-primary-200',
  checked_in:       'bg-success-50 text-success-600 ring-emerald-200',
  checked_out:      'bg-neutral-100 text-neutral-600 ring-neutral-200',
  cancelled:        'bg-neutral-100 text-neutral-400 ring-neutral-200',
  no_show:          'bg-danger-50 text-danger-600 ring-red-200',
}

export function StatusBadge({ status }: { status: VisitStatus }) {
  return (
    <span className={cn(
      'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
      STATUS_STYLE[status],
    )}>
      {STATUS_LABEL[status]}
    </span>
  )
}

// Overdue is a derived state, not a stored VisitStatus: a checked-in visit
// past its planned departure (mirrors the backend scheduler's definition).
export function isVisitOverdue(visit: {
  status: VisitStatus
  planned_departure: string | null
}): boolean {
  return (
    visit.status === 'checked_in' &&
    !!visit.planned_departure &&
    new Date(visit.planned_departure).getTime() < Date.now()
  )
}

export function OverdueBadge() {
  return (
    <span className="inline-flex items-center rounded-full bg-danger-50 px-2 py-0.5 text-xs font-medium text-danger-600 ring-1 ring-inset ring-red-200">
      Overdue
    </span>
  )
}

const ACCESS_AREA_LABEL: Record<AccessArea, string> = {
  office:              'Office',
  warehouse:           'Warehouse',
  production_non_gmp:  'Production (Non-GMP)',
  production_gmp:      'Production (GMP)',
  laboratory:          'Laboratory',
  all:                 'Entire Plant',
}

const ACCESS_AREA_STYLE: Record<AccessArea, string> = {
  office:              'bg-success-50 text-success-600 ring-emerald-200',
  warehouse:           'bg-warning-50 text-warning-500 ring-amber-200',
  production_non_gmp:  'bg-orange-50 text-orange-600 ring-orange-200',
  production_gmp:      'bg-danger-50 text-danger-600 ring-red-200',
  laboratory:          'bg-danger-50 text-danger-600 ring-red-200',
  all:                 'bg-danger-50 text-danger-600 ring-red-200',
}

export function AccessAreaBadge({ area }: { area: AccessArea }) {
  return (
    <span className={cn(
      'inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset',
      ACCESS_AREA_STYLE[area],
    )}>
      {ACCESS_AREA_LABEL[area]}
    </span>
  )
}
