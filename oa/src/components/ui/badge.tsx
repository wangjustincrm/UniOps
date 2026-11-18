import { Badge, type BadgeTone } from '@uniops/shell'

// Generic Badge lives in @uniops/shell; re-export for convenience.
export { Badge }

type BadgeVariant = BadgeTone

// Canonical OA document-status → shell Badge variant mapping.
// Mirrors epms/src/components/ui/badge.tsx so statuses look identical across
// modules. Keep raw Tailwind color literals OUT of pages — add new statuses here.
const STATUS_CONFIG: Record<string, { label: string; variant: BadgeVariant }> = {
  // Workflow statuses (expenses / PA)
  draft:       { label: 'Draft',       variant: 'neutral' },
  submitted:   { label: 'Submitted',   variant: 'warning' },
  in_review:   { label: 'In Review',   variant: 'info' },
  approved:    { label: 'Approved',    variant: 'success' },
  returned:    { label: 'Returned',    variant: 'warning' },
  rejected:    { label: 'Rejected',    variant: 'danger' },
  cancelled:   { label: 'Cancelled',   variant: 'outline' },
  paid:        { label: 'Paid',        variant: 'dark' },
  processed:   { label: 'Processed',   variant: 'dark' },
  // Invoice statuses
  unmatched:   { label: 'Unmatched',   variant: 'warning' },
  matched:     { label: 'Matched',     variant: 'success' },
  exception:   { label: 'Exception',   variant: 'danger' },
  uploaded:    { label: 'Uploaded',    variant: 'neutral' },
  reviewed:    { label: 'Reviewed',    variant: 'info' },
  used:        { label: 'Used',        variant: 'success' },
}

function titleCase(status: string): string {
  return status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const config = STATUS_CONFIG[status]
  return (
    <Badge variant={config?.variant ?? 'neutral'} dot>
      {label ?? config?.label ?? titleCase(status)}
    </Badge>
  )
}
