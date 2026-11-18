import { Badge, type BadgeTone } from '@uniops/shell'
import { cn } from '@/lib/utils'
import type { DocumentStatus } from '@/types'

// Generic Badge now lives in @uniops/shell; re-export it for existing imports.
export { Badge }

type BadgeVariant = BadgeTone

const STATUS_CONFIG: Record<
  DocumentStatus,
  { label: string; variant: BadgeVariant; dot: string }
> = {
  draft: { label: 'Draft', variant: 'neutral', dot: 'bg-fg-faint' },
  submitted: { label: 'Submitted', variant: 'warning', dot: 'bg-warning-solid' },
  in_review: { label: 'In Review', variant: 'info', dot: 'bg-primary-500' },
  approved: { label: 'Approved', variant: 'success', dot: 'bg-success-solid' },
  returned: { label: 'Returned', variant: 'warning', dot: 'bg-warning-solid' },
  rejected: { label: 'Rejected', variant: 'danger', dot: 'bg-danger-solid' },
  cancelled: { label: 'Cancelled', variant: 'outline', dot: 'bg-fg-faint' },
  issued: { label: 'Issued', variant: 'success', dot: 'bg-success-solid' },
  partially_received: { label: 'Partial Receipt', variant: 'info', dot: 'bg-primary-500' },
  fully_received: { label: 'Fully Received', variant: 'success', dot: 'bg-success-solid' },
  confirmed: { label: 'Confirmed', variant: 'success', dot: 'bg-success-solid' },
  collected: { label: 'Collected', variant: 'success', dot: 'bg-success-solid' },
  matched: { label: 'Matched', variant: 'success', dot: 'bg-success-solid' },
  paid: { label: 'Paid', variant: 'dark', dot: 'bg-fg-faint' },
  closed: { label: 'Closed', variant: 'dark', dot: 'bg-fg-faint' },
  nc_milk: { label: 'Milk / NC', variant: 'info', dot: 'bg-primary-500' },
  nc_pending: { label: 'NC Pending Approval', variant: 'warning', dot: 'bg-warning-solid' },
  // Purchase Agreement — 'active' is the approval-terminal status (not 'approved').
  active: { label: 'Active', variant: 'success', dot: 'bg-success-solid' },
  expired: { label: 'Expired', variant: 'warning', dot: 'bg-warning-solid' },
  // Agreement payment-schedule row statuses.
  pending: { label: 'Pending', variant: 'neutral', dot: 'bg-fg-faint' },
  received: { label: 'Received', variant: 'info', dot: 'bg-primary-500' },
  overdue: { label: 'Overdue', variant: 'danger', dot: 'bg-danger-solid' },
  waived: { label: 'Waived', variant: 'dark', dot: 'bg-fg-faint' },
  // House-account pickup receipt statuses.
  pending_ap_review: { label: 'Pending AP Review', variant: 'warning', dot: 'bg-warning-solid' },
  open: { label: 'Open', variant: 'info', dot: 'bg-primary-500' },
  reconciled: { label: 'Reconciled', variant: 'success', dot: 'bg-success-solid' },
  // Label != value ON PURPOSE, which is the whole reason this table exists.
  // The DB status stays 'voided' — it is written into the ag04 migration, the
  // model, crud's RETIRED tuple, and the partial unique index predicate
  // `status NOT IN ('voided', 'rejected')` — but the people recording receipts
  // told us "Void" told them nothing, so the word they READ is "Removed".
  // Note it is still a soft-cancel, not a row delete (see
  // services/agreementReceipts.ts's `void:` comment).
  voided: { label: 'Removed', variant: 'outline', dot: 'bg-fg-faint' },
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
  const config = STATUS_CONFIG[status] ?? { label: String(status), variant: 'neutral' as BadgeVariant, dot: 'bg-fg-faint' }
  return (
    <Badge variant={config.variant}>
      <span className={cn('size-1.5 rounded-full', config.dot)} />
      {label ?? config.label}
    </Badge>
  )
}
