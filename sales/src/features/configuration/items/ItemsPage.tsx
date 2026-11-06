import { Badge, CrudPage, type ColumnDef , columnsOf } from '@/platform-ui'
import { historyTab } from '../historyTab'
import { downloadItems, fetchItemAudit, itemsClient, unitsText, type ItemCreate, type ItemRead, type ItemUpdate } from './itemsApi'
import { itemCreateSchema, itemEmptyValues, itemFields, itemToFormValues, itemUpdateSchema } from './itemSchema'

const colItemRead = columnsOf<ItemRead>()

const columns: ColumnDef<ItemRead, unknown>[] = [
  colItemRead.text('item_code', 'Item'),
  colItemRead.text('description', 'Description'),
  colItemRead.text('sku', 'SKU'),
  colItemRead.text('gtin', 'GTIN'),
  { id: 'units', header: 'Units', cell: ({ row }) => unitsText(row.original.units) },
  { accessorKey: 'active', header: 'Status',
    cell: ({ row }) => <Badge variant={row.original.active ? 'success' : 'neutral'}>{row.original.active ? 'Active' : 'Inactive'}</Badge> },
]

const errorMessages: Record<string, string> = {
  'item.code_taken': 'An item with this code already exists.',
  'item.ea_required': 'Every item needs the unit EA with factor 1.',
  'entity.stale': 'Someone else changed this item. Close the form and open it again.',
}

/** Configuration → Items (FR-OB-02): the Item Master with each item's units. */
export default function ItemsPage() {
  return (
    <CrudPage<ItemRead, ItemCreate, ItemUpdate>
      title="Items" singular="item"
      description="The Item Master: identifiers used to match order lines, and each item's units in EA."
      client={itemsClient} columns={columns}
      writePermission="sales.config" createSchema={itemCreateSchema} updateSchema={itemUpdateSchema}
      fields={itemFields} immutableOnEdit={['item_code']} emptyValues={itemEmptyValues} toFormValues={itemToFormValues}
      onExport={(path) => downloadItems(path)}
      csvImport errorMessages={errorMessages}
      recordTitle={(i) => i.item_code}
      recordSubtitle={(i) => [i.description, i.units.map((u) => `${u.unit} ${Number(u.factor_ea)} EA`).join(' · ')].filter(Boolean).join(' — ')}
      detailTabs={(i, details) => [details, historyTab('items', i.id, i.version, fetchItemAudit)]}
    />
  )
}
