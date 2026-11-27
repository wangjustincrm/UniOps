import { CrudPage } from '@/platform-ui'
import type { ItemCreate, ItemRead, ItemUpdate } from './itemsApi'
import { itemCrud } from './itemCrud'
import { ItemRecord } from './ItemRecord'

/** Configuration → Items (FR-OB-02): the Item Master with each item's units; a row opens the item's record (its facts,
 *  its lists, Edit item) — the same view as its own tab. */
export default function ItemsPage() {
  return <CrudPage<ItemRead, ItemCreate, ItemUpdate> {...itemCrud} detailSize="xl" renderRecord={(r, close) => <ItemRecord row={r} close={close} />} />
}
