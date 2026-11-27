import { ListPage, type ColumnDef, type ListScope, type HelpId, Muted, columnsOf, Text } from '@/platform-ui'
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

/** The transfers as a list: the Transfers page (with New transfer), or a record's own (an item's: its `scope`). */
export function TransferList({ scope, title, help }: { scope?: ListScope; title?: string; help?: HelpId }) {
  return (
    <ListPage<TransferRead>
      resource={transfersClient}
      title={title}
      description={title ? 'Stock sent from the plant to the warehouse (Radiant): a 943 says what is coming, its 944 says what arrived.' : undefined}
      columns={columns}
      scope={scope}
      fit={scope ? 560 : undefined}
      emptyTitle={scope ? 'No transfer has this item' : 'No transfers yet'}
      emptyText={scope ? undefined : EMPTY_TEXT.transfers}
      help={help}
      create={scope ? undefined : { label: 'New transfer', permission: MANAGE, className: 'max-w-3xl',
        render: (done, cancel) => <TransferForm onSaved={(d) => done(d)} onCancel={cancel} /> }}
      renderDetail={(t) => <TransferSheet row={t} />}
    />
  )
}
