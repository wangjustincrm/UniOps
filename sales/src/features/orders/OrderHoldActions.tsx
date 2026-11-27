import { RecordActionBar } from '@/platform-ui'
import type { OrderDetail } from './ordersApi'
import { useOrderHoldItems } from './orderHoldItems'

/** The hold actions alone, as a bar (a page or test that shows only them). */
export function OrderHoldActions({ o }: { o: OrderDetail }) {
  const { items, node } = useOrderHoldItems(o)
  return <><RecordActionBar items={items} />{node}</>
}
