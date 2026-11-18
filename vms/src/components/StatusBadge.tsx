import { cn } from '@/lib/utils'
import { useAreaRules, type VisitStatus, type AccessArea, type AreaRule } from '@/services/api'
import { zoneName } from '@/lib/visitOptions'

const STATUS_LABEL: Record<VisitStatus, string> = {
  pending_approval: 'Pending Approval',
  confirmed:        'Confirmed',
  checked_in:       'On-Site',
  checked_out:      'Departed',
  cancelled:        'Cancelled',
  no_show:          'No Show',
}

const STATUS_STYLE: Record<VisitStatus, string> = {
  pending_approval: 'bg-warning-soft text-warning-fg ring-warning-line',
  confirmed:        'bg-primary-50 text-primary-700 ring-primary-200',
  checked_in:       'bg-success-soft text-success-fg ring-success-line',
  checked_out:      'bg-surface-sunken text-fg-secondary ring-line',
  cancelled:        'bg-surface-sunken text-fg-muted ring-line',
  no_show:          'bg-danger-soft text-danger-fg ring-danger-line',
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
    <span className="inline-flex items-center rounded-full bg-danger-soft px-2 py-0.5 text-xs font-medium text-danger-fg ring-1 ring-inset ring-danger-line">
      Overdue
    </span>
  )
}

// The six original zones keep their colours; zones added in VMS Admin are red
// when GMP-grade, slate otherwise.
const ACCESS_AREA_STYLE: Record<string, string> = {
  office:              'bg-success-soft text-success-fg ring-success-line',
  warehouse:           'bg-warning-soft text-warning-fg ring-warning-line',
  production_non_gmp:  'bg-cat7-soft text-cat7-fg ring-cat7-line',
  production_gmp:      'bg-danger-soft text-danger-fg ring-danger-line',
  laboratory:          'bg-danger-soft text-danger-fg ring-danger-line',
  all:                 'bg-danger-soft text-danger-fg ring-danger-line',
}
const GMP_STYLE = 'bg-danger-soft text-danger-fg ring-danger-line'
const OTHER_STYLE = 'bg-surface-sunken text-fg-secondary ring-line'

/** The label AccessAreaBadge shows — sort by this, not the code. */
export function accessAreaLabel(area: AccessArea, rules: Record<string, AreaRule> | undefined): string {
  return zoneName(rules, area)
}

export function AccessAreaBadge({ area }: { area: AccessArea }) {
  const { data: rules } = useAreaRules()
  const rule = rules?.[area]
  return (
    <span className={cn(
      'inline-flex items-center rounded-md px-1.5 py-0.5 text-2xs font-medium ring-1 ring-inset',
      ACCESS_AREA_STYLE[area] ?? (rule?.gmp_grade ? GMP_STYLE : OTHER_STYLE),
    )}>
      {zoneName(rules, area)}
    </span>
  )
}
