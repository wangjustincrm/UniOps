import { Badge, type BadgeTone } from '@uniops/shell'
import type { DocumentStatus } from '@/types'

// Generic Badge now lives in @uniops/shell; re-export it for existing imports.
export { Badge }

type BadgeVariant = BadgeTone

const STATUS_CONFIG: Record<
  DocumentStatus,
  { label: string; variant: BadgeVariant }
> = {
  draft: { label: 'Draft', variant: 'neutral' },
  submitted: { label: 'Submitted', variant: 'warning' },
  in_review: { label: 'In Review', variant: 'info' },
  approved: { label: 'Approved', variant: 'success' },
  returned: { label: 'Returned', variant: 'warning' },
  rejected: { label: 'Rejected', variant: 'danger' },
  cancelled: { label: 'Cancelled', variant: 'outline' },
  issued: { label: 'Issued', variant: 'success' },
  partially_received: { label: 'Partial Receipt', variant: 'info' },
  fully_received: { label: 'Fully Received', variant: 'success' },
  confirmed: { label: 'Confirmed', variant: 'success' },
  collected: { label: 'Collected', variant: 'success' },
  matched: { label: 'Matched', variant: 'success' },
  paid: { label: 'Paid', variant: 'dark' },
  closed: { label: 'Closed', variant: 'dark' },
  nc_milk: { label: 'Milk / NC', variant: 'info' },
  nc_pending: { label: 'NC Pending Approval', variant: 'warning' },
  // Purchase Agreement — 'active' is the approval-terminal status (not 'approved').
  active: { label: 'Active', variant: 'success' },
  expired: { label: 'Expired', variant: 'warning' },
  // Agreement payment-schedule row statuses.
  pending: { label: 'Pending', variant: 'neutral' },
  received: { label: 'Received', variant: 'info' },
  overdue: { label: 'Overdue', variant: 'danger' },
  waived: { label: 'Waived', variant: 'dark' },
  // House-account pickup receipt statuses.
  pending_ap_review: { label: 'Pending AP Review', variant: 'warning' },
  open: { label: 'Open', variant: 'info' },
  reconciled: { label: 'Reconciled', variant: 'success' },
  // Label != value ON PURPOSE, which is the whole reason this table exists.
  // The DB status stays 'voided' — it is written into the ag04 migration, the
  // model, crud's RETIRED tuple, and the partial unique index predicate
  // `status NOT IN ('voided', 'rejected')` — but the people recording receipts
  // told us "Void" told them nothing, so the word they READ is "Removed".
  // Note it is still a soft-cancel, not a row delete (see
  // services/agreementReceipts.ts's `void:` comment).
  voided: { label: 'Removed', variant: 'outline' },
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
  const config = STATUS_CONFIG[status] ?? { label: String(status), variant: 'neutral' as BadgeVariant }
  return (
    <Badge variant={config.variant} dot>
      {label ?? config.label}
    </Badge>
  )
}
