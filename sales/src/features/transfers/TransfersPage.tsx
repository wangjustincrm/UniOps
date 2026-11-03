import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import {
  ActionButton, Autocomplete, ConfirmAction, DateCell, DateTimeCell, DetailList, ErrorMessage, ListPage, LocalTable,
  MasterDetailForm, RecordSheet, Section, StatusCell, numberColumn, queryKeys,
  type ColumnDef, type EditableColumn, type FieldSpec, type RecordTab,
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

const headerSchema = z.object({
  from_warehouse_id: z.string().min(1, 'Choose the warehouse it leaves'),
  to_warehouse_id: z.string().min(1, 'Choose the warehouse it goes to'),
  notes: z.string().trim().max(2000).nullable().optional(),
}).refine((v) => v.from_warehouse_id !== v.to_warehouse_id, { message: 'The two warehouses must differ', path: ['to_warehouse_id'] })
const headerFields: FieldSpec[] = [
  { name: 'from_warehouse_id', label: 'From warehouse', type: 'reference', required: true, loadOptions: warehouseOptions },
  { name: 'to_warehouse_id', label: 'To warehouse', type: 'reference', required: true, loadOptions: warehouseOptions },
  { name: 'notes', label: 'Notes', type: 'textarea' },
]

/** A Draft transfer, new or edited (FR-OB-80): the warehouses and lines in EA. */
function TransferForm({ t, onSaved, onCancel }: { t?: TransferDetail; onSaved: (d: TransferDetail) => void; onCancel: () => void }) {
  return (
    <MasterDetailForm<Line>
      schema={headerSchema}
      fields={headerFields}
      defaultValues={{ from_warehouse_id: t ? String(t.from_warehouse_id) : '', to_warehouse_id: t ? String(t.to_warehouse_id) : '',
        notes: t?.notes ?? '' }}
      lines={{ label: 'Transfer lines', columns: editColumns, problem: lineProblem, add: () => ({ item_code: '', qty_ea: '' }),
        removable: true, initial: t ? t.lines.map((l) => ({ item_code: l.item_code, qty_ea: String(l.qty_ea) })) : [{ item_code: '', qty_ea: '' }] }}
      submitLabel={t ? 'Save changes' : 'Save draft'}
      errorMessages={errorMessages}
      onCancel={onCancel}
      onSubmit={async (v, lines) => {
        const body = { from_warehouse_id: Number(v.from_warehouse_id), to_warehouse_id: Number(v.to_warehouse_id),
          notes: (v.notes as string) ?? null, lines: lines.map((l) => ({ item_code: l.item_code, qty_ea: Number(l.qty_ea) })) }
        onSaved(t ? await updateTransfer(t.id, t.version, body) : await createTransfer(body))
      }}
    />
  )
}

function Detail({ row }: { row: TransferRead }) {
  const id = row.id
  const qc = useQueryClient()
  const q = useQuery({ queryKey: queryKeys.detail(transfersClient, id), queryFn: () => fetchTransfer(id) })
  const [cancelling, setCancelling] = useState(false)
  const [editing, setEditing] = useState(false)
  const done = async (d: TransferDetail) => {
    qc.setQueryData(queryKeys.detail(transfersClient, id), d)
    await qc.invalidateQueries({ queryKey: queryKeys.list(transfersClient) })
  }
  const ready = useMutation({ mutationFn: () => readyTransfer(id, q.data!.version), onSuccess: done })
  const resend = useMutation({ mutationFn: () => resendTransfer(id, q.data!.version), onSuccess: done })
  const t = q.data
  const sent = t ? t.lines.reduce((n, l) => n + l.qty_ea, 0) : 0
  const received = t ? t.lines.reduce((n, l) => n + l.received_ea, 0) : 0
  const discrepant = !!t?.receipts.some((r) => r.discrepancies && r.discrepancies.length > 0)
  const tabs: RecordTab[] = !t ? [] : editing ? [{
    id: 'edit', label: 'Edit draft', render: () => (
      <TransferForm key={t.version} t={t} onCancel={() => setEditing(false)} onSaved={async (d) => { setEditing(false); await done(d) }} />),
  }] : [
    { id: 'overview', label: 'Overview', tone: t.error ? 'danger' : undefined, render: () => (
      <Section title="Transfer">
        <DetailList items={[
          ['From → to', `${t.from_warehouse} → ${t.to_warehouse}`],
          ['943', t.control ? `${t.control} · file ${t.file_status} · 997 ${t.ack_status}` : null], ['Notes', t.notes], ['Problem', t.error],
        ]} />
      </Section>
    ) },
    { id: 'lines', label: 'Lines', count: t.lines.length, render: () => (
      <LocalTable<TransferLineRead> ariaLabel="Transfer lines" columns={lineColumns} rows={t.lines} rowKey={(l) => l.id}
        footer={[null, 'Total', sent, received, null]} />
    ) },
    { id: 'receipts', label: 'Receipts (944)', count: t.receipts.length, tone: discrepant ? 'warning' : undefined,
      empty: t.receipts.length === 0 && 'No 944 yet', render: () => t.receipts.map((r) => (
        <Section key={r.id} title={<>944 {r.document_name} · <DateCell value={r.receipt_date} /> · {r.applied ? r.outcome : r.shadow ? 'shadow run: not applied' : 'not applied (second 944)'}</>}>
          {r.discrepancies && r.discrepancies.length > 0 && <ul className="text-xs text-danger-600">{r.discrepancies.map((x) => <li key={x}>{x}</li>)}</ul>}
          <LocalTable<ReceiptRowRead> ariaLabel={`944 ${r.document_name ?? r.id} lines`} columns={rowColumns} rows={r.rows}
            rowKey={(x) => x.position} sortable={['item_code', 'lot', 'transfer_line_no']}
            footer={[null, null, 'Total', null, r.rows.reduce((n, x) => n + x.qty_ea, 0), null]} />
        </Section>
      )) },
  ]
  return (
    <>
      <RecordSheet
        title={row.transfer_number}
        subtitle={`${row.from_warehouse ?? '?'} → ${row.to_warehouse ?? '?'}`}
        badges={<StatusCell value={t?.status ?? row.status} />}
        loading={q.isLoading} error={q.error} errorMessages={errorMessages}
        onRefresh={() => q.refetch()} updatedAt={q.dataUpdatedAt} fetching={q.isFetching} snapshot={t as unknown as Record<string, unknown> | undefined}
        facts={t ? [
          { label: 'Status', value: t.status, tone: ['Failed', 'Discrepancy'].includes(t.status) ? 'danger' : t.status === 'Partially Received' ? 'warning' : 'neutral' },
          { label: 'Sent EA', value: sent },
          { label: 'Received EA', value: received, tone: t.receipts.length > 0 && received < sent ? 'warning' : 'neutral' },
          { label: '943', value: t.control ?? '—', hint: t.file_status ?? undefined },
          { label: '997', value: t.ack_status ?? '—' },
        ] : undefined}
        actions={t && !editing ? (
          <>
            {t.status === 'Draft' && <ActionButton size="sm" permission={MANAGE} onAction={() => ready.mutateAsync().catch(() => {})}>Ready to send</ActionButton>}
            {t.status === 'Draft' && <ActionButton size="sm" variant="secondary" permission={MANAGE} onAction={() => setEditing(true)}>Edit draft</ActionButton>}
            {t.status === 'Failed' && <ActionButton size="sm" permission={MANAGE} onAction={() => resend.mutateAsync().catch(() => {})}>Resend 943</ActionButton>}
            <ErrorMessage error={ready.error ?? resend.error} messages={errorMessages} />
          </>
        ) : undefined}
        more={t && !editing && CANCELLABLE.includes(t.status)
          ? [{ label: 'Cancel transfer', permission: MANAGE, onSelect: () => setCancelling(true), opensDialog: true }] : undefined}
        tabs={tabs}
      />
      {t && (
        <ConfirmAction open={cancelling} onOpenChange={setCancelling} title={`Cancel transfer ${t.transfer_number}?`} destructive
          confirmLabel="Cancel transfer" reason={{ label: 'Why' }} errorMessages={errorMessages}
          description="Allowed before its 943 is sent, or after it was rejected. A queued 943 is withdrawn."
          onConfirm={async (why) => done(await cancelTransfer(id, t.version, why))} />
      )}
    </>
  )
}

/** Transfers (FR-OB-80 … 84): Draft → Ready to send → the 943 → the 997 → the
 *  944, its lines allocated and summed per lot. */
export default function TransfersPage() {
  return (
    <ListPage<TransferRead>
      resource={transfersClient}
      title="Transfers"
      description="Stock moved between warehouses: a 943 goes to the warehouse, its 944 says what arrived."
      columns={columns}
      emptyTitle="No transfers yet"
      create={{ label: 'New transfer', permission: MANAGE, className: 'max-w-3xl',
        render: (done, cancel) => <TransferForm onSaved={(d) => done(d)} onCancel={cancel} /> }}
      renderDetail={(t) => <Detail row={t} />}
    />
  )
}
