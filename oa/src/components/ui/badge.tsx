import { Badge } from '@uniops/shell'
import { cn } from '@/lib/utils'

// Generic Badge lives in @uniops/shell; re-export for convenience.
export { Badge }

type BadgeVariant = 'neutral' | 'warning' | 'info' | 'success' | 'danger' | 'dark'

// Canonical OA document-status → shell Badge variant mapping.
// Mirrors epms/src/components/ui/badge.tsx so statuses look identical across
// modules. Keep raw Tailwind color literals OUT of pages — add new statuses here.
const STATUS_CONFIG: Record<string, { label: string; variant: BadgeVariant; dot: string }> = {
  // Workflow statuses (expenses / PA)
  draft:       { label: 'Draft',       variant: 'neutral', dot: 'bg-neutral-400' },
  submitted:   { label: 'Submitted',   variant: 'warning', dot: 'bg-warning-500' },
  in_review:   { label: 'In Review',   variant: 'info',    dot: 'bg-primary-500' },
  approved:    { label: 'Approved',    variant: 'success', dot: 'bg-success-600' },
  returned:    { label: 'Returned',    variant: 'warning', dot: 'bg-warning-500' },
  rejected:    { label: 'Rejected',    variant: 'danger',  dot: 'bg-danger-600' },
  cancelled:   { label: 'Cancelled',   variant: 'danger',  dot: 'bg-danger-600' },
  paid:        { label: 'Paid',        variant: 'dark',    dot: 'bg-neutral-400' },
  processed:   { label: 'Processed',   variant: 'dark',    dot: 'bg-neutral-400' },
  // Invoice statuses
  unmatched:   { label: 'Unmatched',   variant: 'warning', dot: 'bg-warning-500' },
  matched:     { label: 'Matched',     variant: 'success', dot: 'bg-success-600' },
  exception:   { label: 'Exception',   variant: 'danger',  dot: 'bg-danger-600' },
  uploaded:    { label: 'Uploaded',    variant: 'neutral', dot: 'bg-neutral-400' },
  reviewed:    { label: 'Reviewed',    variant: 'info',    dot: 'bg-primary-500' },
  used:        { label: 'Used',        variant: 'success', dot: 'bg-success-600' },
}

function titleCase(status: string): string {
  return status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const config = STATUS_CONFIG[status]
  return (
    <Badge variant={config?.variant ?? 'neutral'}>
      <span className={cn('size-1.5 rounded-full', config?.dot ?? 'bg-neutral-400')} />
      {label ?? config?.label ?? titleCase(status)}
    </Badge>
  )
}
