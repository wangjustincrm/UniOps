/**
 * The voucher lines behind one bucket of the roll-up's unallocated row.
 *
 * The `__unplaced__` bucket is the one that earns this: every line in it is
 * money nobody decided where to put, and a bare total is unactionable until
 * you can see which vouchers, which department and — the actual diagnosis —
 * what NC wrote in the income-expense slot.
 *
 * That bucket holds two different faults: a line with no cost centre, and a
 * line with no budget account. The Why column says which one put each line
 * here, because the bucket's single label cannot: a reader who sees a
 * cost-centre complaint against a line that plainly has one concludes the
 * report is broken.
 */
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Loader2, X } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'

interface Line {
  jv_id: string; jv_number: string; voucher_date: string | null
  fiscal_period: string; line_no: number; account_code: string
  summary: string | null; debit: string
  department_code: string | null; nc_income_expense: string | null
  missing_cost_centre: boolean; missing_budget_account: boolean
}

const LIMIT = 300

/**
 * Which of the bucket's two faults put this line here.
 *
 * Both flags false happens only for a policy bucket (CRM004/007/09912 and the
 * like), where the line is excluded by policy and is neither missing. Say so
 * rather than leaving the cell blank, which reads as "nobody checked".
 */
function whyLabel(r: Line): string {
  if (r.missing_cost_centre && r.missing_budget_account) return 'No cost centre, no budget account'
  if (r.missing_cost_centre) return 'No cost centre'
  if (r.missing_budget_account) return 'No budget account'
  return 'Excluded by policy'
}

export function UnallocatedLinesModal({ fiscalYear, window: win, windowLabel,
                                        bucket, label, onClose, onOpenJv }: {
  fiscalYear: number
  window: [number, number]
  windowLabel: string
  bucket: string
  label: string
  onClose: () => void
  onOpenJv: (jvId: string) => void
}) {
  const [from, to] = win
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const { data } = useQuery({
    queryKey: ['unallocated-lines', fiscalYear, from, to, bucket],
    queryFn: () => financeApi.get<{ rows: Line[] }>(
      `/gl/budget-actual/unallocated-lines?fiscal_year=${fiscalYear}` +
      `&month_from=${from}&month_to=${to}&bucket=${encodeURIComponent(bucket)}&limit=${LIMIT}`),
  })
  const rows = data?.rows ?? []
  const total = rows.reduce((s, r) => s + Number(r.debit), 0)

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 p-6"
         onClick={onClose}>
      <div className="flex max-h-[85vh] w-full max-w-5xl flex-col rounded-xl bg-white shadow-2xl"
           role="dialog" aria-label={`Lines behind ${label}`}
           onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center gap-2 border-b border-neutral-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-neutral-900">{label}</h2>
          <span className="text-xs text-neutral-500">{windowLabel} {fiscalYear}</span>
          <span className="ml-auto flex items-center gap-2 text-xs text-neutral-400">
            {rows.length >= LIMIT && <span>first {LIMIT}</span>}
            {data ? `${rows.length} lines` : <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            <button onClick={onClose} aria-label="Close"
                    className="cursor-pointer rounded-md p-1.5 text-neutral-500 transition-colors hover:bg-neutral-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
              <X className="h-4 w-4" />
            </button>
          </span>
        </header>

        <div className="flex-1 overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10">
              <tr className="border-b border-neutral-200 bg-neutral-50/95 text-xs font-medium text-neutral-600 backdrop-blur">
                <th className="px-3 py-2 text-left">Voucher</th>
                <th className="px-3 py-2 text-left">Account</th>
                <th className="px-3 py-2 text-left">Dept</th>
                <th className="px-3 py-2 text-left">NC income/expense</th>
                <th className="px-3 py-2 text-left">Why</th>
                <th className="px-3 py-2 text-right">Debit</th>
                <th className="px-3 py-2 text-left">Summary</th>
              </tr>
            </thead>
            <tbody>
              {!data && (
                <tr><td colSpan={7} className="px-3 py-8 text-center">
                  <Loader2 className="mx-auto h-4 w-4 animate-spin text-neutral-400" /></td></tr>
              )}
              {data && rows.length === 0 && (
                <tr><td colSpan={7} className="px-3 py-8 text-center text-xs text-neutral-400">
                  No lines in this window.</td></tr>
              )}
              {rows.map((r, i) => (
                <tr key={`${r.jv_id}-${r.line_no}`}
                    className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                  <td className="px-3 py-1.5">
                    <button onClick={() => onOpenJv(r.jv_id)}
                            className="cursor-pointer font-mono text-xs text-[#085E5E] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                      {r.jv_number}
                    </button>
                    <span className="ml-1 text-xs text-neutral-400">#{r.line_no}</span>
                    <div className="text-xs text-neutral-400">{r.fiscal_period}</div>
                  </td>
                  <td className="px-3 py-1.5 font-mono text-xs">{r.account_code}</td>
                  <td className="px-3 py-1.5 font-mono text-xs text-neutral-600">
                    {r.department_code ?? '—'}
                  </td>
                  <td className="px-3 py-1.5 font-mono text-xs text-neutral-600">
                    {r.nc_income_expense ?? <span className="text-danger-600">(none)</span>}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-neutral-600">{whyLabel(r)}</td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums">
                    {Number(r.debit).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </td>
                  <td className="max-w-md truncate px-3 py-1.5 text-xs text-neutral-600"
                      title={r.summary ?? undefined}>{r.summary}</td>
                </tr>
              ))}
            </tbody>
            {rows.length > 0 && (
              <tfoot className="sticky bottom-0">
                <tr className="border-t-2 border-neutral-300 bg-neutral-50 font-semibold">
                  <td colSpan={5} className="px-3 py-2 text-xs">
                    Total of the lines listed
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">
                    {total.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </td>
                  <td />
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </div>
    </div>,
    document.body,
  )
}
