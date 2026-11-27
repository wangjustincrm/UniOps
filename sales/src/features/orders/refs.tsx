import { RefLink } from '@/platform-ui'
import { ordersClient } from './ordersApi'

// An item code or a lot in an order's detail (its lines, its packing, its 945 rows) as a link to its record. Whether
// this person may read it is the server's word, in the orders' /meta (`refs`, the types an order's detail names).

export function ItemRef({ code }: { code: string }) {
  return <RefLink record={{ type: 'item', key: code }} from={ordersClient}>{code}</RefLink>
}

export function LotRef({ lot }: { lot: string }) {
  return <RefLink record={{ type: 'lot', key: lot }} from={ordersClient}>{lot}</RefLink>
}
