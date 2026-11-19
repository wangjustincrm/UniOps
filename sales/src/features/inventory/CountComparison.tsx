import { useMemo, useState } from 'react'
import { Callout, Checkbox, StatusCell, LocalTable, Section, columnsOf, formatDate, formatNumber, startHidden, type ColumnDef, useLookup, Text, Cluster } from '@/platform-ui'
import { COMPARE_KIND, fetchCountCompare, type CountCompareRow, type StockCountRead } from './inventoryApi'

const KIND_TONE = { match: 'success', differs: 'warning', sales_only: 'danger', radiant_only: 'info', no_lot: 'neutral' } as const

/** "Sep 8, 2028 · 665 days" (days counted by the server, Toronto day; past: "expired"). */
function ExpiryCell({ value, days }: { value: string | null; days: number | null }) {
  if (!value || days == null) return <Text tone="faint">—</Text>
  return (
    <Text wrap="nowrap">
      {formatDate(value)}
      <Text tone={days < 0 ? 'danger' : days <= 90 ? 'warning' : 'muted'}>
        {' · '}{days < 0 ? 'expired' : `${formatNumber(days)} days`}
      </Text>
    </Text>
  )
}

const strong = (v: number) => <Text weight="semibold" nums className="block text-right">{formatNumber(v)}</Text>
const signed = (v: number) => (
  <Text weight="semibold" nums tone={v ? 'warning' : 'faint'} className="block text-right">{v > 0 ? `+${formatNumber(v)}` : formatNumber(v)}</Text>
)

/** The figures of a lot and of an item (the sum of its lots) — the same columns, in the same order (user 2026-11-13):
 *  up to the report (in, out, adjusted, remaining, Radiant, difference), then after it (in, out, remaining now). */
function figureColumns<R extends ItemFigures>(): ColumnDef<R, unknown>[] {
  const c = columnsOf<R>()
  return [
    c.number('received_ea' as never, 'Received'),
    c.number('shipped_ea' as never, 'Shipped'),
    startHidden(c.number('returned_ea' as never, 'Returned (in Shipped)')),
    startHidden(c.number('adjusted_ea' as never, 'Adjusted')),
    { id: 'sales_ea', header: 'On hand (Sales)', cell: ({ row }) => strong(row.original.sales_ea) },
    { id: 'radiant_ea', header: 'Radiant', cell: ({ row }) => strong(row.original.radiant_ea) },
    { id: 'difference_ea', header: 'Difference (missing Sales history)', cell: ({ row }) => signed(row.original.difference_ea) },
  ]
}

/** At the report: what Radiant holds back, and what of its figure can be sold. */
function radiantColumns<R extends ItemFigures>(): ColumnDef<R, unknown>[] {
  const c = columnsOf<R>()
  return [
    { id: 'available_ea', header: 'Available', cell: ({ row }) => strong(row.original.available_ea) },
    c.number('hold_ea' as never, 'On hold'),
  ]
}

function compareColumns(): ColumnDef<CountCompareRow, unknown>[] {
  return [
    { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => <Text weight="medium">{row.original.lot ?? '(no lot)'}</Text> },
    { accessorKey: 'expiry', header: 'Expiry', cell: ({ row }) => <ExpiryCell value={row.original.expiry} days={row.original.days_to_expiry} /> },
    ...figureColumns<CountCompareRow>(),
    ...radiantColumns<CountCompareRow>(),
    { accessorKey: 'kind', header: 'Result', cell: ({ row }) => (row.original.open
      ? <StatusCell value={COMPARE_KIND[row.original.kind]} tone={KIND_TONE[row.original.kind]} />
      : <StatusCell value="Closed" tone="neutral" />) },
  ]
}

const SUMS = ['received_ea', 'returned_ea', 'shipped_ea', 'adjusted_ea', 'sales_ea', 'radiant_ea', 'hold_ea', 'difference_ea',
  'available_ea'] as const
type ItemFigures = Record<(typeof SUMS)[number], number>

interface ItemGroup extends ItemFigures {
  item_code: string; description: string | null; lots: CountCompareRow[]; open: number; closed: number; differing: number; first_expiry: string | null; first_days: number | null
}

function group(rows: CountCompareRow[]): ItemGroup[] {
  const by = new Map<string, ItemGroup>()
  for (const r of rows) {
    let g = by.get(r.item_code)
    if (!g) {
      g = { item_code: r.item_code, description: r.description, lots: [], open: 0, closed: 0, differing: 0, first_expiry: null, first_days: null,
        ...(Object.fromEntries(SUMS.map((k) => [k, 0])) as ItemFigures) }
      by.set(r.item_code, g)
    }
    g.lots.push(r)
    if (!r.open) { g.closed += 1; continue }  // a closed lot counts in no total (user 2026-11-13)
    g.open += 1
    for (const k of SUMS) g[k] += r[k]
    if (r.kind !== 'match') g.differing += 1
    if (r.expiry && r.now_ea > 0 && (!g.first_expiry || r.expiry < g.first_expiry)) { g.first_expiry = r.expiry; g.first_days = r.days_to_expiry }
  }
  for (const g of by.values()) g.lots.sort((a, b) => Number(b.open) - Number(a.open) || (a.expiry ?? '9999').localeCompare(b.expiry ?? '9999') || (a.lot ?? '').localeCompare(b.lot ?? ''))
  return [...by.values()]
}

const colGroup = columnsOf<ItemGroup>()

export function Comparison({ count }: { count: StockCountRead }) {
  const [onlyDiff, setOnlyDiff] = useState(false)
  const [showClosed, setShowClosed] = useState(false)
  const q = useLookup(['stock-count-compare', count.id, count.version], () => fetchCountCompare(count.id))
  const all = useMemo(() => q.data ?? [], [q.data])
  // An item lists its open lots and adds up only them; a closed lot (no stock, not in Radiant's report, no move
  // after it) shows only on request, greyed, in no total (user 2026-11-13).
  const groups = useMemo(() => group(all.filter((r) => (showClosed || r.open) && (!onlyDiff || r.kind !== 'match'))), [all, onlyDiff, showClosed])
  const lotCols = useMemo(() => compareColumns(), [])
  const itemCols = useMemo<ColumnDef<ItemGroup, unknown>[]>(() => [
    { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => <Text weight="medium">{row.original.item_code}</Text> },
    startHidden(colGroup.text('description', 'Description')),
    { id: 'lots', header: 'Open lots', cell: ({ row: { original: g } }) => (
      <Text wrap="nowrap">
        {formatNumber(g.open)}{g.differing ? <Text tone="warning"> · {g.differing} differ</Text> : null}
        {g.closed ? <Text tone="faint" size="xs" className="block">+ {g.closed} closed</Text> : null}
      </Text>
    ) },
    { id: 'first_expiry', header: 'First expiry', cell: ({ row }) => <ExpiryCell value={row.original.first_expiry} days={row.original.first_days} /> },
    ...figureColumns<ItemGroup>(),
    ...radiantColumns<ItemGroup>(),
  ], [])
  const open = all.filter((r) => r.open)
  const differ = open.filter((r) => r.kind !== 'match').length
  return (
    <Section title="Per item and open lot, at the moment of the report" plain>
      {count.note && <Callout label="Note">{count.note}</Callout>}
      <Cluster gap={3} align="center" size="xs" tone="soft">
        <span>
          {formatNumber(open.length)} open lots · {formatNumber(open.length - differ)} match · {formatNumber(differ)} differ
          {' · '}{formatNumber(all.length - open.length)} closed (not counted)
        </span>
        <Checkbox checked={onlyDiff} onCheckedChange={(v) => setOnlyDiff(v === true)} label="Only the lots that differ" />
        <Checkbox checked={showClosed} onCheckedChange={(v) => setShowClosed(v === true)} label="Show closed lots" />
      </Cluster>
      <LocalTable<ItemGroup> ariaLabel="Comparison by item" columns={itemCols} rows={groups} rowKey={(g) => g.item_code}
        fetching={q.isFetching} emptyTitle={q.isLoading ? 'Loading…' : 'Nothing to compare'}
        expand={(g) => (
          <LocalTable<CountCompareRow> ariaLabel={`Lots of ${g.item_code}`} columns={lotCols} rows={g.lots} density="compact"
            rowKey={(r) => `${r.item_code}|${r.lot ?? ''}`} rowAttrs={(r) => (r.open ? {} : { className: 'opacity-50', 'data-closed': true })} />
        )} />
    </Section>
  )
}
