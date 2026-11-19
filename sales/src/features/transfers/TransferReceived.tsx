import { LocalTable, type ColumnDef, columnsOf, Text } from '@/platform-ui'
import { type ReceiptRowRead } from './transfersApi'

const colReceiptRowRead = columnsOf<ReceiptRowRead>()
const rowColumns: ColumnDef<ReceiptRowRead, unknown>[] = [
  colReceiptRowRead.text('position', 'W07'),
  colReceiptRowRead.text('lx_number', 'LX'),
  { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => row.original.item_code ?? row.original.upc ?? '—' },
  colReceiptRowRead.text('lot', 'Lot'),
  colReceiptRowRead.text('qty_ea', 'EA'),
  { accessorKey: 'transfer_line_no', header: 'To line', cell: ({ row }) => row.original.transfer_line_no ?? <Text tone="danger">not on the transfer</Text> },
]

export type PackedRow = ReceiptRowRead & { file: string; receipt_date: string | null }
const colPacked = columnsOf<PackedRow>()
const packedColumns: ColumnDef<PackedRow, unknown>[] = [
  { accessorKey: 'lx_number', header: 'Pallet (LX)', cell: ({ row }) => row.original.lx_number ?? '—' },
  { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => row.original.item_code ?? row.original.upc ?? '—' },
  colPacked.text('lot', 'Lot'),
  { accessorKey: 'description', header: 'Description', cell: ({ row }) => <Text size="xs">{row.original.description ?? ''}</Text> },
  colPacked.number('qty_ea', 'EA'),
  colPacked.date('receipt_date', 'Received on'),
  colPacked.text('file', '944'),
]

/** What a 944 received, row by row (an order's packed rows, for a transfer). */
export function Received({ rows, label = 'Received for this line' }: { rows: PackedRow[]; label?: string }) {
  return (
    <LocalTable<PackedRow> ariaLabel={label} columns={packedColumns} rows={rows} rowKey={(x, i) => `${x.file}-${x.position}-${i}`}
      sortable={['lot', 'lx_number']} footer={[null, null, 'Total', null, rows.reduce((n, x) => n + x.qty_ea, 0), null, null]} />
  )
}

/** The rows of one 944, as it listed them, and the transfer line each went to. */
export function ReceiptRows({ rows, label }: { rows: ReceiptRowRead[]; label: string }) {
  return (
    <LocalTable<ReceiptRowRead> ariaLabel={label} columns={rowColumns} rows={rows}
      rowKey={(x) => x.position} sortable={['item_code', 'lot', 'transfer_line_no']}
      footer={[null, null, 'Total', null, rows.reduce((n, x) => n + x.qty_ea, 0), null]} />
  )
}
