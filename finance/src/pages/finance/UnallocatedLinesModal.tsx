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
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { Button, Modal, SortTh, TBody, TFoot, THead, Table, Td, Tr, sortParams, useSort, Badge } from '@uniops/shell'
import { ListPager } from '@/components/ListPager'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'

interface Line {
  jv_id: string; jv_number: string; voucher_date: string | null
  fiscal_period: string; line_no: number; account_code: string
  summary: string | null; debit: string
  department_code: string | null; nc_income_expense: string | null
  missing_cost_centre: boolean; missing_budget_account: boolean
  posted?: boolean
}

const PAGE_SIZE = 100
/** Sort keys of GET /gl/budget-actual/unallocated-lines (budget_rollup.UNALLOCATED_SORT). */
type LineSortKey = 'voucher' | 'account' | 'dept' | 'nc_ie' | 'why' | 'debit' | 'summary'

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
                                        bucket, label, includeUnposted = false,
                                        onClose, onOpenJv }: {
  fiscalYear: number
  includeUnposted?: boolean
  window: [number, number]
  windowLabel: string
  bucket: string
  label: string
  onClose: () => void
  onOpenJv: (jvId: string) => void
}) {
  const [from, to] = win
  // Paged and sorted by the API; the footer total is the API's sum over EVERY
  // matching line, not just the page on screen.
  const [page, setPage] = useState(1)
  const [sort, onSort] = useSort<LineSortKey>(null, () => setPage(1))
  const { sort: sortKey, dir } = sortParams(sort)
  const { data } = useQuery({
    queryKey: ['unallocated-lines', fiscalYear, from, to, bucket, includeUnposted, page, sort],
    queryFn: () => financeApi.get<{ rows: Line[]; total: number; debit_total: string }>(
      `/gl/budget-actual/unallocated-lines?fiscal_year=${fiscalYear}` +
      `&month_from=${from}&month_to=${to}&bucket=${encodeURIComponent(bucket)}` +
      `&limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}` +
      `&include_unposted=${includeUnposted}${sortKey ? `&sort=${sortKey}&dir=${dir}` : ''}`),
  })
  const rows = data?.rows ?? []
  const lineCount = data?.total ?? 0
  const total = Number(data?.debit_total ?? 0)

  // Backdrop click and Escape close, as before.
  return (
    <Modal open onClose={onClose} title={label} closeOnBackdrop size="full" className="max-w-5xl"
      description={<span className="inline-flex items-center gap-2">
        {windowLabel} {fiscalYear} ·{' '}
        {data ? `${lineCount} lines` : <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      </span>}
      bodyClassName="flex min-h-0 flex-col overflow-hidden p-0">
        <div className="flex-1 overflow-auto">
          <Table density="compact">
            <THead className="sticky top-0 z-10">
              <Tr className="bg-surface-subtle/95 text-xs font-medium text-fg-secondary backdrop-blur">
                <SortTh label="Voucher" sortKey="voucher" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
                <SortTh label="Account" sortKey="account" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
                <SortTh label="Dept" sortKey="dept" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
                <SortTh label="NC income/expense" sortKey="nc_ie" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
                <SortTh label="Why" sortKey="why" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
                <SortTh label="Debit" sortKey="debit" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
                <SortTh label="Summary" sortKey="summary" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
              </Tr>
            </THead>
            <TBody>
              {!data && (
                <Tr><Td align="center" colSpan={7}>
                  <Loader2 className="mx-auto h-4 w-4 animate-spin text-fg-muted" /></Td></Tr>
              )}
              {data && rows.length === 0 && (
                <Tr><Td align="center" colSpan={7} className="text-xs text-fg-muted">
                  No lines in this window.</Td></Tr>
              )}
              {rows.map((r, i) => (
                <Tr key={`${r.jv_id}-${r.line_no}`}
                    className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40')}>
                  <Td>
                    <Button variant="link" onClick={() => onOpenJv(r.jv_id)}
                            className="font-mono text-xs">
                      {r.jv_number}
                    </Button>
                    <span className="ml-1 text-xs text-fg-muted">#{r.line_no}</span>
                    {r.posted === false && (
                      <Badge variant="warning" size="sm" className="ml-1.5">
                        Unposted
                      </Badge>
                    )}
                    <div className="text-xs text-fg-muted">{r.fiscal_period}</div>
                  </Td>
                  <Td className="font-mono text-xs">{r.account_code}</Td>
                  <Td className="font-mono text-xs text-fg-secondary">
                    {r.department_code ?? '—'}
                  </Td>
                  <Td className="font-mono text-xs text-fg-secondary">
                    {r.nc_income_expense ?? <span className="text-danger-fg">(none)</span>}
                  </Td>
                  <Td className="text-xs text-fg-secondary">{whyLabel(r)}</Td>
                  <Td align="right" className="font-mono tabular-nums">
                    {Number(r.debit).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </Td>
                  <Td className="max-w-md truncate text-xs text-fg-secondary"
                      title={r.summary ?? undefined}>{r.summary}</Td>
                </Tr>
              ))}
            </TBody>
            {rows.length > 0 && (
              <TFoot className="sticky bottom-0">
                <Tr className="border-t-2 bg-surface-subtle font-semibold">
                  <Td colSpan={5} className="text-xs">
                    Total of all {lineCount} lines
                  </Td>
                  <Td align="right" className="font-mono tabular-nums">
                    {total.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </Td>
                  <Td />
                </Tr>
              </TFoot>
            )}
          </Table>
        </div>
        <ListPager page={page} pageSize={PAGE_SIZE} total={lineCount} noun="lines" onPage={setPage}
                   className="flex items-center justify-between border-t border-line-subtle px-4 py-2 text-sm text-fg-muted" />
    </Modal>
  )
}
