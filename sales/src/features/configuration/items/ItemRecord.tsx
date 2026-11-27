import { useContext, useState } from 'react'
import {
  Badge, ConfirmAction, CrudEditForm, DetailList, RecordSheet, RecordSheetHostContext, compareRows, formatDate, formatNumber, queryKeys,
  useRecordList, useRefresh, type BarItem, type Filter, type ListScope, type QuerySpec, type RecordFact,
} from '@/platform-ui'
import { stockItemsClient } from '@/features/inventory/inventoryApi'
import { itemsClient, unitsText, type ItemRead } from './itemsApi'
import { itemCrud } from './itemCrud'
import { useItemTabs } from './itemTabs'

const CONFIG = 'sales.config'
const yes = (b: boolean) => (b ? 'Yes' : 'No')

/** An item's record (user 2026-11-27) — in the Items list's sheet and in its own tab (/items/:code) alike: what it
 *  is and how much there is first (the stock at Radiant), the edit form behind "Edit item", and every list that
 *  concerns it as a tab — its lots, its stock card, what shipped, its orders, invoices and transfers, its partner
 *  codes — each the list itself, scoped to the item (locked, the server's), and History. */
export function ItemRecord({ row, close }: { row: ItemRead; close: () => void }) {
  const host = useContext(RecordSheetHostContext)
  const code = row.item_code
  const byCode: QuerySpec = { page: 1, pageSize: 1, filters: [{ field: 'item_code', op: 'eq', value: code }] }
  const itemQ = useRecordList(itemsClient, byCode, { errors: 'inline' })
  const stockQ = useRecordList(stockItemsClient, byCode, { errors: 'inline' })
  const item = itemQ.data?.items[0] ?? row
  const stock = stockQ.data?.items[0]
  // Refresh reads both: its stock (the facts) and the item itself.
  const refresh = useRefresh(stockQ, { queryKey: [...queryKeys.list(stockItemsClient), byCode], compare: compareRows((r) => r.id),
    noChange: 'Up to date', auto: 'follow', also: [itemQ] })
  const [editing, setEditing] = useState(false)
  const [deactivating, setDeactivating] = useState(false)
  const [ownTab, setOwnTab] = useState<string | undefined>(undefined)
  const show = (id: string) => (host ? host.onTabChange?.(id) : setOwnTab(id))
  const filters: Filter[] = [{ field: 'item_code', op: 'eq', value: code }]
  const scope: ListScope = { kind: 'item', label: `Item = ${code}`, filters }
  const tabs = useItemTabs(item, scope, editing ? (
    <CrudEditForm row={item} props={itemCrud} done={() => setEditing(false)} />
  ) : (
    <DetailList items={[
      ['Item code', item.item_code], ['Description', item.description ?? '—'], ['SKU', item.sku ?? '—'], ['UPC', item.upc ?? '—'],
      ['GTIN', item.gtin ?? '—'], ['Gross weight per EA (lb)', item.gross_weight_lb_per_ea ?? '—'], ['Units', unitsText(item.units)],
      ['Held in the warehouse', yes(item.warehouse_held)], ['Active', yes(item.active)],
    ]} />
  ))
  const facts: RecordFact[] = [
    { label: 'On hand EA', value: stock ? formatNumber(stock.on_hand_ea) : '—', tone: (stock?.on_hand_ea ?? 0) < 0 ? 'danger' : 'neutral' },
    { label: 'Available EA', value: stock ? formatNumber(stock.available_ea) : '—', tone: (stock?.available_ea ?? 0) < 0 ? 'warning' : 'neutral',
      hint: 'on hand − in process − on hold' },
    { label: 'In process EA', value: stock ? formatNumber(stock.allocated_ea) : '—', hint: 'on 940s sent, no 945 yet' },
    { label: 'First expiry', value: stock?.first_expiry ? formatDate(stock.first_expiry) : '—',
      hint: stock?.days_to_first_expiry != null ? `${stock.days_to_first_expiry} days` : undefined },
  ]
  const bar: BarItem[] = [
    ...(!editing ? [{ id: 'edit-item', label: 'Edit item', permission: CONFIG, primary: 10, refreshes: false,
      onSelect: () => { setEditing(true); show('overview') } }] : []),
    ...(item.active ? [{ id: 'deactivate-item', label: 'Deactivate…', permission: CONFIG, tone: 'destructive' as const, opensDialog: true,
      refreshes: false, hint: 'It stays on existing records but can no longer be chosen', onSelect: () => setDeactivating(true) }] : []),
  ]
  return (
    <>
      <RecordSheet title={code} subtitle={item.description ?? undefined} record={{ type: 'item', key: code }} size="xl"
        badges={!item.active ? <Badge variant="neutral">Inactive</Badge> : undefined}
        refresh={refresh} facts={facts} bar={bar} tabs={tabs} {...(host ? {} : { tab: ownTab, onTabChange: setOwnTab })} />
      <ConfirmAction open={deactivating} onOpenChange={setDeactivating} title={`Deactivate ${code}?`} destructive confirmLabel="Deactivate"
        description="It stays on existing records but can no longer be chosen. It is never deleted." invalidates={[queryKeys.list(itemsClient)]}
        onConfirm={async () => { await itemsClient.deactivate(item.id); close() }} />
    </>
  )
}
