import { Timeline, formatDateTime, type TimelineItem } from '@/platform-ui'
import type { OrderDetail } from '../ordersApi'

const timelineTone = (e: OrderDetail['timeline'][number]): TimelineItem['tone'] => {
  const st = String(e.after?.fulfillment_status ?? e.after?.invoice_status ?? '')
  if (e.action === 'create') return 'success'
  if (['On Hold', 'Review', 'Blocked', 'Failed', '940 Rejected', '940 Failed', '940 Timeout'].includes(st)) return 'warning'
  if (st === 'Cancelled') return 'danger'
  return st ? 'info' : 'neutral'
}
const ENTITY: Record<string, string> = { sales_orders: 'Order', sales_order_lines: 'Line', sales_invoices: 'Invoice', sales_shipments: 'Shipment' }
const ACTION: Record<string, string> = { create: 'created', update: 'updated', delete: 'deleted' }
const show = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v))

export function ActivityTab({ o }: { o: OrderDetail }) {
  return (
    <Timeline items={o.timeline.map((e, i) => {
      const keys = Object.keys(e.after ?? {}).filter((k) => !['version', 'updated_at', 'updated_by', 'updated_by_name'].includes(k))
      return {
        key: i, when: formatDateTime(e.at), who: e.user_name, tone: timelineTone(e),
        title: e.reason ?? `${ENTITY[e.entity] ?? e.entity.replace('sales_', '').replace(/_/g, ' ')} ${ACTION[e.action] ?? e.action}`,
        body: e.action === 'update' && keys.length > 0 ? (
          <ul>{keys.slice(0, 6).map((k) => <li key={k} className="break-all"><span className="font-medium">{k.replace(/_/g, ' ')}</span>: {show(e.before?.[k])} → {show(e.after?.[k])}</li>)}</ul>
        ) : undefined,
      }
    })} />
  )
}
