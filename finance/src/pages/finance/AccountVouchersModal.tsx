/**
 * Voucher drill-down modal (能力③) — the posted JV lines composing one account
 * (+optional cost center) in a period. Each row links into JvDetailModal via
 * onOpenJv. Used by AccountBalancePage and BudgetActualPage.
 *
 * `includeUnposted` mirrors the report's own basis: opened from a report that
 * folds in not-yet-posted vouchers, the drill must show them too, or the lines
 * will not add up to the row that was clicked. Unposted rows are marked, since
 * an unmarked one reads as a voucher that is already in the ledger.
 */
import { useQuery } from '@tanstack/react-query'
import { BookOpen, Loader2 } from 'lucide-react'
import { SortTh, useSortableRows, TBody, THead, Table, Td, Tr, Button, Modal as ShellModal, Badge } from '@uniops/shell'
import { financeApi } from '@/lib/api'
import { cn, formatDate } from '@/lib/utils'

/** The amount a Debit / Credit cell shows; 0 renders blank, so it sorts as empty. */
function shown(v: string | null | undefined) { return Number(v ?? 0) || null }

function money(v: string | null | undefined) {
  const n = Number(v ?? 0)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : ''
}

interface VoucherRow {
  jv_id: string; jv_number: string; voucher_date: string; summary: string | null
  account_code: string; account_name: string | null
  local_debit: string; local_credit: string; posted?: boolean; cost_center_id: string | null
  source_doc_type: string | null; source_doc_id: string | null; source_doc_number: string | null
}
interface VouchersResp { account_code: string; period: string; rows: VoucherRow[] }

export function AccountVouchersModal({ accountCode, period, dimsValues, title,
                                       includeUnposted = false, onClose, onOpenJv }: {
  accountCode: string; period: string; dimsValues?: string | null
  title: string; includeUnposted?: boolean
  onClose: () => void; onOpenJv: (jvId: string) => void
}) {
  const qs = (dimsValues
    ? `?period=${period}&dims_values=${encodeURIComponent(dimsValues)}`
    : `?period=${period}`) + `&include_unposted=${includeUnposted}`
  const { data, isLoading } = useQuery({
    queryKey: ['ab-vouchers', accountCode, period, dimsValues ?? '', includeUnposted],
    queryFn: () => financeApi.get<VouchersResp>(`/gl/account-balance/${accountCode}/vouchers${qs}`),
  })
  const { rows, sort, onSort } = useSortableRows(data?.rows, {
    date: (r) => r.voucher_date, voucher: (r) => r.jv_number,
    account: (r) => `${r.account_code}${r.account_name ? ` · ${r.account_name}` : ''}`,
    summary: (r) => r.summary, debit: (r) => shown(r.local_debit), credit: (r) => shown(r.local_credit),
  })

  return (
    <ShellModal open onClose={onClose} title={title} icon={<BookOpen className="h-4 w-4" />} closeOnBackdrop closeOnEscape={false} size="full" className="max-w-4xl">
      {isLoading ? (
        <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <Table density="compact">
            <THead>
              <Tr>
                <SortTh label="Date" sortKey="date" sort={sort} onSort={onSort} className="px-3 py-2 w-24" />
                <SortTh label="Voucher" sortKey="voucher" sort={sort} onSort={onSort} className="px-3 py-2 w-36" />
                <SortTh label="Account" sortKey="account" sort={sort} onSort={onSort} className="px-3 py-2 w-40" />
                <SortTh label="Summary" sortKey="summary" sort={sort} onSort={onSort} className="px-3 py-2" />
                <SortTh label="Debit" sortKey="debit" sort={sort} onSort={onSort} align="right" className="px-3 py-2 w-28" />
                <SortTh label="Credit" sortKey="credit" sort={sort} onSort={onSort} align="right" className="px-3 py-2 w-28" />
              </Tr>
            </THead>
            <TBody>
              {rows.length === 0 && (
                <Tr><Td align="center" colSpan={6} className="text-fg-muted">No vouchers.</Td></Tr>
              )}
              {rows.map((r, i) => (
                <Tr key={`${r.jv_id}-${i}`} className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40')}>
                  <Td className="whitespace-nowrap font-mono text-xs text-fg-secondary">{formatDate(r.voucher_date)}</Td>
                  <Td>
                    <Button variant="link" onClick={() => onOpenJv(r.jv_id)}
                            className="font-mono text-xs">
                      {r.jv_number}
                    </Button>
                    {r.posted === false && (
                      <Badge variant="warning" size="sm" className="ml-1.5">
                        Unposted
                      </Badge>
                    )}
                  </Td>
                  <Td className="font-mono text-xs text-fg-secondary">
                    {r.account_code}{r.account_name ? ` · ${r.account_name}` : ''}
                  </Td>
                  <Td className="text-fg-secondary">{r.summary || '—'}</Td>
                  <Td align="right" className="font-mono">{money(r.local_debit)}</Td>
                  <Td align="right" className="font-mono">{money(r.local_credit)}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
      )}
    </ShellModal>
  )
}
