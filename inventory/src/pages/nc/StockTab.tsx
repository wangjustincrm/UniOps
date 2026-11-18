// NC Inventory → Stock (IC). Rows are warehouse × material × batch (or one per
// material). Stock state, locks and availability exist in NC only for today:
// on a past date the API sends quantities only (state_available false), and
// those columns show "—" rather than a number that would be invented.
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Badge, Button, Chip, Segmented, SortTh, TBody, THead, Table, Td, Th, Tr, sortParams, type Sort, useSort } from '@uniops/shell'
import { cn } from '@/lib/utils'
import { formatQty, isNegative } from '@/lib/number'
import {
  WAREHOUSE_CLASSES, ncApi,
  type StockBatchRow, type StockBatchSort, type StockMaterialRow, type StockMaterialSort, type StockSummaryRow,
} from './ncApi'
import { EmptyRow, ErrorBox, LoadingRows, MaterialCell, Pager, Uom } from './NcUi'
import { usePageFor } from './usePageFor'
import { ScrollTable, stickyHeadCls } from '@/components/ScrollTable'

const PAGE_SIZE = 50

const CLASS_LABEL: Record<string, string> = Object.fromEntries(WAREHOUSE_CLASSES.map((c) => [c.key, c.label]))

const CLASS_BADGE: Record<string, 'info' | 'neutral' | 'warning' | 'dark'> = {
  wms: 'info', external: 'neutral', production: 'neutral', transient: 'neutral', other: 'neutral',
  unclassified: 'warning',
}

function QtyCell({ value, uom }: { value: string | null; uom?: string | null }) {
  return (
    <Td className={cn('px-3 py-2 text-right font-mono text-xs tabular-nums', isNegative(value) && 'text-danger-fg')}>
      {formatQty(value)}
      {uom !== undefined && <Uom uom={uom} />}
    </Td>
  )
}

function Summary({ rows, residue }: { rows: StockSummaryRow[]; residue: number }) {
  if (rows.length === 0 && residue === 0) return null
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap gap-2">
        {rows.map((s) => (
          <div key={s.warehouse_class} className="min-w-[9rem] rounded-lg border border-line bg-surface px-3 py-2">
            <p className="text-2xs font-medium text-fg-muted">
              {CLASS_LABEL[s.warehouse_class] ?? s.warehouse_class}
            </p>
            <p className="mt-0.5 text-sm text-fg">
              {s.materials.toLocaleString('en-US')} materials · {s.lines.toLocaleString('en-US')} lines
            </p>
            <p className="font-mono text-xs text-fg-muted" title="Summed across units of measure">
              Qty {formatQty(s.qty)}
            </p>
          </div>
        ))}
      </div>
      {residue > 0 && (
        <p className="text-2xs text-fg-muted">
          {residue.toLocaleString('en-US')} locked rows with zero on-hand are not listed (NC residue).
        </p>
      )}
    </div>
  )
}

const thL = 'px-3 py-2 text-left'
const thR = 'px-3 py-2'

function BatchTable({ items, stateAvailable, sort, onSort, loading, emptyText }: {
  items: StockBatchRow[]
  stateAvailable: boolean
  sort: Sort<StockBatchSort> | null
  onSort: (k: StockBatchSort) => void
  loading: boolean
  emptyText: string
}) {
  const cols = 10
  const dash = <span className="text-fg-faint">—</span>
  return (
    <Table density="compact" className="min-w-full">
      <THead className={stickyHeadCls}>
        <Tr>
          <SortTh label="Warehouse" sortKey="warehouse" sort={sort} onSort={onSort} className={thL} />
          <SortTh label="Material" sortKey="material" sort={sort} onSort={onSort} className={thL} />
          <SortTh label="Batch" sortKey="batch" sort={sort} onSort={onSort} className={thL} />
          <SortTh label="Supplier batch" sortKey="supplier_batch" sort={sort} onSort={onSort} className={thL} />
          <SortTh label="Produced" sortKey="produced" sort={sort} onSort={onSort} className={thL} />
          <SortTh label="Expires" sortKey="expires" sort={sort} onSort={onSort} className={thL} />
          <SortTh label="Qty" sortKey="qty" sort={sort} onSort={onSort} align="right" className={thR} />
          {/* Locked / available / state exist only for today; on a past date
              the columns are empty and not sortable. */}
          {stateAvailable ? <>
            <SortTh label="Locked" sortKey="locked" sort={sort} onSort={onSort} align="right" className={thR} />
            <SortTh label="Available" sortKey="available" sort={sort} onSort={onSort} align="right" className={thR} />
            <SortTh label="State" sortKey="state" sort={sort} onSort={onSort} className={thL} />
          </> : <>
            <Th align="right">Locked</Th>
            <Th align="right">Available</Th>
            <Th>State</Th>
          </>}
        </Tr>
      </THead>
      <TBody>
        {loading && <LoadingRows cols={cols} />}
        {!loading && items.length === 0 && <EmptyRow cols={cols}>{emptyText}</EmptyRow>}
        {!loading && items.map((r, i) => (
          <Tr key={`${r.warehouse_code}|${r.material_code}|${r.batch ?? ''}|${r.state ?? ''}|${i}`}
            className="align-top">
            <Td>
              <span className="font-mono text-xs" title={r.warehouse_name ?? undefined}>{r.warehouse_code ?? '—'}</span>
              <span className="ml-1.5 inline-flex flex-wrap gap-1">
                <Badge variant={CLASS_BADGE[r.warehouse_class] ?? 'neutral'} className="text-2xs">
                  {CLASS_LABEL[r.warehouse_class] ?? r.warehouse_class}
                </Badge>
                {r.costed === false && <Badge variant="warning" className="text-2xs">Not costed</Badge>}
              </span>
            </Td>
            <Td><MaterialCell code={r.material_code} name={r.material_name} /></Td>
            <Td className="font-mono text-xs">{r.batch ?? <span className="font-sans text-fg-muted">no batch</span>}</Td>
            <Td className="font-mono text-xs text-fg-secondary">{r.supplier_batch ?? '—'}</Td>
            <Td className="text-xs text-fg-secondary">{r.produced ?? '—'}</Td>
            <Td className="text-xs text-fg-secondary">{r.expires ?? '—'}</Td>
            <QtyCell value={r.qty} uom={r.uom} />
            {stateAvailable ? <QtyCell value={r.locked} /> : <Td align="right">{dash}</Td>}
            {stateAvailable ? <QtyCell value={r.available} /> : <Td align="right">{dash}</Td>}
            <Td className="text-xs text-fg-secondary">{stateAvailable ? (r.state ?? '—') : dash}</Td>
          </Tr>
        ))}
      </TBody>
    </Table>
  )
}

function MaterialTable({ items, stateAvailable, sort, onSort, loading, emptyText }: {
  items: StockMaterialRow[]
  stateAvailable: boolean
  sort: Sort<StockMaterialSort> | null
  onSort: (k: StockMaterialSort) => void
  loading: boolean
  emptyText: string
}) {
  const cols = stateAvailable ? 8 : 7
  return (
    <Table density="compact" className="min-w-full">
      <THead className={stickyHeadCls}>
        <Tr>
          <SortTh label="Material" sortKey="material" sort={sort} onSort={onSort} className={thL} />
          <SortTh label="Total" sortKey="qty" sort={sort} onSort={onSort} align="right" className={thR} />
          <SortTh label="WMS warehouses" sortKey="qty_wms" sort={sort} onSort={onSort} align="right" className={thR} />
          <SortTh label="External" sortKey="qty_external" sort={sort} onSort={onSort} align="right" className={thR} />
          <SortTh label="Production line-side" sortKey="qty_production" sort={sort} onSort={onSort} align="right" className={thR} />
          <SortTh label="Other" title="Transient, other and unclassified warehouses — click to sort"
            sortKey="qty_other" sort={sort} onSort={onSort} align="right" className={thR} />
          <SortTh label="Batches" sortKey="batches" sort={sort} onSort={onSort} align="right" className={thR} />
          {stateAvailable && <SortTh label="Locked" sortKey="locked" sort={sort} onSort={onSort} align="right" className={thR} />}
        </Tr>
      </THead>
      <TBody>
        {loading && <LoadingRows cols={cols} />}
        {!loading && items.length === 0 && <EmptyRow cols={cols}>{emptyText}</EmptyRow>}
        {!loading && items.map((r, i) => (
          <Tr key={`${r.material_code}|${i}`}>
            <Td><MaterialCell code={r.material_code} name={r.material_name} /></Td>
            <QtyCell value={r.qty} uom={r.uom} />
            <QtyCell value={r.qty_wms} />
            <QtyCell value={r.qty_external} />
            <QtyCell value={r.qty_production} />
            <QtyCell value={r.qty_other} />
            <Td align="right" className="font-mono text-xs">{r.batches.toLocaleString('en-US')}</Td>
            {stateAvailable && <QtyCell value={r.locked} />}
          </Tr>
        ))}
      </TBody>
    </Table>
  )
}

export function StockTab({ date, classCode, search }: { date: string; classCode: string; search: string }) {
  const [group, setGroup] = useState<'batch' | 'material'>('batch')
  const [classes, setClasses] = useState<string[]>([])
  // One sort per grouping: the two tables have different columns.
  const [batchSort, onBatchSort] = useSort<StockBatchSort>()
  const [materialSort, onMaterialSort] = useSort<StockMaterialSort>()
  const sort = group === 'batch' ? batchSort : materialSort

  // The sort is part of the key, so a new sort starts again at page 1.
  const filterKey = JSON.stringify([date, classCode, search, group, classes, sort])
  const [page, setPage] = usePageFor(filterKey)

  const q = useQuery({
    queryKey: ['nc', 'stock', date, classCode, search, group, classes, sort, page],
    queryFn: () => ncApi.stock({
      date, class_code: classCode || undefined, search: search || undefined,
      group, warehouse_class: classes, ...sortParams(sort), page, page_size: PAGE_SIZE,
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
        <Segmented<'batch' | 'material'>
          items={[{ key: 'batch', label: 'By batch' }, { key: 'material', label: 'By material' }]}
          value={group}
          onChange={setGroup}
        />

        <fieldset className="flex flex-wrap items-center gap-1.5">
          <legend className="sr-only">Warehouse class</legend>
          <span className="text-xs text-fg-secondary">Warehouses:</span>
          {WAREHOUSE_CLASSES.map((c) => {
            const on = classes.includes(c.key)
            return (
              <Chip key={c.key} pressed={on} onClick={() => toggleClass(c.key)}>
                {c.label}
              </Chip>
            )
          })}
          {classes.length > 0 ? (
            <Button variant="link" type="button" onClick={() => setClasses([])} className="text-xs">
              All
            </Button>
          ) : <span className="text-2xs text-fg-muted">(all)</span>}
        </fieldset>
      </div>

      {q.isError && <ErrorBox error={q.error} fallback="Could not load NC stock." />}

      {!stateAvailable && (
        <p className="text-xs text-fg-muted">
          Historical date: NC keeps stock state and locks only for today — quantities only.
        </p>
      )}

      {!(q.isError && !shown) && <ScrollTable className={cn(q.isFetching && shown && 'opacity-70')}>
        {group === 'batch' ? (
          <BatchTable items={(shown?.items ?? []) as StockBatchRow[]} stateAvailable={stateAvailable}
            sort={batchSort} onSort={onBatchSort} loading={loading} emptyText={emptyText} />
        ) : (
          <MaterialTable items={(shown?.items ?? []) as StockMaterialRow[]} stateAvailable={stateAvailable}
            sort={materialSort} onSort={onMaterialSort} loading={loading} emptyText={emptyText} />
        )}
      </ScrollTable>}

      <Pager page={page} pageSize={PAGE_SIZE} total={shown?.total ?? 0} busy={q.isFetching} onPage={setPage} />
    </div>
  )
}
