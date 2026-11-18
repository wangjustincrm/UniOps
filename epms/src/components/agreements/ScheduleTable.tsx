import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { formatAmount, formatDate, formatDateTime } from '@/lib/utils'
import { useConfirmPeriod } from '@/hooks/useAgreements'
import { errorMessage } from '@/lib/submitFailure'
import type { ApiScheduleRow } from '@/services/agreement'
import type { ApiTask } from '@/services/tasks'
import type { ApiUserBrief } from '@/services/users'

import { TBody, THead, Table, Td, Th, Tr } from '@uniops/shell'
interface ScheduleTableProps {
  scheduleType: 'period' | 'milestone'
  rows: ApiScheduleRow[]
  agreementId: string
  // Needed to reconstruct the confirm_period task's document_number
  // (`${agr.number} · ${row.period_label}` — epms-api/app/crud/agreement_schedule.py
  // create_confirm_task/confirm_period) so we can tell which OPEN task, if any,
  // belongs to THIS row rather than some other period on the same agreement.
  agreementNumber: string
  currency: string
  // Caller's open tasks, unfiltered by type — filtered here to confirm_period
  // rows for this agreement. Same open-task convention as the Approve button
  // elsewhere on this page (useTasks({ is_completed: false })).
  myOpenTasks: ApiTask[]
  // Full user directory (GET /users/directory — open to any authenticated
  // user, unlike system_admin-only GET /users) for resolving accepted_by (a
  // UUID) to a display name. undefined only while the query hasn't settled
  // yet — resolveUserName() degrades to showing the timestamp alone rather
  // than ever rendering a bare UUID if a specific id still doesn't resolve
  // (e.g. a deactivated user dropped from the directory).
  users: ApiUserBrief[] | undefined
}

function resolveUserName(users: ApiUserBrief[] | undefined, id: string | null): string | undefined {
  if (!id || !users) return undefined
  return users.find((u) => u.id === id)?.full_name
}

// One period can hold several invoices (a vendor billing two annual fees in the
// same month) — list every one, plus the period total when there is more than
// one, so whoever clicks Confirm sees exactly what they are confirming.
function PeriodInvoices({ row, currency }: { row: ApiScheduleRow; currency: string }) {
  const invoices = row.invoices ?? []
  if (invoices.length === 0) return <span className="text-fg-faint">—</span>
  // Pre-tax, like the expected / stage amount in the same row (ISS-0128).
  const total = invoices.reduce((sum, i) => sum + Number(i.amount), 0)
  return (
    <div className="flex flex-col gap-0.5">
      {invoices.map((i) => (
        <Link
          key={i.id}
          to={`/invoices/${i.id}`}
          className="inline-flex items-center gap-1.5 text-xs text-primary-700 hover:text-primary-900"
        >
          <span className="font-medium">{i.internal_ref ?? 'Invoice'}</span>
          <span className="amount text-fg-muted" title="Pre-tax">{formatAmount(Number(i.amount), currency)}</span>
        </Link>
      ))}
      {invoices.length > 1 && (
        <span className="text-2xs text-fg-muted">
          {invoices.length} invoices · <span className="amount">{formatAmount(total, currency)}</span>
        </span>
      )}
    </div>
  )
}

export function ScheduleTable({
  scheduleType, rows, agreementId, agreementNumber, currency, myOpenTasks, users,
}: ScheduleTableProps) {
  // Called unconditionally regardless of scheduleType — milestone tables never
  // render the Confirm button so the mutation just sits unused, but React's
  // rules of hooks forbid calling it only on some renders.
  const confirmPeriod = useConfirmPeriod(agreementId)
  // Tracks which row's button reads "Working…" — confirmPeriod.isPending alone
  // is shared by every row's button (one mutation object), so without this every
  // Confirm button on the table would flip to "Working…" when only one was clicked.
  const [pendingRowId, setPendingRowId] = useState<string | null>(null)

  const handleConfirm = (rowId: string) => {
    setPendingRowId(rowId)
    confirmPeriod.reset()
    confirmPeriod.mutate(rowId, { onSettled: () => setPendingRowId(null) })
  }

  if (rows.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-fg-muted">
        No {scheduleType === 'period' ? 'periods' : 'stages'} generated yet.
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-line overflow-hidden">
      {/* A refused Confirm, shown above the table (was a browser alert). */}
      {confirmPeriod.isError && (
        <p role="alert" className="border-b border-danger-line bg-danger-soft px-4 py-2 text-sm text-danger-fg">
          {errorMessage(confirmPeriod.error, 'Failed to confirm the period')}
        </p>
      )}
      <div className="overflow-x-auto">
        <Table density="compact">
          <THead>
            <Tr className="bg-surface-subtle">
              <Th>#</Th>
              {scheduleType === 'period' ? (
                <>
                  <Th>Period</Th>
                  <Th>Expected date</Th>
                  <Th align="right">Expected amount</Th>
                  <Th>Status</Th>
                  <Th>Invoices</Th>
                  <Th>Confirmed</Th>
                </>
              ) : (
                <>
                  <Th>Stage</Th>
                  <Th>Timing</Th>
                  <Th align="right">Expected amount</Th>
                  <Th align="right">% of NTE</Th>
                  <Th>Status</Th>
                  <Th>Invoice</Th>
                </>
              )}
            </Tr>
          </THead>
          <TBody>
            {scheduleType === 'period'
              ? rows.map((row) => {
                  const docNumber = `${agreementNumber} · ${row.period_label ?? ''}`
                  const holdsTask = myOpenTasks.some(
                    (t) => t.type === 'confirm_period' && t.document_id === agreementId && t.document_number === docNumber
                  )
                  const canConfirm = row.status === 'received' && !row.accepted_at && holdsTask
                  const confirmedName = resolveUserName(users, row.accepted_by)
                  return (
                    <Tr key={row.id} className="bg-surface">
                      <Td className="text-fg-muted">{row.sequence}</Td>
                      <Td className="text-fg">{row.period_label ?? '—'}</Td>
                      <Td className="text-xs text-fg-muted">{formatDate(row.expected_date)}</Td>
                      <Td align="right" className="amount font-medium text-fg">
                        {row.expected_amount !== null ? formatAmount(Number(row.expected_amount), currency) : '—'}
                      </Td>
                      <Td><StatusBadge status={row.status} /></Td>
                      <Td>
                        <PeriodInvoices row={row} currency={currency} />
                      </Td>
                      <Td>
                        {row.accepted_at ? (
                          <div className="flex flex-col">
                            {confirmedName && <span className="text-fg">{confirmedName}</span>}
                            <span className={confirmedName ? 'text-xs text-fg-muted' : 'text-fg-secondary'}>
                              {formatDateTime(row.accepted_at)}
                            </span>
                          </div>
                        ) : canConfirm ? (
                          <Button size="sm" onClick={() => handleConfirm(row.id)} disabled={confirmPeriod.isPending}>
                            {confirmPeriod.isPending && pendingRowId === row.id ? 'Working…' : 'Confirm'}
                          </Button>
                        ) : (
                          <span className="text-fg-faint">—</span>
                        )}
                      </Td>
                    </Tr>
                  )
                })
              : rows.map((row) => (
                  <Tr key={row.id} className="bg-surface">
                    <Td className="text-fg-muted">{row.sequence}</Td>
                    <Td className="text-fg">{row.milestone_name ?? '—'}</Td>
                    <Td className="text-xs text-fg-muted">{row.expected_timing ?? '—'}</Td>
                    <Td align="right" className="amount font-medium text-fg">
                      {row.expected_amount !== null ? formatAmount(Number(row.expected_amount), currency) : '—'}
                    </Td>
                    <Td align="right" className="text-fg-secondary">
                      {row.amount_pct !== null ? `${Number(row.amount_pct).toFixed(2)}%` : '—'}
                    </Td>
                    <Td><StatusBadge status={row.status} /></Td>
                    <Td>
                      {row.invoice_id ? (
                        <Link
                          to={`/invoices/${row.invoice_id}`}
                          className="inline-flex items-center gap-1 text-xs font-medium text-primary-700 hover:text-primary-900"
                        >
                          View <ExternalLink className="h-3 w-3" />
                        </Link>
                      ) : (
                        <span className="text-fg-faint">—</span>
                      )}
                    </Td>
                  </Tr>
                ))}
          </TBody>
        </Table>
      </div>
    </div>
  )
}
