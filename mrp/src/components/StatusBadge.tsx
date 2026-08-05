// StatusBadge — domain status -> visual mapping stays in the app (see
// @uniops/shell/ui/badge.tsx comment). MRP has one status domain so far:
// forecast version lifecycle (draft -> confirmed -> superseded).
import { cn } from '@/lib/utils'

export type ForecastVersionStatus = 'draft' | 'confirmed' | 'superseded'

const LABEL: Record<ForecastVersionStatus, string> = {
  draft: 'Draft',
  confirmed: 'Confirmed',
  superseded: 'Superseded',
}

const STYLE: Record<ForecastVersionStatus, string> = {
  draft: 'bg-amber-50 text-amber-700 ring-amber-200',
  confirmed: 'bg-success-50 text-success-700 ring-emerald-200',
  superseded: 'bg-neutral-100 text-neutral-500 ring-neutral-200',
}

export function StatusBadge({ status }: { status: string }) {
  const key = (status in LABEL ? status : 'draft') as ForecastVersionStatus
  return (
    <span className={cn(
      'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
      STYLE[key],
    )}>
      {LABEL[key] ?? status}
    </span>
  )
}
