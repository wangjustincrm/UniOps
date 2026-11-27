import { useParams } from 'react-router-dom'
import { RecordSheet, useRecordList } from '@/platform-ui'
import { itemsClient } from '@/features/configuration/items/itemsApi'
import { ItemRecord } from '@/features/configuration/items/ItemRecord'
import { RecordRoute } from './RecordRoute'

/** /items/:code — an item in its own tab: the same view as the Items list's sheet (ItemRecord). */
export default function ItemRecordPage() {
  const code = useParams().code ?? ''
  const q = useRecordList(itemsClient, { page: 1, pageSize: 1, filters: [{ field: 'item_code', op: 'eq', value: code }] }, { errors: 'inline' })
  const row = q.data?.items[0]
  const missing = q.data && !row ? new Error(`No item ${code}`) : null
  return (
    <RecordRoute type="item" title={row?.item_code}>
      {(close) => (row
        ? <ItemRecord row={row} close={close} />
        : <RecordSheet title={code} loading={!q.error && !missing} error={q.error ?? missing} tabs={[]} />)}
    </RecordRoute>
  )
}
