import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { formatAmount, formatDate, formatDateTime } from '@/lib/utils'
import { useConfirmPeriod } from '@/hooks/useAgreements'
import type { ApiScheduleRow } from '@/services/agreement'
import type { ApiTask } from '@/services/tasks'
import type { ApiUser } from '@/services/users'

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
  // Full user list for resolving accepted_by (a UUID) to a display name.
  // undefined when the list hasn't loaded or the caller isn't allowed to fetch
  // it (GET /users is system_admin-only) — resolveName() degrades to showing
  // the timestamp alone rather than ever rendering a bare UUID.
  users: ApiUser[] | undefined
}

function resolveUserName(users: ApiUser[] | undefined, id: string | null): string | undefined {
  if (!id || !users) return undefined
  return users.find((u) => u.id === id)?.full_name
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
    confirmPeriod.mutate(rowId, { onSettled: () => setPendingRowId(null) })
  }

  if (rows.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-neutral-400">
        No {scheduleType === 'period' ? 'periods' : 'stages'} generated yet.
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-neutral-200 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-200 bg-neutral-50">
              <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">#</th>
              {scheduleType === 'period' ? (
                <>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Period</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Expected date</th>
                  <th className="px-4 py-2.5 text-right text-xs font-semibold text-neutral-500">Expected amount</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Status</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Confirmed</th>
                </>
              ) : (
                <>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Stage</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Timing</th>
                  <th className="px-4 py-2.5 text-right text-xs font-semibold text-neutral-500">Expected amount</th>
                  <th className="px-4 py-2.5 text-right text-xs font-semibold text-neutral-500">% of NTE</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Status</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-neutral-500">Invoice</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {scheduleType === 'period'
              ? rows.map((row) => {
                  const docNumber = `${agreementNumber} · ${row.period_label ?? ''}`
                  const holdsTask = myOpenTasks.some(
                    (t) => t.type === 'confirm_period' && t.document_id === agreementId && t.document_number === docNumber
                  )
                  const canConfirm = row.status === 'received' && !row.accepted_at && holdsTask
                  const confirmedName = resolveUserName(users, row.accepted_by)
                  return (
                    <tr key={row.id} className="border-b border-neutral-100 last:border-0 bg-white">
                      <td className="px-4 py-2.5 text-neutral-500">{row.sequence}</td>
                      <td className="px-4 py-2.5 text-neutral-900">{row.period_label ?? '—'}</td>
                      <td className="px-4 py-2.5 text-xs text-neutral-500">{formatDate(row.expected_date)}</td>
                      <td className="px-4 py-2.5 text-right amount font-medium text-neutral-900">
                        {row.expected_amount !== null ? formatAmount(Number(row.expected_amount), currency) : '—'}
                      </td>
                      <td className="px-4 py-2.5"><StatusBadge status={row.status} /></td>
                      <td className="px-4 py-2.5">
                        {row.accepted_at ? (
                          <div className="flex flex-col">
                            {confirmedName && <span className="text-neutral-900">{confirmedName}</span>}
                            <span className={confirmedName ? 'text-xs text-neutral-400' : 'text-neutral-700'}>
                              {formatDateTime(row.accepted_at)}
                            </span>
                          </div>
                        ) : canConfirm ? (
                          <Button size="sm" onClick={() => handleConfirm(row.id)} disabled={confirmPeriod.isPending}>
                            {confirmPeriod.isPending && pendingRowId === row.id ? 'Working…' : 'Confirm'}
                          </Button>
                        ) : (
                          <span className="text-neutral-300">—</span>
                        )}
                      </td>
                    </tr>
                  )
                })
              : rows.map((row) => (
                  <tr key={row.id} className="border-b border-neutral-100 last:border-0 bg-white">
                    <td className="px-4 py-2.5 text-neutral-500">{row.sequence}</td>
                    <td className="px-4 py-2.5 text-neutral-900">{row.milestone_name ?? '—'}</td>
                    <td className="px-4 py-2.5 text-xs text-neutral-500">{row.expected_timing ?? '—'}</td>
                    <td className="px-4 py-2.5 text-right amount font-medium text-neutral-900">
                      {row.expected_amount !== null ? formatAmount(Number(row.expected_amount), currency) : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right text-neutral-600">
                      {row.amount_pct !== null ? `${Number(row.amount_pct).toFixed(2)}%` : '—'}
                    </td>
                    <td className="px-4 py-2.5"><StatusBadge status={row.status} /></td>
                    <td className="px-4 py-2.5">
                      {row.invoice_id ? (
                        <Link
                          to={`/invoices/${row.invoice_id}`}
                          className="inline-flex items-center gap-1 text-xs font-medium text-primary-700 hover:text-primary-900"
                        >
                          View <ExternalLink className="h-3 w-3" />
                        </Link>
                      ) : (
                        <span className="text-neutral-300">—</span>
                      )}
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
