import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import {
  Badge, Button, DateCell, Dialog, ListPage, Textarea, type ColumnDef, type Filter,
  IconButton, Callout, TrackingLink, useNotices,
} from '@/platform-ui'
import { PartnerCell } from '@/features/common/PartnerCell'
import { exportLots, lotsClient, parseLots, type LotRead } from './lotsApi'

const MAX_LOTS = 100  // the list query's limit on one filter's values

const columns: ColumnDef<LotRead, unknown>[] = [
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => <span className="font-medium">{row.original.lot ?? '—'}</span> },
  { accessorKey: 'item_code', header: 'Item' },
  { accessorKey: 'expiry', header: 'Expiry', cell: ({ row }) => <DateCell value={row.original.expiry} /> },
  { accessorKey: 'qty_shipped_ea', header: 'Qty EA' },
  { accessorKey: 'ship_date', header: 'Ship date', cell: ({ row }) => <DateCell value={row.original.ship_date} /> },
  { accessorKey: 'order_ref', header: 'Order ref' },
  { accessorKey: 'order_number', header: 'Internal no.' },
  { accessorKey: 'partner_name', header: 'Partner', cell: ({ row }) => <PartnerCell name={row.original.partner_name} code={row.original.partner_code} /> },
  { accessorKey: 'ship_to_city', header: 'Ship to', cell: ({ row }) => [row.original.ship_to_city, row.original.ship_to_postal].filter(Boolean).join(' · ') },
  { accessorKey: 'tracking', header: 'Tracking', cell: ({ row }) => row.original.tracking
    ? <TrackingLink number={row.original.tracking} url={row.original.tracking_url} /> : '—' },
  { accessorKey: 'sscc', header: 'Pallet (SSCC)', cell: ({ row }) => row.original.sscc ?? '—' },
  { accessorKey: 'document_id', header: '945', cell: ({ row: { original: r } }) => (
    <span className="inline-flex items-center gap-1">#{r.document_id}
      {r.shadow ? <Badge variant="neutral">Shadow run</Badge> : !r.applied && <Badge variant="warning">Second 945</Badge>}
    </span>) },
]

/** How far a recall reaches: the orders and customers its lots went to. */
function RecallReach({ lots }: { lots: string[] }) {
  const q = useQuery({
    queryKey: ['lots', 'recall', lots],
    queryFn: () => lotsClient.list({ page: 1, pageSize: 1, filters: [{ field: 'lot', op: 'in', value: lots }],
      group: [{ field: 'partner_name' }, { field: 'order_ref' }] }),
  })
  if (!q.data) return null
  const groups = q.data.groups ?? []
  const customers = new Set(groups.map((g) => g.key[0])).size
  return <span>{q.data.total} rows · {groups.length} {groups.length === 1 ? 'order' : 'orders'} · {customers} {customers === 1 ? 'customer' : 'customers'}</span>
}

function RecallDialog({ open, onOpenChange, onApply }: { open: boolean; onOpenChange: (o: boolean) => void; onApply: (lots: string[]) => void }) {
  const [text, setText] = useState('')
  const lots = parseLots(text)
  const tooMany = lots.length > MAX_LOTS
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Recall: find where lots went"
      description="Paste one lot or several (one per line, or separated by commas). Every order and customer they went to is listed, ready to export."
      footer={<>
        <Button variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button disabled={lots.length === 0 || tooMany} onClick={() => { onApply(lots); onOpenChange(false) }}>
          Find {lots.length > 0 ? `${lots.length} ${lots.length === 1 ? 'lot' : 'lots'}` : ''}
        </Button>
      </>}>
      <Textarea aria-label="Lots" rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder={'LOT-A\nLOT-B'} />
      {tooMany && <p className="mt-1 text-sm text-danger-600">At most {MAX_LOTS} lots at a time.</p>}
    </Dialog>
  )
}

/** Operations → Lots: every pallet row a 945 reported, with the order and
 *  customer it went to — for tracing and recalls. A recall (one lot or a
 *  pasted list) lists every order and customer they reached, as CSV too.
 *  Nothing here is ever deleted (FR-OB-05). */
/** Export CSV of what the list shows; the result in its status row. */
function ExportLots({ spec }: { spec: Parameters<typeof exportLots>[0] }) {
  const notices = useNotices()
  return (
    <IconButton label="Export CSV" icon={<Download className="h-4 w-4" />} className="h-8 w-8 rounded-lg border border-neutral-200 bg-white"
      onClick={() => {
        exportLots(spec).then(() => notices.notify({ key: 'csv', tone: 'success', text: 'CSV exported' }),
          (e: unknown) => notices.notify({ key: 'csv', tone: 'error', text: `Export failed: ${e instanceof Error ? e.message : String(e)}` }))
      }} />
  )
}

export default function LotsPage() {
  const [recall, setRecall] = useState<string[] | null>(null)
  const [asking, setAsking] = useState(false)
  const fixed: Filter[] | undefined = recall ? [{ field: 'lot', op: 'in', value: recall }] : undefined
  return (
    <>
      <ListPage<LotRead>
        key={recall ? recall.join('|') : 'all'}
        resource={lotsClient}
        title="Lots"
        description="Every pallet row the warehouse's 945s reported: lot, expiry, quantity, and the order and customer it went to."
        columns={columns}
        fixed={fixed}
        emptyTitle={recall ? 'None of these lots has shipped' : 'No 945 rows yet'}
        notice={recall && (
          <Callout label="Recall">
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-medium">Recall: {recall.join(', ')}</span>
              <span>—</span><RecallReach lots={recall} />
              <Button size="sm" variant="secondary" className="ml-auto" onClick={() => setRecall(null)}>Clear recall</Button>
            </span>
          </Callout>
        )}
        toolbar={(ctx) => (
          <>
            <Button size="sm" variant="secondary" onClick={() => setAsking(true)}>Recall…</Button>
            <ExportLots spec={ctx.spec} />
          </>
        )}
      />
      <RecallDialog open={asking} onOpenChange={setAsking} onApply={setRecall} />
    </>
  )
}
