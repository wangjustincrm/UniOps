import { RecordActivity } from '@/features/common/RecordActivity'
import type { OrderDetail } from '../ordersApi'

export function ActivityTab({ o }: { o: OrderDetail }) {
  return <RecordActivity entries={o.timeline} />
}
