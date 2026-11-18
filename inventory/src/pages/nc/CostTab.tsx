// NC Inventory → Cost (IA). One row per material: quantity and value from
// NC's inventory accounting ledger as of the date. IA costs at material level,
// so the warehouse/batch breakdown under a row carries quantities only.
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Badge, SortTh, sortParams, useSort, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
import { cn, formatDate } from '@/lib/utils'
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
      <h3 className="text-xs font-semibold text-fg-secondary">Quantity by warehouse and batch</h3>
      <p className="text-2xs text-fg-muted">IA costs at material level only — amounts are not split by batch.</p>
      {q.isError ? <ErrorBox error={q.error} fallback="Could not load the breakdown." /> : (
        <div className="max-h-72 overflow-auto rounded-md border border-line bg-surface">
          <Table density="compact" className="min-w-full">
            <THead className={thCls}>
              <Tr>
                <Th>Warehouse</Th>
                <Th>Batch</Th>
                <Th align="right">Qty</Th>
              </Tr>
            </THead>
            <TBody>
              {q.isLoading && <LoadingRows cols={3} rows={3} />}
              {q.data && q.data.items.length === 0 && <EmptyRow cols={3}>No quantity on this date.</EmptyRow>}
              {q.data?.items.map((r, i) => (
                <Tr key={`${r.warehouse_code}|${r.batch}|${i}`}>
                  <Td className="font-mono">{r.warehouse_code ?? '—'}</Td>
                  <Td className="font-mono">{r.batch ?? <span className="font-sans text-fg-muted">no batch</span>}</Td>
                  <Td className={cn(numCls, isNegative(r.qty) && 'text-danger-fg')}>
                    {formatQty(r.qty)}<Uom uom={uom} />
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
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
      <h3 className="text-xs font-semibold text-fg-secondary">Ledger lines</h3>
      {q.isError ? <ErrorBox error={q.error} fallback="Could not load the ledger lines." /> : (
        <>
          <div className="max-h-96 overflow-auto rounded-md border border-line bg-surface">
            <Table density="compact" className="min-w-full">
              <THead className={cn(thCls, 'sticky top-0')}>
                <Tr>
                  <Th>Period</Th>
                  <Th title="As NC recorded it, on NC's clock">Biz date</Th>
                  <Th>Dir</Th>
                  <Th align="right">Qty</Th>
                  {showAmt && <Th align="right">Amount</Th>}
                  <Th>Type</Th>
                  <Th>Bill</Th>
                  <Th>Source</Th>
                  <Th>Batch</Th>
                  <Th>Warehouse</Th>
                  <Th>Estimate</Th>
                </Tr>
              </THead>
              <TBody>
                {q.isLoading && <LoadingRows cols={cols} rows={4} />}
                {q.data && q.data.items.length === 0 && <EmptyRow cols={cols}>No ledger lines up to this date.</EmptyRow>}
                {q.data?.items.map((l, i) => (
                  <Tr key={i} className={cn('border-t border-line-subtle', l.uncosted && 'bg-danger-50/50')}>
                    <Td className="font-mono">{l.period ?? '—'}</Td>
                    <Td className="whitespace-nowrap" title={l.biz_date ?? undefined}>
                      {l.biz_date ? formatDate(l.biz_date.slice(0, 10)) : '—'}
                    </Td>
                    <Td>{l.direction === 'in' ? 'In' : 'Out'}</Td>
                    <Td className={numCls}>{formatQty(l.qty)}</Td>
                    {showAmt && (
                      <Td className={numCls}>
                        {l.uncosted
                          ? <span className="font-sans font-medium text-danger-fg">not costed</span>
                          : formatMoney(l.amount)}
                      </Td>
                    )}
                    <Td>
                      <span className="font-mono">{l.trantype_code ?? l.bill_type ?? '—'}</span>
                      {l.trantype_name && <span className="ml-1.5 text-fg-muted">{l.trantype_name}</span>}
                    </Td>
                    <Td className="font-mono">{l.bill_code ?? '—'}</Td>
                    <Td className="font-mono">{l.source_code ?? '—'}</Td>
                    <Td className="font-mono">{l.batch ?? '—'}</Td>
                    <Td className="font-mono">{l.warehouse_code ?? '—'}</Td>
                    <Td>
                      {l.estimate && <Badge variant="warning" className="text-2xs">Estimate</Badge>}
                      {l.uncosted && !showAmt && <span className="ml-1 font-medium text-danger-fg">not costed</span>}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
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
      <Tr
        className={cn('border-t border-line-subtle', code && 'cursor-pointer hover:bg-surface-subtle', open && 'bg-surface-subtle')}
        onClick={code ? onToggle : undefined}
      >
        <Td className="w-8 text-fg-muted">
          {code && (
            <Button variant="subtle" size="icon-xs" type="button" aria-expanded={open} aria-label={open ? 'Hide details' : 'Show details'}
              onClick={(e) => { e.stopPropagation(); onToggle() }}>
              {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </Button>
          )}
        </Td>
        <Td><MaterialCell code={code} name={r.material_name} /></Td>
        <Td className={cn('px-3 py-2 text-right font-mono text-xs tabular-nums', isNegative(r.qty) && 'text-danger-fg')}>
          {formatQty(r.qty)}<Uom uom={r.uom} />
        </Td>
        {showAmt && (
          <Td className={cn('px-3 py-2 text-right font-mono text-xs tabular-nums', isNegative(r.amount) && 'text-danger-fg')}>
            {formatMoney(r.amount)}
          </Td>
        )}
        {showAmt && (
          <Td className={cn('px-3 py-2 text-right font-mono text-xs tabular-nums', isNegative(r.unit_price) && 'text-danger-fg')}>
            {formatUnitPrice(r.unit_price)}
          </Td>
        )}
        <Td>
          {r.uncosted_rows > 0 && <Badge variant="danger">{r.uncosted_rows.toLocaleString('en-US')} uncosted</Badge>}
        </Td>
      </Tr>
      {open && code && (
        <Tr className="bg-surface-subtle/60">
          <Td colSpan={cols}>
            <div className="flex flex-col gap-4">
              <Breakdown code={code} date={date} uom={r.uom} />
              <Lines code={code} date={date} />
            </div>
          </Td>
        </Tr>
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
          <Table density="compact" className="min-w-full">
            <THead className={stickyHeadCls}>
              <Tr>
                <Th className="w-8" aria-label="Expand" />
                <SortTh label="Material" sortKey="material" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
                <SortTh label="Qty" sortKey="qty" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />
                {showAmt && <SortTh label="Amount" sortKey="amount" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />}
                {showAmt && <SortTh label="Unit price" sortKey="unit_price" sort={sort} onSort={onSort} align="right" className="px-3 py-2" />}
                <SortTh label="Costing" title="Sort by number of uncosted rows" sortKey="uncosted" sort={sort} onSort={onSort} className="px-3 py-2 text-left" />
              </Tr>
            </THead>
            <TBody>
              {q.isLoading && <LoadingRows cols={cols} />}
              {data && data.items.length === 0 && (
                <EmptyRow cols={cols}>{search ? `Nothing matches "${search}".` : 'No IA balance on this date for these filters.'}</EmptyRow>
              )}
              {data?.items.map((r, i) => (
                <CostRowView key={r.material_code ?? `missing-${i}`} r={r} showAmt={showAmt} date={date} cols={cols}
                  open={!!r.material_code && expanded.has(r.material_code)}
                  onToggle={() => r.material_code && toggle(r.material_code)} />
              ))}
            </TBody>
          </Table>
        </ScrollTable>
      )}

      <Pager page={page} pageSize={PAGE_SIZE} total={data?.total ?? 0} busy={q.isFetching} onPage={setPage} />
    </div>
  )
}

function Tile({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="min-w-[9rem] rounded-lg border border-line bg-surface px-3 py-2" title={title}>
      <p className="text-2xs font-medium text-fg-muted">{label}</p>
      <p className="mt-0.5 font-mono text-sm text-fg">{value}</p>
    </div>
  )
}
