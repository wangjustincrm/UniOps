// NC Inventory → Ledger (GL). Inventory accounts by material from NC's
// voucher lines as of the date. Lines that carry no material auxiliary are
// kept as their own "(no material)" row, not dropped; 1471 impairment
// provision is shown on its own line and never compared per material.
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Badge } from '@uniops/shell'
import { cn } from '@/lib/utils'
import { formatMoney, formatQty, isNegative } from '@/lib/number'
import { ncApi, type LedgerAccountRow, type LedgerRow } from './ncApi'
import { EmptyRow, ErrorBox, LoadingRows, Pager, Uom, tableWrapCls, thCls } from './NcUi'
import { usePageFor } from './usePageFor'

const PAGE_SIZE = 50
const LINES_PAGE_SIZE = 100

const num = (v: string | null) => cn('px-3 py-2 text-right font-mono text-xs tabular-nums', isNegative(v) && 'text-danger-600')

function AccountsTable({ accounts, impairment, showAmt }: {
  accounts: LedgerAccountRow[]
  impairment: LedgerAccountRow[]
  showAmt: boolean
}) {
  const cols = showAmt ? 4 : 3
  return (
    <div className={cn(tableWrapCls, 'sm:max-w-3xl')}>
      <table className="min-w-full text-sm">
        <thead className={thCls}>
          <tr>
            <th className="px-3 py-2 text-left">Account</th>
            <th className="px-3 py-2 text-left">Name</th>
            <th className="px-3 py-2 text-right" title="Summed across units of measure">Qty</th>
            {showAmt && <th className="px-3 py-2 text-right">Amount</th>}
          </tr>
        </thead>
        <tbody>
          {accounts.length === 0 && <EmptyRow cols={cols}>No inventory account balances on this date.</EmptyRow>}
          {accounts.map((a) => (
            <tr key={a.account_code} className="border-t border-neutral-100">
              <td className="px-3 py-2 font-mono text-xs">{a.account_code}</td>
              <td className="px-3 py-2 text-xs text-neutral-600">{a.account_name ?? '—'}</td>
              <td className={num(a.qty)}>{formatQty(a.qty)}</td>
              {showAmt && <td className={num(a.amount)}>{formatMoney(a.amount)}</td>}
            </tr>
          ))}
          {impairment.map((a) => (
            <tr key={a.account_code} className="border-t-2 border-neutral-200 bg-neutral-50/60">
              <td className="px-3 py-2 font-mono text-xs">{a.account_code}</td>
              <td className="px-3 py-2 text-xs text-neutral-600">
                {a.account_name ?? '—'}
                <span className="block text-[11px] text-neutral-400">
                  Impairment provision — shown separately, not compared per material.
                </span>
              </td>
              <td className={num(a.qty)}>{formatQty(a.qty)}</td>
              {showAmt && <td className={num(a.amount)}>{formatMoney(a.amount)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function VoucherLines({ row, date, includeUnposted }: { row: LedgerRow; date: string; includeUnposted: boolean }) {
  const [page, setPage] = usePageFor(`${date}|${includeUnposted}`)
  const q = useQuery({
    queryKey: ['nc', 'ledger-lines', row.account_code, row.material_code, row.material_pk, date, includeUnposted, page],
    queryFn: () => ncApi.ledgerLines({
      account_code: row.account_code, material_code: row.material_code,
      // A material missing from the NC mirror has no code: drill by its pk so
      // its lines are not confused with lines carrying no material at all.
      material_pk: row.material_code == null ? row.material_pk : null, date,
      include_unposted: includeUnposted, page, page_size: LINES_PAGE_SIZE,
    }),
    placeholderData: keepPreviousData,
  })
  const showAmt = q.data?.amounts_visible ?? false
  const cols = showAmt ? 8 : 7
  if (q.isError) return <ErrorBox error={q.error} fallback="Could not load the voucher lines." />
  return (
    <div className="flex flex-col gap-1.5">
      <div className="max-h-96 overflow-auto rounded-md border border-neutral-200 bg-white">
        <table className="min-w-full text-xs">
          <thead className={cn(thCls, 'sticky top-0')}>
            <tr>
              <th className="px-3 py-1.5 text-left">Year-Period</th>
              <th className="px-3 py-1.5 text-left">Voucher #</th>
              <th className="px-3 py-1.5 text-left" title="As NC recorded it, on NC's clock">Prepared</th>
              <th className="px-3 py-1.5 text-left">Posted</th>
              <th className="px-3 py-1.5 text-left">Source</th>
              <th className="px-3 py-1.5 text-left">Explanation</th>
              <th className="px-3 py-1.5 text-right">Qty</th>
              {showAmt && <th className="px-3 py-1.5 text-right">Amount</th>}
            </tr>
          </thead>
          <tbody>
            {q.isLoading && <LoadingRows cols={cols} rows={4} />}
            {q.data && q.data.items.length === 0 && <EmptyRow cols={cols}>No voucher lines up to this date.</EmptyRow>}
            {q.data?.items.map((l, i) => (
              <tr key={i} className="border-t border-neutral-100">
                <td className="px-3 py-1.5 font-mono">{l.year}-{l.period}</td>
                <td className="px-3 py-1.5 font-mono">{l.voucher_num ?? '—'}</td>
                <td className="px-3 py-1.5 whitespace-nowrap" title={l.prepared ?? undefined}>
                  {l.prepared ? l.prepared.slice(0, 10) : '—'}
                </td>
                <td className="px-3 py-1.5">
                  {l.posted
                    ? <span className="text-success-700" aria-label="Posted">✓</span>
                    : <span className="text-warning-600" aria-label="Not posted" title="Not posted yet">—</span>}
                </td>
                <td className="px-3 py-1.5 font-mono">{l.source ?? '—'}</td>
                <td className="max-w-md px-3 py-1.5 text-neutral-600">{l.explanation ?? '—'}</td>
                <td className={num(l.qty)}>{formatQty(l.qty)}</td>
                {showAmt && <td className={num(l.amount)}>{formatMoney(l.amount)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {q.data && q.data.total > LINES_PAGE_SIZE && (
        <Pager page={page} pageSize={LINES_PAGE_SIZE} total={q.data.total} busy={q.isFetching} onPage={setPage} />
      )}
    </div>
  )
}

function rowKey(r: LedgerRow, i: number): string {
  const mat = r.material_code ?? (r.material_name != null ? '(none)' : `(unknown ${i})`)
  return `${r.account_code}|${mat}`
}

export function LedgerTab({ date, classCode, search, includeUnposted, onIncludeUnposted }: {
  date: string
  classCode: string
  search: string
  includeUnposted: boolean
  onIncludeUnposted: (v: boolean) => void
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [page, setPage] = usePageFor(JSON.stringify([date, classCode, search, includeUnposted]))

  const q = useQuery({
    queryKey: ['nc', 'ledger', date, classCode, search, includeUnposted, page],
    queryFn: () => ncApi.ledger({
      date, class_code: classCode || undefined, search: search || undefined,
      include_unposted: includeUnposted, page, page_size: PAGE_SIZE,
    }),
    placeholderData: keepPreviousData,
  })
  const data = q.data
  const showAmt = data?.amounts_visible ?? false
  const cols = showAmt ? 5 : 4

  function toggle(key: string) {
    setExpanded((cur) => {
      const next = new Set(cur)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <div className="flex flex-col gap-3">
      <label className="flex items-center gap-2 text-sm text-neutral-700">
        <input type="checkbox" checked={includeUnposted} onChange={(e) => onIncludeUnposted(e.target.checked)}
          className="h-4 w-4 rounded border-neutral-300" />
        Include unposted vouchers
      </label>

      {q.isError && <ErrorBox error={q.error} fallback="Could not load the NC ledger." />}

      {q.isLoading && (
        <div className={cn(tableWrapCls, 'sm:max-w-3xl')}>
          <table className="min-w-full text-sm"><tbody><LoadingRows cols={3} rows={3} /></tbody></table>
        </div>
      )}
      {data && <AccountsTable accounts={data.accounts} impairment={data.impairment} showAmt={showAmt} />}
      {data && data.impairment.length === 0 && (
        <p className="text-[11px] text-neutral-400">No impairment provision (1471) balance on this date.</p>
      )}

      {!(q.isError && !data) && (
        <div className={cn(tableWrapCls, q.isFetching && data && 'opacity-70')}>
          <table className="min-w-full text-sm">
            <thead className={thCls}>
              <tr>
                <th className="w-8 px-2 py-2" aria-label="Expand" />
                <th className="px-3 py-2 text-left">Account</th>
                <th className="px-3 py-2 text-left">Material</th>
                <th className="px-3 py-2 text-right">Qty</th>
                {showAmt && <th className="px-3 py-2 text-right">Amount</th>}
              </tr>
            </thead>
            <tbody>
              {q.isLoading && <LoadingRows cols={cols} />}
              {data && data.items.length === 0 && (
                <EmptyRow cols={cols}>{search ? `Nothing matches "${search}".` : 'No ledger balances on this date for these filters.'}</EmptyRow>
              )}
              {data?.items.map((r, i) => {
                const key = rowKey(r, i)
                // Every row drills: by code, by pk (material not in the NC
                // mirror), or as the lines carrying no material auxiliary.
                const drillable = true
                const open = drillable && expanded.has(key)
                return [
                  <tr key={key}
                    className={cn('border-t border-neutral-100', drillable && 'cursor-pointer hover:bg-neutral-50', open && 'bg-neutral-50')}
                    onClick={drillable ? () => toggle(key) : undefined}>
                    <td className="w-8 px-2 py-2 text-neutral-400">
                      {drillable && (
                        <button type="button" aria-expanded={open} aria-label={open ? 'Hide voucher lines' : 'Show voucher lines'}
                          onClick={(e) => { e.stopPropagation(); toggle(key) }}>
                          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <span className="font-mono text-xs">{r.account_code}</span>
                      {r.account_name && <span className="ml-1.5 text-xs text-neutral-500">{r.account_name}</span>}
                    </td>
                    <td className="px-3 py-2">
                      {r.material_code ? (
                        <>
                          <span className="font-mono text-xs">{r.material_code}</span>
                          {r.material_name && <span className="ml-1.5 text-xs text-neutral-500">{r.material_name}</span>}
                        </>
                      ) : (
                        <span className="text-xs italic text-neutral-400">
                          {r.material_name != null ? '(no material)' : '(material not in NC mirror)'}
                        </span>
                      )}
                      {r.unposted_lines > 0 && (
                        <Badge variant="warning" className="ml-2 text-[10px]">
                          {r.unposted_lines.toLocaleString('en-US')} unposted
                        </Badge>
                      )}
                    </td>
                    <td className={num(r.qty)}>{formatQty(r.qty)}<Uom uom={r.uom} /></td>
                    {showAmt && <td className={num(r.amount)}>{formatMoney(r.amount)}</td>}
                  </tr>,
                  open && (
                    <tr key={`${key}-lines`} className="border-t border-neutral-100 bg-neutral-50/60">
                      <td colSpan={cols} className="px-4 py-3">
                        <VoucherLines row={r} date={date} includeUnposted={includeUnposted} />
                      </td>
                    </tr>
                  ),
                ]
              })}
            </tbody>
          </table>
        </div>
      )}

      <Pager page={page} pageSize={PAGE_SIZE} total={data?.total ?? 0} busy={q.isFetching} onPage={setPage} />
    </div>
  )
}
