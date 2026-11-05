import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  ActionButton, AddressView, Badge, Button, DateCell, DateTimeCell, Tooltip, LocalTable, StatusCell, TrackingLink, queryKeys, requestOpen,
  type ColumnDef, Hint,
} from '@/platform-ui'
import { LineNo } from './LineNo'
import { downloadPackingList, ordersClient, retryShopify, type LineRead, type ShipmentRead, type ShipmentRowRead } from './ordersApi'
import { ResolveRow } from './ResolveRow'

const errorMessages: Record<string, string> = {
  'shipment.not_failed': 'The Shopify write is not failed.',
  'auth.forbidden': 'You do not have permission to retry Shopify writes.',
}

// The cases a row shipped, when the Item Master knows the case (EA per CASE).
function cases(r: ShipmentRowRead): string | null {
  if (!r.case_factor) return null
  const n = r.qty_shipped_ea / r.case_factor
  return `${Number.isInteger(n) ? n : n.toFixed(1)} CS`
}

// Whether the row found its order line: matched, none to review, or not linked yet.
function Match({ r }: { r: ShipmentRowRead }) {
  if (r.needs_review) return <Badge variant="danger">matches no order line</Badge>
  if (r.order_line_id != null) return <Badge variant="success">Matched</Badge>
  return <Badge variant="neutral">Not linked</Badge>
}

const rowColumns = (s: ShipmentRead, numbers: Map<number, number>, orderId: number, lines: LineRead[]): ColumnDef<ShipmentRowRead, unknown>[] => [
  { id: 'line', header: 'Line', cell: ({ row: { original: r } }) => r.line_no != null
    ? <span className="whitespace-nowrap">#<LineNo lineNo={r.line_no} numbers={numbers} /> · {r.item_code ?? '?'}</span> : '—' },
  { accessorKey: 'item_code', header: 'Item code', cell: ({ row }) => row.original.item_code ?? '—' },
  { accessorKey: 'upc', header: 'UPC', cell: ({ row }) => <span className="tabular-nums">{row.original.upc ?? '—'}</span> },
  { accessorKey: 'description', header: 'Description', cell: ({ row }) => row.original.description ?? '—' },
  { accessorKey: 'qty_shipped_ea', header: 'Qty EA', cell: ({ row: { original: r } }) => (
    <span className="whitespace-nowrap tabular-nums">{r.qty_shipped_ea}{cases(r) && <Hint> ({cases(r)})</Hint>}</span>) },
  { accessorKey: 'lot', header: 'Lot', cell: ({ row }) => row.original.lot ?? '—' },
  { accessorKey: 'expiry', header: 'Expiry', cell: ({ row }) => <DateCell value={row.original.expiry} /> },
  { accessorKey: 'sscc', header: 'SSCC', cell: ({ row }) => <span className="tabular-nums">{row.original.sscc ?? '—'}</span> },
  { accessorKey: 'lx_number', header: 'LX no.', cell: ({ row }) => row.original.lx_number ?? row.original.lx_index },
  { accessorKey: 'tracking', header: 'Tracking', cell: ({ row: { original: r } }) => r.tracking
    ? <TrackingLink number={r.tracking} url={r.tracking_url} /> : '—' },
  { id: 'match', header: 'Match', cell: ({ row }) => (
    <span className="inline-flex flex-wrap items-center gap-1">
      <Match r={row.original} />
      {row.original.needs_review && <ResolveRow r={row.original} shipmentId={s.id} orderId={orderId} lines={lines} />}
    </span>
  ) },
]

/** The 945's line items, one table per pallet (LX) with its subtotal. */
function LineItems({ s, numbers, orderId, lines }: { s: ShipmentRead; numbers: Map<number, number>; orderId: number; lines: LineRead[] }) {
  const pallets = [...new Set(s.rows.map((r) => r.lx_index))]
  const columns = rowColumns(s, numbers, orderId, lines)
  return (
    <div className="flex flex-col gap-2">
      {pallets.map((lx) => {
        const rows = s.rows.filter((r) => r.lx_index === lx)
        const first = rows[0]
        const ea = rows.reduce((n, r) => n + r.qty_shipped_ea, 0)
        return (
          <section key={lx} aria-label={`Pallet ${first.lx_number ?? lx}`} className="flex flex-col gap-1">
            <h4 className="text-xs font-medium text-neutral-600">
              Pallet LX {first.lx_number ?? lx}{first.sscc && <> · SSCC <span className="tabular-nums">{first.sscc}</span></>} · {ea} EA
            </h4>
            <LocalTable<ShipmentRowRead> ariaLabel={`Shipment ${s.id} pallet ${first.lx_number ?? lx} line items`} columns={columns} rows={rows}
              rowKey={(r) => `${r.lx_index}-${r.w12_index}`} initiallyHidden={['upc', 'sscc']}
              rowAttrs={(r) => (r.needs_review ? { className: 'bg-danger-50' } : {})}
              footer={[null, null, null, 'Pallet subtotal', <span key="ea" className="tabular-nums">{ea}</span>, null, null, null, null, null, null]} />
          </section>
        )
      })}
    </div>
  )
}

function Retry({ s, orderId }: { s: ShipmentRead; orderId: number }) {
  const qc = useQueryClient()
  const m = useMutation({
    mutationFn: () => retryShopify(s.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.detail(ordersClient, orderId) }),
  })
  return (
    <span className="inline-flex flex-col gap-1">
      <ActionButton size="sm" variant="secondary" permission="sales.order.operate" errorMessages={errorMessages} done="Shopify write queued again"
        onAction={() => m.mutateAsync()}>
        Retry in Shopify
      </ActionButton>
    </span>
  )
}

// Where the ship date came from, and the 945's W27 mode, in words (the codes in a tooltip).
const DATE_SOURCE: Record<string, string> = { G62: "From the 945's G62*11 (ship date)", DTM: "From the 945's DTM*011 (shipped)" }
const MODE: Record<string, string> = { U: 'Parcel', LT: 'LTL', M: 'Truck' }

function Pair({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <Hint as="dt">{label}</Hint>
      <dd className="min-w-0 break-words text-neutral-800">{children}</dd>
    </div>
  )
}

/** The order's 945s, each as the warehouse sent it: where it went, by which
 *  carrier (in words, its tracking linked), its control numbers and file (a 997
 *  sent back only when there is one: Sales sends none for 945s today), the
 *  checks that notified (FR-OB-25d), the Shopify write-back (FR-OB-11), and its
 *  line items pallet by pallet, each matched to its order line. A split
 *  shipment's parts ("9451a", "9451b") are cards Part a, Part b. */
export function Shipments({ shipments, orderId, lineNumbers = new Map(), lines = [] }: {
  shipments: ShipmentRead[]; orderId: number
  /** The order's lines, to resolve a 945 row that matches none. */
  lines?: LineRead[]
  /** The order's line numbers as shown (`lineNumbers` in ordersApi); none: the line_no as is. */
  lineNumbers?: Map<number, number>
}) {
  const navigate = useNavigate()
  if (shipments.length === 0) return null
  return (
    <section aria-label="Shipments" className="flex flex-col gap-3">
      {shipments.map((s) => (
        <div key={s.id} aria-label={`Shipment ${s.id}`} role="group" className="flex flex-col gap-3 rounded-lg border border-neutral-200 bg-white p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-neutral-800">945 #{s.document_id}</span>
            {s.part && <Badge variant="info">Part {s.part}</Badge>}
            {!s.shadow && !s.applied && <Badge variant="warning">Not applied (second 945)</Badge>}
            <span className="ml-auto">
              <ActionButton size="sm" variant="secondary" onAction={() => downloadPackingList(orderId, s.id)}>Packing list (PDF)</ActionButton>
            </span>
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
            <Pair label="Ship date">
              {s.ship_date ? <Tooltip tip={DATE_SOURCE[s.ship_date_source ?? ''] ?? 'From the 945'}><DateCell value={s.ship_date} /></Tooltip> : '—'}
            </Pair>
            <Pair label="Ship to (945)">{s.ship_to ? <AddressView value={s.ship_to} /> : '—'}</Pair>
            <Pair label="Carrier">
              {s.carrier ? <>
                {s.carrier_name ? <>{s.carrier_name} <Hint>({s.carrier})</Hint></> : s.carrier}
                {s.mode && <> · <Tooltip tip={`W27 mode ${s.mode}`}>{MODE[s.mode] ?? s.mode}</Tooltip></>}
              </> : '—'}
            </Pair>
            <Pair label="Tracking">
              {s.tracking_links?.length
                ? <span className="flex flex-wrap gap-1">{s.tracking_links.map((t) => <TrackingLink key={t.number} number={t.number} url={t.url} />)}</span>
                : 'None'}
            </Pair>
            <Pair label="BOL">{s.bol ?? '—'}</Pair>
            <Pair label="940 ref">{s.our_reference ?? '—'}</Pair>
            <Pair label="Packed">{s.rows.length} {s.rows.length === 1 ? 'row' : 'rows'} · {s.rows.reduce((n, r) => n + r.qty_shipped_ea, 0)} EA</Pair>
            <Pair label="ISA13 / GS06 / ST02">
              <span className="tabular-nums">{s.isa13 ?? '—'} / {s.gs06 ?? '—'} / {s.set_control ?? '—'}</span>
            </Pair>
            <Pair label="Received">{s.received_at ? <DateTimeCell value={s.received_at} /> : '—'}</Pair>
            <Pair label="Source file">
              <Button size="sm" variant="ghost" className="h-auto max-w-full px-0 text-left text-primary-700 underline-offset-2 hover:underline"
                onClick={() => { requestOpen('/documents', s.document_id); navigate('/documents') }}>
                <span className="break-all">{s.document_name ?? `Document #${s.document_id}`}</span>
              </Button>
            </Pair>
            {s.ack_sent && <Pair label="997 sent back">{s.ack_sent}</Pair>}
            <Pair label="Shopify">
              <span className="inline-flex flex-wrap items-center gap-1">
                <StatusCell value={s.shopify_status} />
                {(s.shopify_status === 'Failed' || s.shopify_status === 'Suppressed') && <Retry s={s} orderId={orderId} />}
              </span>
            </Pair>
          </dl>
          {s.shopify_error && <p className="text-xs text-danger-600">{s.shopify_error}</p>}
          {s.checks && s.checks.length > 0 && (
            <ul aria-label="945 checks" className="text-warning-700">
              {s.checks.map((c, i) => <li key={i}>{c.check}: {c.message}</li>)}
            </ul>
          )}
          {s.rows.length > 0 && <LineItems s={s} numbers={lineNumbers} orderId={orderId} lines={lines} />}
        </div>
      ))}
    </section>
  )
}
