import { Badge } from '@uniops/shell'
import { cn } from '@/lib/utils'
import type { DocumentStatus } from '@/types'

// Generic Badge now lives in @uniops/shell; re-export it for existing imports.
export { Badge }

type BadgeVariant = 'neutral' | 'warning' | 'info' | 'success' | 'danger' | 'dark'

const STATUS_CONFIG: Record<
  DocumentStatus,
  { label: string; variant: BadgeVariant; dot: string }
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
  nc_milk: { label: 'Milk / NC', variant: 'info', dot: 'bg-primary-500' },
  // Purchase Agreement — 'active' is the approval-terminal status (not 'approved').
  active: { label: 'Active', variant: 'success', dot: 'bg-success-600' },
  expired: { label: 'Expired', variant: 'warning', dot: 'bg-warning-500' },
  // Agreement payment-schedule row statuses.
  pending: { label: 'Pending', variant: 'neutral', dot: 'bg-neutral-400' },
  received: { label: 'Received', variant: 'info', dot: 'bg-primary-500' },
  overdue: { label: 'Overdue', variant: 'danger', dot: 'bg-danger-600' },
  waived: { label: 'Waived', variant: 'dark', dot: 'bg-neutral-400' },
  // House-account pickup receipt statuses.
  pending_ap_review: { label: 'Pending AP Review', variant: 'warning', dot: 'bg-warning-500' },
  open: { label: 'Open', variant: 'info', dot: 'bg-primary-500' },
  reconciled: { label: 'Reconciled', variant: 'success', dot: 'bg-success-600' },
  voided: { label: 'Voided', variant: 'dark', dot: 'bg-neutral-400' },
}

// The wording in STATUS_CONFIG above is the ONLY place a status is spelled out
// for a human in this app. Callers that need the words WITHOUT the badge — e.g.
// InvoiceReceiptsPanel's empty state, which has to say "2 Removed" inside a
// sentence — must come through here rather than keeping their own status→text
// map, or the two copies drift and the same status ends up with two names on
// two screens. Takes a plain string (not DocumentStatus) so callers holding a
// narrower union — ReceiptStatus, schedule-row status — can pass it without a
// cast, and falls back to the raw value for anything not in the table, exactly
// as StatusBadge does.
export function statusLabel(status: string): string {
  return STATUS_CONFIG[status as DocumentStatus]?.label ?? String(status)
}

export function StatusBadge({ status, label }: { status: DocumentStatus; label?: string }) {
  const config = STATUS_CONFIG[status] ?? { label: String(status), variant: 'neutral' as BadgeVariant, dot: 'bg-neutral-400' }
  return (
    <Badge variant={config.variant}>
      <span className={cn('size-1.5 rounded-full', config.dot)} />
      {label ?? config.label}
    </Badge>
  )
}
