import { Badge, type BadgeTone } from '@uniops/shell'
import { cn } from '@/lib/utils'

// Generic Badge lives in @uniops/shell; re-export for convenience.
export { Badge }

type BadgeVariant = BadgeTone

// Canonical OA document-status → shell Badge variant mapping.
// Mirrors epms/src/components/ui/badge.tsx so statuses look identical across
// modules. Keep raw Tailwind color literals OUT of pages — add new statuses here.
const STATUS_CONFIG: Record<string, { label: string; variant: BadgeVariant; dot: string }> = {
  // Workflow statuses (expenses / PA)
  draft:       { label: 'Draft',       variant: 'neutral', dot: 'bg-fg-faint' },
  submitted:   { label: 'Submitted',   variant: 'warning', dot: 'bg-warning-solid' },
  in_review:   { label: 'In Review',   variant: 'info',    dot: 'bg-primary-500' },
  approved:    { label: 'Approved',    variant: 'success', dot: 'bg-success-solid' },
  returned:    { label: 'Returned',    variant: 'warning', dot: 'bg-warning-solid' },
  rejected:    { label: 'Rejected',    variant: 'danger',  dot: 'bg-danger-solid' },
  cancelled:   { label: 'Cancelled',   variant: 'outline', dot: 'bg-fg-faint' },
  paid:        { label: 'Paid',        variant: 'dark',    dot: 'bg-fg-faint' },
  processed:   { label: 'Processed',   variant: 'dark',    dot: 'bg-fg-faint' },
  // Invoice statuses
  unmatched:   { label: 'Unmatched',   variant: 'warning', dot: 'bg-warning-solid' },
  matched:     { label: 'Matched',     variant: 'success', dot: 'bg-success-solid' },
  exception:   { label: 'Exception',   variant: 'danger',  dot: 'bg-danger-solid' },
  uploaded:    { label: 'Uploaded',    variant: 'neutral', dot: 'bg-fg-faint' },
  reviewed:    { label: 'Reviewed',    variant: 'info',    dot: 'bg-primary-500' },
  used:        { label: 'Used',        variant: 'success', dot: 'bg-success-solid' },
}

function titleCase(status: string): string {
  return status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const config = STATUS_CONFIG[status]
  return (
    <Badge variant={config?.variant ?? 'neutral'}>
      <span className={cn('size-1.5 rounded-full', config?.dot ?? 'bg-fg-faint')} />
      {label ?? config?.label ?? titleCase(status)}
    </Badge>
  )
}
