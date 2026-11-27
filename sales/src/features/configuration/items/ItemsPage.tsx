import { CrudPage } from '@/platform-ui'
import type { ItemCreate, ItemRead, ItemUpdate } from './itemsApi'
import { itemCrud } from './itemCrud'

/** Configuration → Items (FR-OB-02): the Item Master with each item's units; a row opens the item's record. */
export default function ItemsPage() {
  return <CrudPage<ItemRead, ItemCreate, ItemUpdate> {...itemCrud} />
}
