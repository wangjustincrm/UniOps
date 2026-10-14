// NC Inventory → Stock (IC). Rows are warehouse × material × batch (or one per
// material). Stock state, locks and availability exist in NC only for today:
// on a past date the API sends quantities only (state_available false), and
// those columns show "—" rather than a number that would be invented.
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Badge } from '@uniops/shell'
import { cn } from '@/lib/utils'
import { formatQty, isNegative } from '@/lib/number'
import {
  WAREHOUSE_CLASSES, ncApi,
  type StockBatchRow, type StockMaterialRow, type StockSort, type StockSummaryRow,
} from './ncApi'
import { EmptyRow, ErrorBox, LoadingRows, MaterialCell, Pager, SortTh, Uom, tableWrapCls, thCls } from './NcUi'
import { usePageFor } from './usePageFor'

const PAGE_SIZE = 50

const CLASS_LABEL: Record<string, string> = Object.fromEntries(WAREHOUSE_CLASSES.map((c) => [c.key, c.label]))

const CLASS_BADGE: Record<string, 'info' | 'neutral' | 'warning' | 'dark'> = {
  wms: 'info', external: 'neutral', production: 'neutral', transient: 'neutral', other: 'neutral',
  unclassified: 'warning',
}

function QtyCell({ value, uom }: { value: string | null; uom?: string | null }) {
  return (
    <td className={cn('px-3 py-2 text-right font-mono text-xs tabular-nums', isNegative(value) && 'text-danger-600')}>
      {formatQty(value)}
      {uom !== undefined && <Uom uom={uom} />}
    </td>
  )
}

function Summary({ rows, residue }: { rows: StockSummaryRow[]; residue: number }) {
  if (rows.length === 0 && residue === 0) return null
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap gap-2">
        {rows.map((s) => (
          <div key={s.warehouse_class} className="min-w-[9rem] rounded-lg border border-neutral-200 bg-white px-3 py-2">
            <p className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">
              {CLASS_LABEL[s.warehouse_class] ?? s.warehouse_class}
            </p>
            <p className="mt-0.5 text-sm text-neutral-800">
              {s.materials.toLocaleString('en-US')} materials · {s.lines.toLocaleString('en-US')} lines
            </p>
            <p className="font-mono text-xs text-neutral-500" title="Summed across units of measure">
              Qty {formatQty(s.qty)}
            </p>
          </div>
        ))}
      </div>
      {residue > 0 && (
        <p className="text-[11px] text-neutral-400">
          {residue.toLocaleString('en-US')} locked rows with zero on-hand are not listed (NC residue).
        </p>
      )}
    </div>
  )
}

function BatchTable({ items, stateAvailable, sort, onSort, loading, emptyText }: {
  items: StockBatchRow[]
  stateAvailable: boolean
  sort: StockSort
  onSort: (s: StockSort) => void
  loading: boolean
  emptyText: string
}) {
  const cols = 10
  const dash = <span className="text-neutral-300">—</span>
  return (
    <table className="min-w-full text-sm">
      <thead className={thCls}>
        <tr>
          <SortTh label="Warehouse" sortKey="warehouse" current={sort} onSort={onSort} />
          <SortTh label="Material" sortKey="material" current={sort} onSort={onSort} />
          <SortTh label="Batch" sortKey="batch" current={sort} onSort={onSort} />
          <th className="px-3 py-2 text-left">Supplier batch</th>
          <th className="px-3 py-2 text-left">Produced</th>
          <th className="px-3 py-2 text-left">Expires</th>
          <SortTh label="Qty" sortKey="qty" current={sort} onSort={onSort} align="right" descending />
          <th className={cn('px-3 py-2 text-right', !stateAvailable && 'text-neutral-300')}>Locked</th>
          <th className={cn('px-3 py-2 text-right', !stateAvailable && 'text-neutral-300')}>Available</th>
          <th className={cn('px-3 py-2 text-left', !stateAvailable && 'text-neutral-300')}>State</th>
        </tr>
      </thead>
      <tbody>
        {loading && <LoadingRows cols={cols} />}
        {!loading && items.length === 0 && <EmptyRow cols={cols}>{emptyText}</EmptyRow>}
        {!loading && items.map((r, i) => (
          <tr key={`${r.warehouse_code}|${r.material_code}|${r.batch ?? ''}|${r.state ?? ''}|${i}`}
            className="border-t border-neutral-100 align-top">
            <td className="px-3 py-2">
              <span className="font-mono text-xs" title={r.warehouse_name ?? undefined}>{r.warehouse_code ?? '—'}</span>
              <span className="ml-1.5 inline-flex flex-wrap gap-1">
                <Badge variant={CLASS_BADGE[r.warehouse_class] ?? 'neutral'} className="text-[10px]">
                  {CLASS_LABEL[r.warehouse_class] ?? r.warehouse_class}
                </Badge>
                {r.costed === false && <Badge variant="warning" className="text-[10px]">Not costed</Badge>}
              </span>
            </td>
            <td className="px-3 py-2"><MaterialCell code={r.material_code} name={r.material_name} /></td>
            <td className="px-3 py-2 font-mono text-xs">{r.batch ?? <span className="font-sans text-neutral-400">no batch</span>}</td>
            <td className="px-3 py-2 font-mono text-xs text-neutral-600">{r.supplier_batch ?? '—'}</td>
            <td className="px-3 py-2 text-xs text-neutral-600">{r.produced ?? '—'}</td>
            <td className="px-3 py-2 text-xs text-neutral-600">{r.expires ?? '—'}</td>
            <QtyCell value={r.qty} uom={r.uom} />
            {stateAvailable ? <QtyCell value={r.locked} /> : <td className="px-3 py-2 text-right">{dash}</td>}
            {stateAvailable ? <QtyCell value={r.available} /> : <td className="px-3 py-2 text-right">{dash}</td>}
            <td className="px-3 py-2 text-xs text-neutral-600">{stateAvailable ? (r.state ?? '—') : dash}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function MaterialTable({ items, stateAvailable, sort, onSort, loading, emptyText }: {
  items: StockMaterialRow[]
  stateAvailable: boolean
  sort: StockSort
  onSort: (s: StockSort) => void
  loading: boolean
  emptyText: string
}) {
  const cols = stateAvailable ? 8 : 7
  return (
    <table className="min-w-full text-sm">
      <thead className={thCls}>
        <tr>
          <SortTh label="Material" sortKey="material" current={sort} onSort={onSort} />
          <SortTh label="Total" sortKey="qty" current={sort} onSort={onSort} align="right" descending />
          <th className="px-3 py-2 text-right">WMS warehouses</th>
          <th className="px-3 py-2 text-right">External</th>
          <th className="px-3 py-2 text-right">Production line-side</th>
          <th className="px-3 py-2 text-right" title="Transient, other and unclassified warehouses">Other</th>
          <th className="px-3 py-2 text-right">Batches</th>
          {stateAvailable && <th className="px-3 py-2 text-right">Locked</th>}
        </tr>
      </thead>
      <tbody>
        {loading && <LoadingRows cols={cols} />}
        {!loading && items.length === 0 && <EmptyRow cols={cols}>{emptyText}</EmptyRow>}
        {!loading && items.map((r, i) => (
          <tr key={`${r.material_code}|${i}`} className="border-t border-neutral-100">
            <td className="px-3 py-2"><MaterialCell code={r.material_code} name={r.material_name} /></td>
            <QtyCell value={r.qty} uom={r.uom} />
            <QtyCell value={r.qty_wms} />
            <QtyCell value={r.qty_external} />
            <QtyCell value={r.qty_production} />
            <QtyCell value={r.qty_other} />
            <td className="px-3 py-2 text-right font-mono text-xs">{r.batches.toLocaleString('en-US')}</td>
            {stateAvailable && <QtyCell value={r.locked} />}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function StockTab({ date, classCode, search }: { date: string; classCode: string; search: string }) {
  const [group, setGroup] = useState<'batch' | 'material'>('batch')
  const [classes, setClasses] = useState<string[]>([])
  const [sortState, setSort] = useState<StockSort>('material')
  // By material the API sorts by material or total only.
  const sort: StockSort = group === 'material' && sortState !== 'qty' ? 'material' : sortState

  const filterKey = JSON.stringify([date, classCode, search, group, classes, sort])
  const [page, setPage] = usePageFor(filterKey)

  const q = useQuery({
    queryKey: ['nc', 'stock', date, classCode, search, group, classes, sort, page],
    queryFn: () => ncApi.stock({
      date, class_code: classCode || undefined, search: search || undefined,
      group, warehouse_class: classes, sort, page, page_size: PAGE_SIZE,
    }),
    placeholderData: keepPreviousData,
  })

  const data = q.data
  // A previous response may still be on screen while the new one loads
  // (keepPreviousData); only trust its shape when it matches the toggle.
  const shown = data && data.group === group ? data : undefined
  const loading = q.isLoading || (q.isFetching && !shown)
  const stateAvailable = shown?.state_available ?? true
  const emptyText = search ? `Nothing matches "${search}".` : 'No stock on this date for these filters.'

  function toggleClass(key: string) {
    setClasses((cur) => (cur.includes(key) ? cur.filter((c) => c !== key) : [...cur, key]))
  }

  return (
    <div className="flex flex-col gap-3">
      {shown && <Summary rows={shown.summary} residue={shown.lock_residue_rows} />}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div role="group" aria-label="Group rows" className="inline-flex rounded-lg border border-neutral-300 bg-white p-0.5">
          {(['batch', 'material'] as const).map((g) => (
            <button key={g} type="button" onClick={() => setGroup(g)} aria-pressed={group === g}
              className={cn('rounded-md px-3 py-1 text-xs font-medium',
                group === g ? 'bg-primary-600 text-white' : 'text-neutral-600 hover:bg-neutral-100')}>
              {g === 'batch' ? 'By batch' : 'By material'}
            </button>
          ))}
        </div>

        <fieldset className="flex flex-wrap items-center gap-1.5">
          <legend className="sr-only">Warehouse class</legend>
          <span className="text-xs text-neutral-600">Warehouses:</span>
          {WAREHOUSE_CLASSES.map((c) => {
            const on = classes.includes(c.key)
            return (
              <button key={c.key} type="button" onClick={() => toggleClass(c.key)} aria-pressed={on}
                className={cn('rounded-full border px-2.5 py-0.5 text-xs',
                  on ? 'border-primary-600 bg-primary-50 text-primary-700' : 'border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50')}>
                {c.label}
              </button>
            )
          })}
          {classes.length > 0 ? (
            <button type="button" onClick={() => setClasses([])} className="text-xs text-primary-600 hover:underline">
              All
            </button>
          ) : <span className="text-[11px] text-neutral-400">(all)</span>}
        </fieldset>
      </div>

      {q.isError && <ErrorBox error={q.error} fallback="Could not load NC stock." />}

      {!stateAvailable && (
        <p className="text-xs text-neutral-500">
          Historical date: NC keeps stock state and locks only for today — quantities only.
        </p>
      )}

      {!(q.isError && !shown) && <div className={cn(tableWrapCls, q.isFetching && shown && 'opacity-70')}>
        {group === 'batch' ? (
          <BatchTable items={(shown?.items ?? []) as StockBatchRow[]} stateAvailable={stateAvailable}
            sort={sort} onSort={setSort} loading={loading} emptyText={emptyText} />
        ) : (
          <MaterialTable items={(shown?.items ?? []) as StockMaterialRow[]} stateAvailable={stateAvailable}
            sort={sort} onSort={setSort} loading={loading} emptyText={emptyText} />
        )}
      </div>}

      <Pager page={page} pageSize={PAGE_SIZE} total={shown?.total ?? 0} busy={q.isFetching} onPage={setPage} />
    </div>
  )
}
