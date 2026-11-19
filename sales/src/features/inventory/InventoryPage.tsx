import { useState } from 'react'
import { Download } from 'lucide-react'
import { z } from 'zod'
import {
  ActionButton, Button, Callout, IconButton, formatDate, formatNumber, ListPage, LocalTable, RecordSheet, SchemaForm, Section, columnsOf, useNotices, type ColumnDef, type FieldSpec, type QuerySpec, type RecordTab, useRecordList, Text, Cluster, Icon,
} from '@/platform-ui'
import {
  PLACE, SOURCE, adjustStock, exportStock, stockItemsClient, stockLotsClient, stockMovesClient,
  type StockItemRead, type StockLotRead, type StockMoveRead,
} from './inventoryApi'
import { itemColumns, lotColumns } from './inventoryColumns'
import { CountsView } from './CountsView'
import { EMPTY_TEXT } from '@/features/help/pageHelp'

const colMove = columnsOf<StockMoveRead>()
const ADJUST = 'sales.inventory.adjust'


/** An item's active lots (something on hand), nearest expiry first — under its row in the list. */
function ActiveLots({ item }: { item: string }) {
  const spec: QuerySpec = { page: 1, pageSize: 200, filters: [{ field: 'item_code', op: 'eq', value: item }, { field: 'on_hand_ea', op: 'ne', value: '0' }],
    sort: [{ field: 'expiry', desc: false }] }
  const q = useRecordList(stockLotsClient, spec)
  return (
    <LocalTable<StockLotRead> ariaLabel={`Active lots of ${item}`} density="compact" fetching={q.isFetching}
      columns={lotColumns.filter((c) => (c as { accessorKey?: string }).accessorKey !== 'item_code')}
      rows={q.data?.items ?? []} rowKey={(l) => l.id} emptyTitle={q.isLoading ? 'Loading…' : 'No active lot'} />
  )
}

const moveColumns: ColumnDef<StockMoveRead, unknown>[] = [
  colMove.datetime('at', 'When'),
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => row.original.lot ?? '—' },
  colMove.number('qty_ea', 'Qty EA'),
  { id: 'move', header: 'Move', cell: ({ row: { original: m } }) => `${PLACE[m.from_place] ?? m.from_place} → ${PLACE[m.to_place] ?? m.to_place}` },
  { accessorKey: 'source_type', header: 'Source', cell: ({ row }) => SOURCE[row.original.source_type] ?? row.original.source_type },
  { id: 'ref', header: 'Record', cell: ({ row: { original: m } }) => [
    m.order_id && `order #${m.order_id}`, m.transfer_id && `transfer #${m.transfer_id}`, m.document_id && `file #${m.document_id}`,
  ].filter(Boolean).join(' · ') || '—' },
  { accessorKey: 'reason', header: 'Reason', cell: ({ row }) => <Text size="xs">{row.original.reason ?? ''}</Text> },
]

const adjustSchema = z.object({
  lot: z.string().trim().max(50).nullable(),
  qty_ea: z.number({ message: 'Required' }).int().refine((v) => v !== 0, 'Not zero'),
  reason: z.string().trim().min(3, 'Say why (at least 3 characters)').max(500),
})

function AdjustForm({ item, lots, onDone }: { item: StockItemRead; lots: StockLotRead[]; onDone: () => void }) {
  const fields: FieldSpec[] = [
    { name: 'lot', label: 'Lot', type: 'select', options: [{ value: '', label: '(no lot)' }, ...lots.filter((l) => l.lot).map((l) => ({ value: l.lot!, label: `${l.lot} · ${l.on_hand_ea} EA` }))] },
    { name: 'qty_ea', label: 'Quantity EA', type: 'number', required: true, hint: 'Positive: found or counted more. Negative: written off (damaged, destroyed, counted less).' },
    { name: 'reason', label: 'Why', type: 'textarea', required: true, hint: "Kept with the move. Use Radiant's figure as the reference." },
  ]
  return (
    <SchemaForm schema={adjustSchema} fields={fields} submitLabel="Record adjustment" onCancel={onDone}
      errorMessages={{ 'stock.zero': 'The quantity must not be zero.', 'auth.forbidden': 'You may not adjust the inventory.' }}
      defaultValues={{ lot: '', qty_ea: undefined as unknown as number, reason: '' }}
      onSubmit={async (v) => {
        const out = await adjustStock({ item_code: item.item_code, lot: (v.lot as string) || null, qty_ea: Number(v.qty_ea), reason: String(v.reason) })
        onDone()
        return out
      }} />
  )
}

function ItemSheet({ row }: { row: StockItemRead }) {
  const [adjusting, setAdjusting] = useState(false)
  const spec: QuerySpec = { page: 1, pageSize: 500, filters: [{ field: 'item_code', op: 'eq', value: row.item_code }] }
  const lotsQ = useRecordList(stockLotsClient, spec, { errors: 'inline' })
  const movesQ = useRecordList(stockMovesClient, spec, { errors: 'inline' })
  const lots = lotsQ.data?.items ?? []
  const moves = movesQ.data?.items ?? []
  const held = lots.filter((l) => l.on_hand_ea !== 0)
  const tabs: RecordTab[] = [
    { id: 'lots', label: 'Lots', count: held.length, render: () => (
      <LocalTable<StockLotRead> ariaLabel="Lots" columns={lotColumns.filter((c) => (c as { accessorKey?: string }).accessorKey !== 'item_code')}
        rows={held} rowKey={(l) => l.id} emptyTitle="Nothing on hand" />
    ) },
    { id: 'all-lots', label: 'All lots', count: lots.length, render: () => (
      <LocalTable<StockLotRead> ariaLabel="All lots" columns={lotColumns.filter((c) => (c as { accessorKey?: string }).accessorKey !== 'item_code')}
        rows={lots} rowKey={(l) => l.id} />
    ) },
    { id: 'moves', label: 'Stock card', count: movesQ.data?.total, render: () => (
      <Section title="Every move, newest first" plain>
        <Cluster gap={2} align="center" size="xs" tone="muted">
          {(movesQ.data?.total ?? 0) > moves.length ? `The latest ${moves.length} of ${movesQ.data?.total}; the CSV has every one.` : 'Every move of this item.'}
          <ExportButton client={stockMovesClient} spec={{ ...spec, pageSize: 50 }} name={`stock-card-${row.item_code}.csv`} />
        </Cluster>
        <LocalTable<StockMoveRead> ariaLabel="Moves" columns={moveColumns} rows={moves} rowKey={(m) => m.id} />
      </Section>
    ) },
    { id: 'adjust', label: 'Adjust', hidden: !adjusting, render: () => (
      <Section title={`Adjust ${row.item_code} at Radiant`}>
        <AdjustForm item={row} lots={lots} onDone={() => setAdjusting(false)} />
      </Section>
    ) },
  ]
  return (
    <RecordSheet
      title={row.item_code}
      subtitle={row.description ?? undefined}
      size="xl" loading={lotsQ.isLoading} error={lotsQ.error ?? movesQ.error}
      facts={[
        { label: 'On hand EA', value: formatNumber(row.on_hand_ea), tone: row.on_hand_ea < 0 ? 'danger' : 'neutral',
          hint: `received ${formatNumber(row.received_ea)} − shipped ${formatNumber(row.shipped_ea)}`
            + (row.adjusted_ea ? ` ${row.adjusted_ea > 0 ? '+' : '−'} adjusted ${formatNumber(Math.abs(row.adjusted_ea))}` : '') },
        { label: 'On hold EA', value: formatNumber(row.hold_ea), hint: "Radiant's latest report" },
        { label: 'In process EA', value: formatNumber(row.allocated_ea), hint: 'on 940s sent, no 945 yet' },
        { label: 'Available EA', value: formatNumber(row.available_ea), tone: row.available_ea < 0 ? 'warning' : 'neutral',
          hint: 'received − in process − shipped − on hold' },
        { label: 'In transit to Radiant EA', value: formatNumber(row.to_radiant_ea), tone: row.to_radiant_ea < 0 ? 'warning' : 'neutral',
          hint: row.to_radiant_ea < 0 ? 'received more than sent' : undefined },
        { label: 'First expiry', value: row.first_expiry ? formatDate(row.first_expiry) : '—', hint: row.days_to_first_expiry != null ? `${row.days_to_first_expiry} days` : undefined },
      ]}
      actions={<ActionButton size="sm" variant="secondary" permission={ADJUST} onAction={() => setAdjusting(true)}>Adjust…</ActionButton>}
      tabs={tabs}
    />
  )
}

function ExportButton({ client, spec, name }: { client: { exportPath: (s: QuerySpec) => string }; spec: QuerySpec; name: string }) {
  const notices = useNotices()
  return (
    <IconButton label="Export CSV" icon={<Icon icon={Download} />} className="h-8 w-8 rounded-lg border border-neutral-200 bg-white"
      onClick={() => {
        exportStock(client, spec, name).then(() => notices.notify({ key: 'csv', tone: 'success', text: 'CSV exported' }),
          (e: unknown) => notices.notify({ key: 'csv', tone: 'error', text: `Export failed: ${e instanceof Error ? e.message : String(e)}` }))
      }} />
  )
}

const NOTE = (
  <Callout label="How these figures are made">
    Every figure is a sum of moves made from Sales' own records: transfers (943) and their receipts (944), shipments (945),
    deliveries and returns. A Radiant report applied as the start (Radiant reports) sets every lot to Radiant's figure at
    its moment; the moves after it add up from there.
  </Callout>
)

/** Operations → Inventory: the stock at Radiant (design §15), by item or by lot, with each item's stock card. */
type View = 'items' | 'lots' | 'counts'

function ViewSwitch({ view, set }: { view: View; set: (v: View) => void }) {
  const views: [View, string][] = [['items', 'By item'], ['lots', 'By lot'], ['counts', 'Radiant reports']]
  return <>{views.filter(([v]) => v !== view).map(([v, label]) => <Button key={v} size="sm" variant="secondary" onClick={() => set(v)}>{label}</Button>)}</>
}

export default function InventoryPage() {
  const [view, setView] = useState<View>('items')
  const nav = <ViewSwitch view={view} set={setView} />
  if (view === 'counts') return <CountsView toolbar={nav} />
  if (view === 'lots') {
    return (
      <ListPage<StockLotRead>
        key="lots" resource={stockLotsClient} title="Inventory by lot"
        description="Each lot at Radiant: received − shipped (less returns) = on hand; on hand − in process (picked for an order, per Radiant's report) − on hold = available."
        columns={lotColumns} notice={NOTE} emptyTitle="No stock moves yet" emptyText={EMPTY_TEXT.inventory} help="page:inventory"
        toolbar={(ctx) => (<>
          {nav}
          <ExportButton client={stockLotsClient} spec={ctx.spec} name="inventory-lots.csv" />
        </>)}
      />
    )
  }
  return (
    <ListPage<StockItemRead>
      key="items" resource={stockItemsClient} title="Inventory"
      description="Stock at Radiant by item (its open lots): received − shipped (less returns) = on hand; on hand − in process (940 sent, no 945 yet) − on hold = available."
      columns={itemColumns} notice={NOTE} emptyTitle="No stock moves yet" emptyText={EMPTY_TEXT.inventory} help="page:inventory"
      renderDetail={(r) => <ItemSheet row={r} />}
      expand={(r) => (r.lots > 0 || r.on_hand_ea !== 0 ? <ActiveLots item={r.item_code} /> : null)}
      toolbar={(ctx) => (<>
        {nav}
        <ExportButton client={stockItemsClient} spec={ctx.spec} name="inventory-items.csv" />
      </>)}
    />
  )
}
