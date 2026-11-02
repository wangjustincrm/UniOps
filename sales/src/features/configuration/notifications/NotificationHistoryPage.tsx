import { Badge, DateTimeCell, ListPage, type ColumnDef } from '@/platform-ui'
import { historyClient, type NotificationRead } from './notificationsApi'

const DELIVERY: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
  DONE: 'success', 'Task written': 'success', PENDING: 'neutral', IN_FLIGHT: 'neutral', SUPPRESSED: 'warning',
  FAILED: 'danger', CANCELLED: 'neutral',
}
const DELIVERY_LABEL: Record<string, string> = { DONE: 'Sent', SUPPRESSED: 'Not sent (switch off)', PENDING: 'Queued', IN_FLIGHT: 'Sending' }

const columns: ColumnDef<NotificationRead, unknown>[] = [
  { accessorKey: 'at', header: 'When', cell: ({ row }) => <DateTimeCell value={row.original.at} /> },
  { accessorKey: 'event_type', header: 'Event', cell: ({ row }) => row.original.event_label + (row.original.test ? ' (test)' : '') },
  { accessorKey: 'channel', header: 'Channel', cell: ({ row }) => (row.original.channel === 'TASK' ? 'Task' : 'Email') },
  { id: 'recipients', header: 'To', cell: ({ row }) => row.original.recipients.join(', ') || 'the operator' },
  { accessorKey: 'subject', header: 'Subject' },
  { id: 'delivery', header: 'Delivery', cell: ({ row: { original: n } }) => n.delivery
    ? <Badge variant={DELIVERY[n.delivery] ?? 'neutral'}>{DELIVERY_LABEL[n.delivery] ?? n.delivery}</Badge> : '—' },
]

/** Operations → Notification history (FR-OB-56: "the history of notifications sent"). */
export default function NotificationHistoryPage() {
  return (
    <ListPage<NotificationRead>
      resource={historyClient}
      title="Notification history"
      description="Every task and email the notification rules made, newest first. An email's delivery is its send's status."
      columns={columns}
      emptyTitle="No notification yet"
    />
  )
}
