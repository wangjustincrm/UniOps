import { ListPage, type ColumnDef, Muted, columnsOf, Text } from '@/platform-ui'
import { transfersClient, type TransferRead } from './transfersApi'
import { EMPTY_TEXT } from '@/features/help/pageHelp'
import { MANAGE } from './transferRules'
import { TransferForm } from './TransferForms'
import { TransferSheet } from './TransferSheet'

const colTransferRead = columnsOf<TransferRead>()
const columns: ColumnDef<TransferRead, unknown>[] = [
  colTransferRead.text('transfer_number', 'Transfer'),
  colTransferRead.text('from_warehouse', 'From'),
  colTransferRead.text('to_warehouse', 'To'),
  colTransferRead.status('status', 'Status'),
  { accessorKey: 'items', header: 'Items', cell: ({ row }) => <Text wrap="clamp1">{row.original.items ?? ''}</Text> },
  colTransferRead.number('sent_ea', 'Sent EA'),
  colTransferRead.number('received_ea', 'Received EA'),
  colTransferRead.date('receipt_date', 'Received on'),
  colTransferRead.number('attachments', 'Attachments'),
  colTransferRead.datetime('created_at', 'Created'),
  { accessorKey: 'error', header: 'Note', cell: ({ row }) => <Muted className="line-clamp-1">{row.original.error ?? ''}</Muted> },
]

/** Transfers (FR-OB-80 … 84): Draft → Ready to send → the 943 → the 997 → the
 *  944, its lines allocated and summed per lot. */
export default function TransfersPage() {
  return (
    <ListPage<TransferRead>
      resource={transfersClient}
      title="Transfers"
      description="Stock sent from the plant to the warehouse (Radiant): a 943 says what is coming, its 944 says what arrived."
      columns={columns}
      emptyTitle="No transfers yet"
      emptyText={EMPTY_TEXT.transfers}
      help="page:transfers"
      create={{ label: 'New transfer', permission: MANAGE, className: 'max-w-3xl',
        render: (done, cancel) => <TransferForm onSaved={(d) => done(d)} onCancel={cancel} /> }}
      renderDetail={(t) => <TransferSheet row={t} />}
    />
  )
}
