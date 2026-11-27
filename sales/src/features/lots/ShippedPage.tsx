import { ListPage } from '@/platform-ui'
import { shippedClient, type ShippedRead } from './lotsApi'
import { shippedColumns } from './shippedColumns'

/** Operations → Shipped (user 2026-11-27): what went out to customers, from the applied 945s — not a shadow-run
 *  945, not a second 945 kept only (Lots has every 945 row). A Shopify customer shows as an initial and the city. */
export default function ShippedPage() {
  return (
    <ListPage<ShippedRead> resource={shippedClient} title="Shipped" columns={shippedColumns}
      
      emptyTitle="Nothing shipped yet" />
  )
}
