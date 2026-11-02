import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, Autocomplete, DateCell, DateTimeCell, DetailList, EditableTable, ErrorMessage, ErrorView, FormField,
  Input, ListPage, LocalTable, Skeleton, StatusCell, numberColumn, queryKeys, useCan,
  type ColumnDef, type EditableColumn,
} from '@/platform-ui'
import { itemOptions, warehouseOptions } from '../configuration/lookups'
import {
  cancelTransfer, createTransfer, fetchTransfer, readyTransfer, resendTransfer, transfersClient, updateTransfer,
  type ReceiptRowRead, type TransferDetail, type TransferLineRead, type TransferRead,
} from './transfersApi'

const MANAGE = 'sales.transfer.manage'
const CANCELLABLE = ['Draft', 'Ready To Send', '943 Generated', 'Failed']
const errorMessages: Record<string, string> = {
  'transfer.shadow': 'The shadow run is on: Sales sends no 943 (Zoho is live).',
  'transfer.not_cancellable': 'The 943 is sent: the transfer can no longer be cancelled.',
  'transfer.same_warehouse': 'The two warehouses must differ.',
  'entity.stale': 'Someone else changed this transfer. Close it and open it again.',
  'auth.forbidden': 'You do not have permission to manage transfers.',
}

const columns: ColumnDef<TransferRead, unknown>[] = [
  { accessorKey: 'transfer_number', header: 'Transfer' },
  { accessorKey: 'from_warehouse', header: 'From' },
  { accessorKey: 'to_warehouse', header: 'To' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusCell value={row.original.status} /> },
  { accessorKey: 'created_at', header: 'Created', cell: ({ row }) => <DateTimeCell value={row.original.created_at} /> },
  { accessorKey: 'error', header: 'Note', cell: ({ row }) => <span className="line-clamp-1 text-neutral-500">{row.original.error ?? ''}</span> },
]
const lineColumns: ColumnDef<TransferLineRead, unknown>[] = [
  { accessorKey: 'line_no', header: '#' },
  { accessorKey: 'item_code', header: 'Item' },
  { accessorKey: 'qty_ea', header: 'Requested EA' },
  { accessorKey: 'received_ea', header: 'Received EA' },
  { accessorKey: 'line_status', header: 'Line', cell: ({ row }) => <StatusCell value={row.original.line_status} /> },
]
const rowColumns: ColumnDef<ReceiptRowRead, unknown>[] = [
  { accessorKey: 'position', header: 'W07' },
  { accessorKey: 'lx_number', header: 'LX' },
  { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => row.original.item_code ?? row.original.upc ?? '—' },
  { accessorKey: 'lot', header: 'Lot' },
  { accessorKey: 'qty_ea', header: 'EA' },
  { accessorKey: 'transfer_line_no', header: 'To line', cell: ({ row }) => row.original.transfer_line_no ?? <span className="text-danger-600">not on the transfer</span> },
]

type Line = { item_code: string; qty_ea: string }
const editColumns: EditableColumn<Line>[] = [
  { id: 'item', header: 'Item', cell: ({ row, index, update, invalid }) => (
    <Autocomplete aria-label={`Item ${index + 1}`} aria-invalid={invalid || undefined} value={row.item_code || null}
      onChange={(v) => update({ item_code: v ?? '' })} loadOptions={itemOptions} />
  ) },
  numberColumn<Line>('qty', 'EA', { label: (_, i) => `EA ${i + 1}`, get: (l) => l.qty_ea, set: (v) => ({ qty_ea: v }), min: 1 }),
]
const lineProblem = (l: Line) => (!l.item_code ? 'choose an item' : Number.isInteger(Number(l.qty_ea)) && Number(l.qty_ea) > 0 ? null : 'at least 1 EA')

/** A Draft transfer (new or edited): warehouses and lines in EA (FR-OB-80). */
function Editor({ t, onSaved }: { t?: TransferDetail; onSaved: (d: TransferDetail) => void }) {
  const qc = useQueryClient()
  const [from, setFrom] = useState<string | null>(t ? String(t.from_warehouse_id) : null)
  const [to, setTo] = useState<string | null>(t ? String(t.to_warehouse_id) : null)
  const [notes, setNotes] = useState(t?.notes ?? '')
  const [lines, setLines] = useState<Line[]>(t ? t.lines.map((l) => ({ item_code: l.item_code, qty_ea: String(l.qty_ea) })) : [{ item_code: '', qty_ea: '' }])
  const save = useMutation({
    mutationFn: () => {
      const body = { from_warehouse_id: Number(from), to_warehouse_id: Number(to), notes: notes || null,
        lines: lines.map((l) => ({ item_code: l.item_code, qty_ea: Number(l.qty_ea) })) }
      return t ? updateTransfer(t.id, t.version, body) : createTransfer(body)
    },
    onSuccess: async (d) => { await qc.invalidateQueries({ queryKey: queryKeys.list(transfersClient) }); onSaved(d) },
  })
  const valid = from && to && from !== to && lines.length > 0 && lines.every((l) => lineProblem(l) === null)
  return (
    <section aria-label={t ? 'Edit transfer' : 'New transfer'} className="flex flex-col gap-3 rounded-lg border border-neutral-200 bg-white p-4">
      {!t && <h2 className="font-semibold text-neutral-800">New transfer</h2>}
      <div className="grid gap-3 sm:grid-cols-2">
        <FormField label="From warehouse" htmlFor="tr-from" required>
          <Autocomplete id="tr-from" aria-label="From warehouse" value={from} onChange={setFrom} loadOptions={warehouseOptions} minChars={0} />
        </FormField>
        <FormField label="To warehouse" htmlFor="tr-to" required>
          <Autocomplete id="tr-to" aria-label="To warehouse" value={to} onChange={setTo} loadOptions={warehouseOptions} minChars={0} />
        </FormField>
      </div>
      <EditableTable<Line> ariaLabel="Transfer lines" rows={lines} onChange={setLines} columns={editColumns} errors={lineProblem}
        onAdd={() => ({ item_code: '', qty_ea: '' })} removable />
      <FormField label="Notes" htmlFor="tr-notes"><Input id="tr-notes" value={notes} onChange={(e) => setNotes(e.target.value)} /></FormField>
      <ActionButton size="sm" permission={MANAGE} disabled={!valid} onAction={() => save.mutateAsync().catch(() => {})}>
        {t ? 'Save changes' : 'Save draft'}
      </ActionButton>
      <ErrorMessage error={save.error} messages={errorMessages} />
    </section>
  )
}

function Detail({ id }: { id: number }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: queryKeys.detail(transfersClient, id), queryFn: () => fetchTransfer(id) })
  const [cancelling, setCancelling] = useState(false)
  const [reason, setReason] = useState('')
  const done = async (d: TransferDetail) => {
    qc.setQueryData(queryKeys.detail(transfersClient, id), d)
    await qc.invalidateQueries({ queryKey: queryKeys.list(transfersClient) })
  }
  const ready = useMutation({ mutationFn: () => readyTransfer(id, q.data!.version), onSuccess: done })
  const resend = useMutation({ mutationFn: () => resendTransfer(id, q.data!.version), onSuccess: done })
  const cancel = useMutation({ mutationFn: () => cancelTransfer(id, q.data!.version, reason.trim()), onSuccess: done })
  if (q.isLoading) return <Skeleton className="h-40 w-full" />
  if (q.error || !q.data) return <ErrorView error={q.error} />
  const t = q.data
  return (
    <div className="flex flex-col gap-4 text-sm">
      <DetailList items={[
        ['Status', <StatusCell key="s" value={t.status} />], ['From → to', `${t.from_warehouse} → ${t.to_warehouse}`],
        ['943', t.control ? `${t.control} · file ${t.file_status} · 997 ${t.ack_status}` : null], ['Notes', t.notes], ['Problem', t.error],
      ]} />
      {t.status !== 'Draft' && (  // a Draft shows its lines in the editor below
        <LocalTable<TransferLineRead> ariaLabel="Transfer lines" columns={lineColumns} rows={t.lines} rowKey={(l) => l.id}
          footer={[null, 'Total', t.lines.reduce((n, l) => n + l.qty_ea, 0), t.lines.reduce((n, l) => n + l.received_ea, 0), null]} />
      )}
      {t.status === 'Draft' && <Editor key={t.version} t={t} onSaved={done} />}
      <div className="flex flex-wrap gap-2">
        {t.status === 'Draft' && <ActionButton size="sm" permission={MANAGE} onAction={() => ready.mutateAsync().catch(() => {})}>Ready to send</ActionButton>}
        {t.status === 'Failed' && <ActionButton size="sm" permission={MANAGE} onAction={() => resend.mutateAsync().catch(() => {})}>Resend 943</ActionButton>}
        {CANCELLABLE.includes(t.status) && (
          <ActionButton size="sm" variant="secondary" permission={MANAGE} onAction={() => setCancelling(true)}>Cancel transfer</ActionButton>
        )}
      </div>
      <ErrorMessage error={ready.error ?? resend.error} messages={errorMessages} />
      {cancelling && (
        <section aria-label="Cancel transfer" className="flex flex-col gap-2 rounded-md border border-neutral-100 p-2">
          <FormField label="Why" htmlFor="tr-cancel"><Input id="tr-cancel" value={reason} onChange={(e) => setReason(e.target.value)} /></FormField>
          <ActionButton size="sm" variant="secondary" permission={MANAGE} disabled={reason.trim().length < 3}
            onAction={() => cancel.mutateAsync().then(() => setCancelling(false)).catch(() => {})}>Cancel it</ActionButton>
          <ErrorMessage error={cancel.error} messages={errorMessages} />
        </section>
      )}
      {t.receipts.map((r) => (
        <section key={r.id} aria-label={`Receipt ${r.document_name ?? r.id}`} className="flex flex-col gap-2">
          <h3 className="font-semibold text-neutral-800">
            944 {r.document_name} · <DateCell value={r.receipt_date} /> · {r.applied ? r.outcome : r.shadow ? 'shadow run: not applied' : 'not applied (second 944)'}
          </h3>
          {r.discrepancies && r.discrepancies.length > 0 && <ul className="text-xs text-danger-600">{r.discrepancies.map((x) => <li key={x}>{x}</li>)}</ul>}
          <LocalTable<ReceiptRowRead> ariaLabel={`944 ${r.document_name ?? r.id} lines`} columns={rowColumns} rows={r.rows}
            rowKey={(x) => x.position} sortable={['item_code', 'lot', 'transfer_line_no']}
            footer={[null, null, 'Total', null, r.rows.reduce((n, x) => n + x.qty_ea, 0), null]} />
        </section>
      ))}
    </div>
  )
}

/** Transfers (FR-OB-80 … 84): Draft → Ready to send → the 943 → the 997 → the
 *  944, its lines allocated and summed per lot. */
export default function TransfersPage() {
  const can = useCan(MANAGE)
  const [creating, setCreating] = useState(false)
  return (
    <div className="flex flex-col gap-4">
      {creating && <Editor onSaved={() => setCreating(false)} />}
      <ListPage<TransferRead>
        resource={transfersClient}
        title="Transfers"
        description="Stock moved between warehouses: a 943 goes to the warehouse, its 944 says what arrived."
        columns={columns}
        emptyTitle="No transfers yet"
        toolbar={() => can && !creating ? <ActionButton size="sm" permission={MANAGE} onAction={() => setCreating(true)}>New transfer</ActionButton> : null}
        detailTitle={(t) => `Transfer ${t.transfer_number}`}
        detailClassName="max-w-4xl"
        renderDetail={(t) => <Detail id={t.id} />}
      />
    </div>
  )
}
