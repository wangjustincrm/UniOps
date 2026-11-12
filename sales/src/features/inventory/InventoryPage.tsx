import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { z } from 'zod'
import {
  ActionButton, Button, Callout, IconButton, formatDate, formatNumber, ListPage, LocalTable, RecordSheet, SchemaForm, Section, columnsOf, queryKeys, useNotices,
  type ColumnDef, type FieldSpec, type QuerySpec, type RecordTab,
} from '@/platform-ui'
import {
  PLACE, SOURCE, adjustStock, exportStock, stockItemsClient, stockLotsClient, stockMovesClient,
  type StockItemRead, type StockLotRead, type StockMoveRead,
} from './inventoryApi'

const colItem = columnsOf<StockItemRead>()
const colLot = columnsOf<StockLotRead>()
const colMove = columnsOf<StockMoveRead>()
const ADJUST = 'sales.inventory.adjust'

const itemColumns: ColumnDef<StockItemRead, unknown>[] = [
  { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => <span className="font-medium">{row.original.item_code}</span> },
  colItem.text('description', 'Description'),
  colItem.number('on_hand_ea', 'On hand EA'),
  colItem.number('allocated_ea', 'Allocated EA'),
  colItem.number('available_ea', 'Available EA'),
  colItem.number('to_radiant_ea', 'In transit to Radiant EA'),
  colItem.number('to_customer_ea', 'In transit to customers EA'),
  colItem.number('returning_ea', 'Returns on the way EA'),
  colItem.number('lots', 'Lots on hand'),
  colItem.date('first_expiry', 'First expiry'),
  colItem.datetime('last_move_at', 'Last move'),
]

const lotColumns: ColumnDef<StockLotRead, unknown>[] = [
  colLot.text('item_code', 'Item'),
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => <span className="font-medium">{row.original.lot ?? '(no lot)'}</span> },
  colLot.date('expiry', 'Expiry'),
  colLot.number('days_to_expiry', 'Days to expiry'),
  colLot.number('on_hand_ea', 'On hand EA'),
  colLot.number('received_ea', 'Received EA'),
  colLot.number('shipped_ea', 'Shipped EA'),
  colLot.number('to_customer_ea', 'In transit to customers EA'),
  colLot.number('returning_ea', 'Returns on the way EA'),
  colLot.datetime('last_move_at', 'Last move'),
]

const moveColumns: ColumnDef<StockMoveRead, unknown>[] = [
  colMove.datetime('at', 'When'),
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => row.original.lot ?? '—' },
  colMove.number('qty_ea', 'Qty EA'),
  { id: 'move', header: 'Move', cell: ({ row: { original: m } }) => `${PLACE[m.from_place] ?? m.from_place} → ${PLACE[m.to_place] ?? m.to_place}` },
  { accessorKey: 'source_type', header: 'Source', cell: ({ row }) => SOURCE[row.original.source_type] ?? row.original.source_type },
  { id: 'ref', header: 'Record', cell: ({ row: { original: m } }) => [
    m.order_id && `order #${m.order_id}`, m.transfer_id && `transfer #${m.transfer_id}`, m.document_id && `file #${m.document_id}`,
  ].filter(Boolean).join(' · ') || '—' },
  { accessorKey: 'reason', header: 'Reason', cell: ({ row }) => <span className="text-xs">{row.original.reason ?? ''}</span> },
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
  const lotsQ = useQuery({ queryKey: [...queryKeys.list(stockLotsClient), 'item', row.item_code], queryFn: () => stockLotsClient.list(spec) })
  const movesQ = useQuery({ queryKey: [...queryKeys.list(stockMovesClient), 'item', row.item_code], queryFn: () => stockMovesClient.list(spec) })
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
        <div className="flex items-center gap-2 text-xs text-neutral-500">
          {(movesQ.data?.total ?? 0) > moves.length ? `The latest ${moves.length} of ${movesQ.data?.total}; the CSV has every one.` : 'Every move of this item.'}
          <ExportButton client={stockMovesClient} spec={{ ...spec, pageSize: 50 }} name={`stock-card-${row.item_code}.csv`} />
        </div>
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
        { label: 'On hand EA', value: formatNumber(row.on_hand_ea), tone: row.on_hand_ea < 0 ? 'danger' : 'neutral' },
        { label: 'Allocated EA', value: formatNumber(row.allocated_ea), hint: 'on 940s sent, not shipped' },
        { label: 'Available EA', value: formatNumber(row.available_ea), tone: row.available_ea < 0 ? 'warning' : 'neutral' },
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
    <IconButton label="Export CSV" icon={<Download className="h-4 w-4" />} className="h-8 w-8 rounded-lg border border-neutral-200 bg-white"
      onClick={() => {
        exportStock(client, spec, name).then(() => notices.notify({ key: 'csv', tone: 'success', text: 'CSV exported' }),
          (e: unknown) => notices.notify({ key: 'csv', tone: 'error', text: `Export failed: ${e instanceof Error ? e.message : String(e)}` }))
      }} />
  )
}

const NOTE = (
  <Callout label="How these figures are made">
    Every figure is a sum of moves made from Sales' own records: transfers (943) and their receipts (944), shipments (945),
    deliveries and returns. They start from the first data Sales holds; shipments that never reached Sales (retailer orders
    before July 2026) are not in them, so compare with Radiant's portal and record any difference as an adjustment.
  </Callout>
)

/** Operations → Inventory: the stock at Radiant (design §15), by item or by lot, with each item's stock card. */
export default function InventoryPage() {
  const [byLot, setByLot] = useState(false)
  if (byLot) {
    return (
      <ListPage<StockLotRead>
        key="lots" resource={stockLotsClient} title="Inventory by lot"
        description="Each lot at Radiant: on hand, received, shipped, expiry."
        columns={lotColumns} notice={NOTE} emptyTitle="No stock moves yet"
        toolbar={(ctx) => (<>
          <Button size="sm" variant="secondary" onClick={() => setByLot(false)}>By item</Button>
          <ExportButton client={stockLotsClient} spec={ctx.spec} name="inventory-lots.csv" />
        </>)}
      />
    )
  }
  return (
    <ListPage<StockItemRead>
      key="items" resource={stockItemsClient} title="Inventory"
      description="Stock at Radiant by item: on hand, allocated to 940s, available, and what is on its way."
      columns={itemColumns} notice={NOTE} emptyTitle="No stock moves yet"
      renderDetail={(r) => <ItemSheet row={r} />}
      toolbar={(ctx) => (<>
        <Button size="sm" variant="secondary" onClick={() => setByLot(true)}>By lot</Button>
        <ExportButton client={stockItemsClient} spec={ctx.spec} name="inventory-items.csv" />
      </>)}
    />
  )
}
