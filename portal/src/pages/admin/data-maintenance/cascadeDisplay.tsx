import type { CascadeSummary } from '@/services/adminApi'

// Not everything a cascade reports is a deletion. Listing "invoices_unlinked 1"
// under "the following will be removed" is how an admin ends up believing the
// action destroys a financial record when it only withdraws a link — so the
// three kinds are named and shown apart. Shared by the single-record and bulk
// confirm dialogs so they can never describe the same summary differently.
export const KEPT_LABELS: Record<string, string> = {
  invoices_unlinked: 'Invoices unlinked from this receipt — kept, with their PO match and status',
  invoice_claims_released: 'Receipt claims released back to their invoice',
  create_pa_tasks_reopened: 'Create-PA tasks reopened',
  po_lines_received_qty_resynced: 'PO lines whose received quantity is recalculated',
}

export const BLOCKED_LABELS: Record<string, string> = {
  blocked_by_invoices: 'Invoices still reference this record',
  blocked_by_payment_applications: 'Payment applications still reference this record',
  blocked_by_agreement_invoices: 'Invoice is matched to an agreement',
  blocked_by_in_payment_invoices: 'Invoice has already entered payment',
  blocked_by_claimed_by_pa_invoices: 'Invoice is claimed by a live payment application',
}

const humanize = (key: string) => key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())

export interface CascadeParts {
  removed: [string, number][]
  kept: [string, number][]
  blocked: [string, number][]
}

export function splitCascade(cascade: CascadeSummary): CascadeParts {
  const parts: CascadeParts = { removed: [], kept: [], blocked: [] }
  for (const [key, n] of Object.entries(cascade)) {
    if (key.startsWith('blocked_by_')) parts.blocked.push([key, n])
    else if (key in KEPT_LABELS) parts.kept.push([key, n])
    else parts.removed.push([key, n])
  }
  return parts
}

function Rows({ entries, labels, tone }: {
  entries: [string, number][]
  labels?: Record<string, string>
  tone: string
}) {
  return (
    <ul className="space-y-1">
      {entries.map(([key, n]) => (
        <li key={key} className="flex justify-between gap-3">
          <span className="text-neutral-700">{labels?.[key] ?? humanize(key)}</span>
          <span className={`shrink-0 font-semibold ${tone}`}>{n}</span>
        </li>
      ))}
    </ul>
  )
}

export function CascadeBreakdown({ parts, removedLabel }: {
  parts: CascadeParts
  removedLabel: string
}) {
  return (
    <div className="space-y-3">
      {parts.blocked.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-semibold uppercase text-red-700">Blocked</p>
          <Rows entries={parts.blocked} labels={BLOCKED_LABELS} tone="text-red-700" />
          <p className="mt-1 text-xs text-neutral-500">
            Resolve this first — deleting would pull the evidence out from under it.
          </p>
        </div>
      )}
      {parts.removed.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-semibold uppercase text-neutral-500">{removedLabel}</p>
          <Rows entries={parts.removed} tone="text-red-700" />
        </div>
      )}
      {parts.kept.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-semibold uppercase text-neutral-500">Changed, not deleted</p>
          <Rows entries={parts.kept} labels={KEPT_LABELS} tone="text-neutral-700" />
        </div>
      )}
    </div>
  )
}
