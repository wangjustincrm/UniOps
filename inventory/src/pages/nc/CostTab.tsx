// NC Inventory → Cost (IA). One row per material: quantity and value from
// NC's inventory accounting ledger as of the date. IA costs at material level,
// so the warehouse/batch breakdown under a row carries quantities only.
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Badge, SortTh, sortParams, useSort } from '@uniops/shell'
import { cn } from '@/lib/utils'
import { formatMoney, formatQty, formatUnitPrice, isNegative } from '@/lib/number'
import { ncApi, type CostRow, type CostSort } from './ncApi'
import { EmptyRow, ErrorBox, LoadingRows, MaterialCell, Pager, Uom, thCls } from './NcUi'
import { usePageFor } from './usePageFor'
import { ScrollTable, stickyHeadCls } from '@/components/ScrollTable'

const PAGE_SIZE = 50
const LINES_PAGE_SIZE = 100

const numCls = 'px-3 py-1.5 text-right font-mono tabular-nums'

function Breakdown({ code, date, uom }: { code: string; date: string; uom: string | null }) {
  const q = useQuery({
    queryKey: ['nc', 'cost-breakdown', code, date],
    queryFn: () => ncApi.costBreakdown(code, date),
  })
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-xs font-semibold text-neutral-700">Quantity by warehouse and batch</h3>
      <p className="text-[11px] text-neutral-400">IA costs at material level only — amounts are not split by batch.</p>
      {q.isError ? <ErrorBox error={q.error} fallback="Could not load the breakdown." /> : (
        <div className="max-h-72 overflow-auto rounded-md border border-neutral-200 bg-white">
          <table className="min-w-full text-xs">
            <thead className={thCls}>
              <tr>
                <th className="px-3 py-1.5 text-left">Warehouse</th>
                <th className="px-3 py-1.5 text-left">Batch</th>
                <th className="px-3 py-1.5 text-right">Qty</th>
              </tr>
            </thead>
            <tbody>
              {q.isLoading && <LoadingRows cols={3} rows={3} />}
              {q.data && q.data.items.length === 0 && <EmptyRow cols={3}>No quantity on this date.</EmptyRow>}
              {q.data?.items.map((r, i) => (
                <tr key={`${r.warehouse_code}|${r.batch}|${i}`} className="border-t border-neutral-100">
                  <td className="px-3 py-1.5 font-mono">{r.warehouse_code ?? '—'}</td>
                  <td className="px-3 py-1.5 font-mono">{r.batch ?? <span className="font-sans text-neutral-400">no batch</span>}</td>
                  <td className={cn(numCls, isNegative(r.qty) && 'text-danger-600')}>
                    {formatQty(r.qty)}<Uom uom={uom} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function Lines({ code, date }: { code: string; date: string }) {
  const [page, setPage] = usePageFor(date)
  const q = useQuery({
    queryKey: ['nc', 'cost-lines', code, date, page],
    queryFn: () => ncApi.costLines(code, date, page, LINES_PAGE_SIZE),
    placeholderData: keepPreviousData,
  })
  const showAmt = q.data?.amounts_visible ?? false
  const cols = showAmt ? 11 : 10
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-xs font-semibold text-neutral-700">Ledger lines</h3>
      {q.isError ? <ErrorBox error={q.error} fallback="Could not load the ledger lines." /> : (
        <>
          <div className="max-h-96 overflow-auto rounded-md border border-neutral-200 bg-white">
            <table className="min-w-full text-xs">
              <thead className={cn(thCls, 'sticky top-0')}>
                <tr>
                  <th className="px-3 py-1.5 text-left">Period</th>
                  <th className="px-3 py-1.5 text-left" title="As NC recorded it, on NC's clock">Biz date</th>
                  <th className="px-3 py-1.5 text-left">Dir</th>
                  <th className="px-3 py-1.5 text-right">Qty</th>
                  {showAmt && <th className="px-3 py-1.5 text-right">Amount</th>}
                  <th className="px-3 py-1.5 text-left">Type</th>
                  <th className="px-3 py-1.5 text-left">Bill</th>
                  <th className="px-3 py-1.5 text-left">Source</th>
                  <th className="px-3 py-1.5 text-left">Batch</th>
                  <th className="px-3 py-1.5 text-left">Warehouse</th>
                  <th className="px-3 py-1.5 text-left">Estimate</th>
                </tr>
              </thead>
              <tbody>
                {q.isLoading && <LoadingRows cols={cols} rows={4} />}
                {q.data && q.data.items.length === 0 && <EmptyRow cols={cols}>No ledger lines up to this date.</EmptyRow>}
                {q.data?.items.map((l, i) => (
                  <tr key={i} className={cn('border-t border-neutral-100', l.uncosted && 'bg-danger-50/50')}>
                    <td className="px-3 py-1.5 font-mono">{l.period ?? '—'}</td>
                    <td className="px-3 py-1.5 whitespace-nowrap" title={l.biz_date ?? undefined}>
                      {l.biz_date ? l.biz_date.slice(0, 10) : '—'}
                    </td>
                    <td className="px-3 py-1.5">{l.direction === 'in' ? 'In' : 'Out'}</td>
                    <td className={numCls}>{formatQty(l.qty)}</td>
                    {showAmt && (
                      <td className={numCls}>
                        {l.uncosted
                          ? <span className="font-sans font-medium text-danger-600">not costed</span>
                          : formatMoney(l.amount)}
                      </td>
                    )}
                    <td className="px-3 py-1.5">
                      <span className="font-mono">{l.trantype_code ?? l.bill_type ?? '—'}</span>
                      {l.trantype_name && <span className="ml-1.5 text-neutral-500">{l.trantype_name}</span>}
                    </td>
                    <td className="px-3 py-1.5 font-mono">{l.bill_code ?? '—'}</td>
                    <td className="px-3 py-1.5 font-mono">{l.source_code ?? '—'}</td>
                    <td className="px-3 py-1.5 font-mono">{l.batch ?? '—'}</td>
                    <td className="px-3 py-1.5 font-mono">{l.warehouse_code ?? '—'}</td>
                    <td className="px-3 py-1.5">
                      {l.estimate && <Badge variant="warning" className="text-[10px]">Estimate</Badge>}
                      {l.uncosted && !showAmt && <span className="ml-1 font-medium text-danger-600">not costed</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {q.data && q.data.total > LINES_PAGE_SIZE && (
            <Pager page={page} pageSize={LINES_PAGE_SIZE} total={q.data.total} busy={q.isFetching} onPage={setPage} />
          )}
        </>
      )}
    </section>
  )
}

function CostRowView({ r, showAmt, open, onToggle, date, cols }: {
  r: CostRow
  showAmt: boolean
  open: boolean
  onToggle: () => void
  date: string
  cols: number
}) {
  const code = r.material_code
  return (
    <>
      <tr
        className={cn('border-t border-neutral-100', code && 'cursor-pointer hover:bg-neutral-50', open && 'bg-neutral-50')}
        onClick={code ? onToggle : undefined}
      >
        <td className="w-8 px-2 py-2 text-neutral-400">
          {code && (
            <button type="button" aria-expanded={open} aria-label={open ? 'Hide details' : 'Show details'}
              onClick={(e) => { e.stopPropagation(); onToggle() }}>
              {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </button>
          )}
        </td>
        <td className="px-3 py-2"><MaterialCell code={code} name={r.material_name} /></td>
        <td className={cn('px-3 py-2 text-right font-mono text-xs tabular-nums', isNegative(r.qty) && 'text-danger-600')}>
          {formatQty(r.qty)}<Uom uom={r.uom} />
        </td>
        {showAmt && (
          <td className={cn('px-3 py-2 text-right font-mono text-xs tabular-nums', isNegative(r.amount) && 'text-danger-600')}>
            {formatMoney(r.amount)}
          </td>
        )}
        {showAmt && (
          <td className={cn('px-3 py-2 text-right font-mono text-xs tabular-nums', isNegative(r.unit_price) && 'text-danger-600')}>
            {formatUnitPrice(r.unit_price)}
          </td>
        )}
        <td className="px-3 py-2">
          {r.uncosted_rows > 0 && <Badge variant="danger">{r.uncosted_rows.toLocaleString('en-US')} uncosted</Badge>}
        </td>
      </tr>
      {open && code && (
        <tr className="border-t border-neutral-100 bg-neutral-50/60">
          <td colSpan={cols} className="px-4 py-3">
            <div className="flex flex-col gap-4">
              <Breakdown code={code} date={date} uom={r.uom} />
              <Lines code={code} date={date} />
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export function CostTab({ date, classCode, search }: { date: string; classCode: string; search: string }) {
  const [sortState, onSort] = useSort<CostSort>()
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())

  const filterKey = JSON.stringify([date, classCode, search, sortState])
  const [page, setPage] = usePageFor(filterKey)

  const q = useQuery({
    queryKey: ['nc', 'cost', date, classCode, search, sortState, page],
    queryFn: () => ncApi.cost({
      date, class_code: classCode || undefined, search: search || undefined,
      ...sortParams(sortState), page, page_size: PAGE_SIZE,
    }),
    placeholderData: keepPreviousData,
  })

  const data = q.data
  const showAmt = data?.amounts_visible ?? false
  // The API ignores an amount sort when amounts are hidden (default order).
  const sort = sortState && (sortState.key === 'amount' || sortState.key === 'unit_price') && !showAmt
    ? null : sortState
  const cols = showAmt ? 6 : 4

  function toggle(code: string) {
    setExpanded((cur) => {
      const next = new Set(cur)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  }

  return (
    <div className="flex flex-col gap-3">
      {data && (
        <div className="flex flex-wrap gap-2">
          <Tile label="Materials" value={data.totals.materials.toLocaleString('en-US')} />
          <Tile label="Qty" value={formatQty(data.totals.qty)} title="Summed across units of measure" />
          {showAmt && <Tile label="Amount" value={formatMoney(data.totals.amount)} />}
        </div>
      )}

      {q.isError && <ErrorBox error={q.error} fallback="Could not load NC cost." />}

      {!(q.isError && !data) && (
        <ScrollTable className={cn(q.isFetching && data && 'opacity-70')}>
          <table className="min-w-full text-sm">
            <thead className={stickyHeadCls}>
              <tr>
                <th className="w-8 px-2 py-2" aria-label="Expand" />
                <SortTh label="Material" sortKey="material" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
                <SortTh label="Qty" sortKey="qty" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
                {showAmt && <SortTh label="Amount" sortKey="amount" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />}
                {showAmt && <SortTh label="Unit price" sortKey="unit_price" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />}
                <SortTh label="Costing" title="Sort by number of uncosted rows" sortKey="uncosted" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
              </tr>
            </thead>
            <tbody>
              {q.isLoading && <LoadingRows cols={cols} />}
              {data && data.items.length === 0 && (
                <EmptyRow cols={cols}>{search ? `Nothing matches "${search}".` : 'No IA balance on this date for these filters.'}</EmptyRow>
              )}
              {data?.items.map((r, i) => (
                <CostRowView key={r.material_code ?? `missing-${i}`} r={r} showAmt={showAmt} date={date} cols={cols}
                  open={!!r.material_code && expanded.has(r.material_code)}
                  onToggle={() => r.material_code && toggle(r.material_code)} />
              ))}
            </tbody>
          </table>
        </ScrollTable>
      )}

      <Pager page={page} pageSize={PAGE_SIZE} total={data?.total ?? 0} busy={q.isFetching} onPage={setPage} />
    </div>
  )
}

function Tile({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="min-w-[9rem] rounded-lg border border-neutral-200 bg-white px-3 py-2" title={title}>
      <p className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">{label}</p>
      <p className="mt-0.5 font-mono text-sm text-neutral-800">{value}</p>
    </div>
  )
}
