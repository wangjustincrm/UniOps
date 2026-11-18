// NC Inventory → Ledger (GL). Inventory accounts by material from NC's
// voucher lines as of the date. Lines that carry no material auxiliary are
// kept as their own "(no material)" row, not dropped; 1471 impairment
// provision is shown on its own line and never compared per material.
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Check, ChevronDown, ChevronRight } from 'lucide-react'
import { Badge, SortTh, sortParams, useSort, Checkbox, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
import { cn, formatDate } from '@/lib/utils'
import { formatMoney, formatQty, isNegative } from '@/lib/number'
import { ncApi, type LedgerAccountRow, type LedgerRow, type LedgerSort } from './ncApi'
import { EmptyRow, ErrorBox, LoadingRows, Pager, Uom, tableWrapCls, thCls } from './NcUi'
import { usePageFor } from './usePageFor'
import { ScrollTable, stickyHeadCls } from '@/components/ScrollTable'

const PAGE_SIZE = 50
const LINES_PAGE_SIZE = 100

const num = (v: string | null) => cn('px-3 py-2 text-right font-mono text-xs tabular-nums', isNegative(v) && 'text-danger-fg')

function AccountsTable({ accounts, impairment, showAmt }: {
  accounts: LedgerAccountRow[]
  impairment: LedgerAccountRow[]
  showAmt: boolean
}) {
  const cols = showAmt ? 4 : 3
  return (
    <div className={cn(tableWrapCls, 'sm:max-w-3xl')}>
      <Table density="compact" className="min-w-full">
        <THead className={thCls}>
          <Tr>
            <Th>Account</Th>
            <Th>Name</Th>
            <Th align="right" title="Summed across units of measure">Qty</Th>
            {showAmt && <Th align="right">Amount</Th>}
          </Tr>
        </THead>
        <TBody>
          {accounts.length === 0 && <EmptyRow cols={cols}>No inventory account balances on this date.</EmptyRow>}
          {accounts.map((a) => (
            <Tr key={a.account_code}>
              <Td className="font-mono text-xs">{a.account_code}</Td>
              <Td className="text-xs text-fg-secondary">{a.account_name ?? '—'}</Td>
              <Td className={num(a.qty)}>{formatQty(a.qty)}</Td>
              {showAmt && <Td className={num(a.amount)}>{formatMoney(a.amount)}</Td>}
            </Tr>
          ))}
          {impairment.map((a) => (
            <Tr key={a.account_code} className="border-t-2 bg-surface-subtle/60">
              <Td className="font-mono text-xs">{a.account_code}</Td>
              <Td className="text-xs text-fg-secondary">
                {a.account_name ?? '—'}
                <span className="block text-2xs text-fg-muted">
                  Impairment provision — shown separately, not compared per material.
                </span>
              </Td>
              <Td className={num(a.qty)}>{formatQty(a.qty)}</Td>
              {showAmt && <Td className={num(a.amount)}>{formatMoney(a.amount)}</Td>}
            </Tr>
          ))}
        </TBody>
      </Table>
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
      <div className="max-h-96 overflow-auto rounded-md border border-line bg-surface">
        <Table density="compact" className="min-w-full">
          <THead className={cn(thCls, 'sticky top-0')}>
            <Tr>
              <Th>Year-Period</Th>
              <Th>Voucher #</Th>
              <Th title="As NC recorded it, on NC's clock">Prepared</Th>
              <Th>Posted</Th>
              <Th>Source</Th>
              <Th>Explanation</Th>
              <Th align="right">Qty</Th>
              {showAmt && <Th align="right">Amount</Th>}
            </Tr>
          </THead>
          <TBody>
            {q.isLoading && <LoadingRows cols={cols} rows={4} />}
            {q.data && q.data.items.length === 0 && <EmptyRow cols={cols}>No voucher lines up to this date.</EmptyRow>}
            {q.data?.items.map((l, i) => (
              <Tr key={i}>
                <Td className="font-mono">{l.year}-{l.period}</Td>
                <Td className="font-mono">{l.voucher_num ?? '—'}</Td>
                <Td className="whitespace-nowrap" title={l.prepared ?? undefined}>
                  {l.prepared ? formatDate(l.prepared.slice(0, 10)) : '—'}
                </Td>
                <Td>
                  {l.posted
                    ? <Check className="inline h-3.5 w-3.5 text-success-fg" aria-label="Posted" />
                    : <span className="text-warning-fg" aria-label="Not posted" title="Not posted yet">—</span>}
                </Td>
                <Td className="font-mono">{l.source ?? '—'}</Td>
                <Td className="max-w-md text-fg-secondary">{l.explanation ?? '—'}</Td>
                <Td className={num(l.qty)}>{formatQty(l.qty)}</Td>
                {showAmt && <Td className={num(l.amount)}>{formatMoney(l.amount)}</Td>}
              </Tr>
            ))}
          </TBody>
        </Table>
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
  const [sortState, onSort] = useSort<LedgerSort>()
  const [page, setPage] = usePageFor(JSON.stringify([date, classCode, search, includeUnposted, sortState]))

  const q = useQuery({
    queryKey: ['nc', 'ledger', date, classCode, search, includeUnposted, sortState, page],
    queryFn: () => ncApi.ledger({
      date, class_code: classCode || undefined, search: search || undefined,
      include_unposted: includeUnposted, ...sortParams(sortState), page, page_size: PAGE_SIZE,
    }),
    placeholderData: keepPreviousData,
  })
  const data = q.data
  const showAmt = data?.amounts_visible ?? false
  const cols = showAmt ? 5 : 4
  // The API ignores an amount sort when amounts are hidden (default order).
  const sort = sortState?.key === 'amount' && !showAmt ? null : sortState

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
      <label className="flex items-center gap-2 text-sm text-fg-secondary">
        <Checkbox checked={includeUnposted} onChange={(e) => onIncludeUnposted(e.target.checked)}
          />
        Include unposted vouchers
      </label>

      {q.isError && <ErrorBox error={q.error} fallback="Could not load the NC ledger." />}

      {q.isLoading && (
        <div className={cn(tableWrapCls, 'sm:max-w-3xl')}>
          <Table className="min-w-full"><TBody><LoadingRows cols={3} rows={3} /></TBody></Table>
        </div>
      )}
      {data && <AccountsTable accounts={data.accounts} impairment={data.impairment} showAmt={showAmt} />}
      {data && data.impairment.length === 0 && (
        <p className="text-2xs text-fg-muted">No impairment provision (1471) balance on this date.</p>
      )}

      {!(q.isError && !data) && (
        <ScrollTable className={cn(q.isFetching && data && 'opacity-70')}>
          <Table density="compact" className="min-w-full">
            <THead className={stickyHeadCls}>
              <Tr>
                <Th className="w-8" aria-label="Expand" />
                <SortTh label="Account" sortKey="account" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
                <SortTh label="Material" sortKey="material" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
                <SortTh label="Qty" sortKey="qty" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
                {showAmt && <SortTh label="Amount" sortKey="amount" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />}
              </Tr>
            </THead>
            <TBody>
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
                  <Tr key={key}
                    className={cn('border-t border-line-subtle', drillable && 'cursor-pointer hover:bg-surface-subtle', open && 'bg-surface-subtle')}
                    onClick={drillable ? () => toggle(key) : undefined}>
                    <Td className="w-8 text-fg-muted">
                      {drillable && (
                        <Button variant="subtle" size="icon-xs" type="button" aria-expanded={open} aria-label={open ? 'Hide voucher lines' : 'Show voucher lines'}
                          onClick={(e) => { e.stopPropagation(); toggle(key) }}>
                          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </Button>
                      )}
                    </Td>
                    <Td>
                      <span className="font-mono text-xs">{r.account_code}</span>
                      {r.account_name && <span className="ml-1.5 text-xs text-fg-muted">{r.account_name}</span>}
                    </Td>
                    <Td>
                      {r.material_code ? (
                        <>
                          <span className="font-mono text-xs">{r.material_code}</span>
                          {r.material_name && <span className="ml-1.5 text-xs text-fg-muted">{r.material_name}</span>}
                        </>
                      ) : (
                        <span className="text-xs italic text-fg-muted">
                          {r.material_name != null ? '(no material)' : '(material not in NC mirror)'}
                        </span>
                      )}
                      {r.unposted_lines > 0 && (
                        <Badge variant="warning" className="ml-2 text-2xs">
                          {r.unposted_lines.toLocaleString('en-US')} unposted
                        </Badge>
                      )}
                    </Td>
                    <Td className={num(r.qty)}>{formatQty(r.qty)}<Uom uom={r.uom} /></Td>
                    {showAmt && <Td className={num(r.amount)}>{formatMoney(r.amount)}</Td>}
                  </Tr>,
                  open && (
                    <Tr key={`${key}-lines`} className="bg-surface-subtle/60">
                      <Td colSpan={cols}>
                        <VoucherLines row={r} date={date} includeUnposted={includeUnposted} />
                      </Td>
                    </Tr>
                  ),
                ]
              })}
            </TBody>
          </Table>
        </ScrollTable>
      )}

      <Pager page={page} pageSize={PAGE_SIZE} total={data?.total ?? 0} busy={q.isFetching} onPage={setPage} />
    </div>
  )
}
