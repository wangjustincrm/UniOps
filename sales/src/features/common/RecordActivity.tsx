import { Timeline, formatDateTime, type TimelineItem } from '@/platform-ui'
import type { TimelineEntry } from './recordFiles'

const WARN = ['On Hold', 'Review', 'Blocked', 'Failed', '940 Rejected', '940 Failed', '940 Timeout', 'Discrepancy', 'Partially Received']
const statusOf = (e: TimelineEntry) => String(e.after?.fulfillment_status ?? e.after?.invoice_status ?? e.after?.status ?? '')
const tone = (e: TimelineEntry): TimelineItem['tone'] => {
  const st = statusOf(e)
  if (e.action === 'create') return 'success'
  if (WARN.includes(st)) return 'warning'
  if (st === 'Cancelled') return 'danger'
  return st ? 'info' : 'neutral'
}
const ENTITY: Record<string, string> = {
  sales_orders: 'Order', sales_order_lines: 'Line', sales_invoices: 'Invoice', sales_shipments: 'Shipment',
  sales_transfers: 'Transfer', sales_transfer_lines: 'Line',
}
const ACTION: Record<string, string> = { create: 'created', update: 'updated', delete: 'deleted' }
const HIDDEN = ['version', 'updated_at', 'updated_by', 'updated_by_name']
const show = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v))

/** A record's activity (its audit, newest first): who did what, why, and what changed. Orders and transfers. */
export function RecordActivity({ entries }: { entries: TimelineEntry[] }) {
  return (
    <Timeline items={entries.map((e, i) => {
      const keys = Object.keys(e.after ?? {}).filter((k) => !HIDDEN.includes(k))
      return {
        key: i, when: formatDateTime(e.at), who: e.user_name, tone: tone(e),
        title: e.reason ?? `${ENTITY[e.entity] ?? e.entity.replace('sales_', '').replace(/_/g, ' ')} ${ACTION[e.action] ?? e.action}`,
        body: e.action === 'update' && keys.length > 0 ? (
          <ul>{keys.slice(0, 6).map((k) => <li key={k} className="break-all"><span className="font-medium">{k.replace(/_/g, ' ')}</span>: {show(e.before?.[k])} → {show(e.after?.[k])}</li>)}</ul>
        ) : undefined,
      }
    })} />
  )
}
