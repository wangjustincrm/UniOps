import { factTone } from '@/features/common/statusTones'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import {
  ActionButton, Autocomplete, ConfirmAction, DateCell, DetailList, ListPage, LocalTable, ViewFileButton,
  MasterDetailForm, RecordSheet, SchemaForm, Section, StatusCell, numberColumn, queryKeys, useFileViewer,
  type ColumnDef, type EditableColumn, type FieldSpec, type RecordTab,
  compareRecord, useRefresh, Muted, columnsOf
 } from '@/platform-ui'
import { itemOptions, warehouseOptions } from '../configuration/lookups'
import { Attachments } from '@/features/common/Attachments'
import { RecordActivity } from '@/features/common/RecordActivity'
import { RecordFilesTable } from '@/features/common/RecordFilesTable'
import {
  amendTransfer, cancelTransfer, createTransfer, fetchTransfer, readyTransfer, releaseTransfer, resolveTransfer, resendTransfer, transfersClient, updateTransfer,
  transferPdf, type ReceiptRowRead, type TransferDetail, type TransferLineRead, type TransferRead,
} from './transfersApi'

const colReceiptRowRead = columnsOf<ReceiptRowRead>()
const colTransferLineRead = columnsOf<TransferLineRead>()
const colTransferRead = columnsOf<TransferRead>()

const MANAGE = 'sales.transfer.manage'
const CANCELLABLE = ['Draft', 'Ready To Send', '943 Generated', 'Failed']
const AMENDABLE = ['Waiting for 997', 'Waiting for 944', 'Failed']
const errorMessages: Record<string, string> = {
  'transfer.not_cancellable': 'The 943 is sent: the transfer can no longer be cancelled.',
  'transfer.same_warehouse': 'The two warehouses must differ.',
  'entity.stale': 'Someone else changed this transfer. Close it and open it again.',
  'auth.forbidden': 'You do not have permission to manage transfers.',
  'transfer.not_on_hold': 'This transfer is not On Hold any more.',
  'transfer.not_amendable': 'A 944 has answered this transfer: settle the difference instead.',
  'transfer.not_discrepant': 'This transfer has no receipt difference to settle any more.',
  'transfer.item': 'An item is not active in the Item Master.',
}

const columns: ColumnDef<TransferRead, unknown>[] = [
  colTransferRead.text('transfer_number', 'Transfer'),
  colTransferRead.text('from_warehouse', 'From'),
  colTransferRead.text('to_warehouse', 'To'),
  colTransferRead.status('status', 'Status'),
  { accessorKey: 'items', header: 'Items', cell: ({ row }) => <span className="line-clamp-1">{row.original.items ?? ''}</span> },
  colTransferRead.number('sent_ea', 'Sent EA'),
  colTransferRead.number('received_ea', 'Received EA'),
  colTransferRead.date('receipt_date', 'Received on'),
  colTransferRead.number('attachments', 'Attachments'),
  colTransferRead.datetime('created_at', 'Created'),
  { accessorKey: 'error', header: 'Note', cell: ({ row }) => <Muted className="line-clamp-1">{row.original.error ?? ''}</Muted> },
]
const lineColumns: ColumnDef<TransferLineRead, unknown>[] = [
  colTransferLineRead.text('line_no', '#'),
  colTransferLineRead.text('item_code', 'Item'),
  colTransferLineRead.text('qty_ea', 'Requested EA'),
  colTransferLineRead.text('received_ea', 'Received EA'),
  colTransferLineRead.status('line_status', 'Line'),
]
const rowColumns: ColumnDef<ReceiptRowRead, unknown>[] = [
  colReceiptRowRead.text('position', 'W07'),
  colReceiptRowRead.text('lx_number', 'LX'),
  { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => row.original.item_code ?? row.original.upc ?? '—' },
  colReceiptRowRead.text('lot', 'Lot'),
  colReceiptRowRead.text('qty_ea', 'EA'),
  { accessorKey: 'transfer_line_no', header: 'To line', cell: ({ row }) => row.original.transfer_line_no ?? <span className="text-danger-600">not on the transfer</span> },
]

type PackedRow = ReceiptRowRead & { file: string; receipt_date: string | null }
const colPacked = columnsOf<PackedRow>()
const packedColumns: ColumnDef<PackedRow, unknown>[] = [
  { accessorKey: 'lx_number', header: 'Pallet (LX)', cell: ({ row }) => row.original.lx_number ?? '—' },
  { accessorKey: 'item_code', header: 'Item', cell: ({ row }) => row.original.item_code ?? row.original.upc ?? '—' },
  colPacked.text('lot', 'Lot'),
  { accessorKey: 'description', header: 'Description', cell: ({ row }) => <span className="text-xs">{row.original.description ?? ''}</span> },
  colPacked.number('qty_ea', 'EA'),
  colPacked.date('receipt_date', 'Received on'),
  colPacked.text('file', '944'),
]

/** What a 944 received, row by row (an order's packed rows, for a transfer). */
function Received({ rows, label = 'Received for this line' }: { rows: PackedRow[]; label?: string }) {
  return (
    <LocalTable<PackedRow> ariaLabel={label} columns={packedColumns} rows={rows} rowKey={(x, i) => `${x.file}-${x.position}-${i}`}
      sortable={['lot', 'lx_number']} footer={[null, null, 'Total', null, rows.reduce((n, x) => n + x.qty_ea, 0), null, null]} />
  )
}

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
      version={t?.version}
      onSubmit={async (v, lines, edit) => {
        const body = { from_warehouse_id: Number(v.from_warehouse_id), to_warehouse_id: Number(v.to_warehouse_id),
          notes: (v.notes as string) ?? null, lines: lines.map((l) => ({ item_code: l.item_code, qty_ea: Number(l.qty_ea) })) }
        onSaved(t ? await updateTransfer(t.id, edit.version ?? t.version, body) : await createTransfer(body))
      }}
    />
  )
}

const amendSchema = z.object({
  notes: z.string().trim().max(2000).nullable().optional(),
  reason: z.string().trim().min(3, 'Say what was wrong (at least 3 characters)').max(2000),
})
const amendFields: FieldSpec[] = [
  { name: 'reason', label: 'What was wrong', type: 'textarea', required: true,
    hint: 'Kept with the transfer. The 943 that went is Replaced; a new one (a new control number) goes on the next pass.' },
  { name: 'notes', label: 'Notes', type: 'textarea' },
]

/** A sent transfer corrected and sent again (user 2026-11-13), until a 944 answers it: same warehouses, new lines, why. */
function AmendForm({ t, onSaved, onCancel }: { t: TransferDetail; onSaved: (d: TransferDetail) => void; onCancel: () => void }) {
  return (
    <MasterDetailForm<Line>
      schema={amendSchema}
      fields={amendFields}
      defaultValues={{ notes: t.notes ?? '', reason: '' }}
      lines={{ label: 'Transfer lines', columns: editColumns, problem: lineProblem, add: () => ({ item_code: '', qty_ea: '' }),
        removable: true, initial: t.lines.map((l) => ({ item_code: l.item_code, qty_ea: String(l.qty_ea) })) }}
      submitLabel="Amend and send again"
      errorMessages={errorMessages}
      onCancel={onCancel}
      version={t.version}
      onSubmit={async (v, lines, edit) => {
        onSaved(await amendTransfer(t.id, edit.version ?? t.version, { notes: (v.notes as string) || null, reason: String(v.reason),
          lines: lines.map((l) => ({ item_code: l.item_code, qty_ea: Number(l.qty_ea) })) }))
      }}
    />
  )
}

const resolveSchema = z.object({
  how: z.enum(['corrected', 'accepted'], { message: 'Choose how it is settled' }),
  reason: z.string().trim().min(3, 'Say why (at least 3 characters)').max(2000),
})
const resolveFields: FieldSpec[] = [
  { name: 'how', label: 'How', type: 'segmented', required: true, options: [
    { value: 'corrected', label: 'Our 943 was wrong: correct it to what arrived' },
    { value: 'accepted', label: 'The difference is real: accept it' }],
    hint: 'Corrected: the transfer\'s lines become the 944\'s items and quantities, nothing stays in transit. Accepted: the lines stay as sent; what did not arrive is written off in transit (or what came extra is added).' },
  { name: 'reason', label: 'Why', type: 'textarea', required: true },
]

/** A receipt difference settled by a person (user 2026-11-13): the transfer is Received, its tasks closed. */
function ResolveForm({ t, onSaved, onCancel }: { t: TransferDetail; onSaved: (d: TransferDetail) => void; onCancel: () => void }) {
  return (
    <SchemaForm schema={resolveSchema} fields={resolveFields} submitLabel="Settle the difference" onCancel={onCancel} errorMessages={errorMessages}
      defaultValues={{ how: '', reason: '' }}
      onSubmit={async (v) => {
        const out = await resolveTransfer(t.id, t.version, v.how as 'corrected' | 'accepted', String(v.reason))
        onSaved(out)
        return out
      }} />
  )
}

function Detail({ row }: { row: TransferRead }) {
  const id = row.id
  const qc = useQueryClient()
  const { view, viewer } = useFileViewer()
  const q = useQuery({ queryKey: queryKeys.detail(transfersClient, id), queryFn: () => fetchTransfer(id) })
  const sheetRefresh = useRefresh(q, { queryKey: queryKeys.detail(transfersClient, id), compare: compareRecord, noChange: 'Up to date', auto: 'follow' })
  const [cancelling, setCancelling] = useState(false)
  const [releasing, setReleasing] = useState(false)
  const [editing, setEditing] = useState(false)
  const [amending, setAmending] = useState(false)
  const [settling, setSettling] = useState(false)
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
  // What the applied 944s received, row by row, with the file it came in (user 2026-11-13: under each line, as an order's).
  const receivedRows: PackedRow[] = (t?.receipts ?? []).filter((r) => r.applied).flatMap((r) => r.rows.map((x) => ({ ...x, file: r.document_name ?? `#${r.document_id}`, receipt_date: r.receipt_date })))
  const packedFor = (lineNo: number) => receivedRows.filter((x) => x.transfer_line_no === lineNo)
  const offTransfer = receivedRows.filter((x) => x.transfer_line_no == null)
  const tabs: RecordTab[] = !t ? [] : editing ? [{
    id: 'edit', label: 'Edit draft', render: () => (
      <TransferForm t={t} onCancel={() => setEditing(false)} onSaved={async (d) => { setEditing(false); await done(d) }} />),
  }] : amending ? [{
    id: 'amend', label: 'Amend and send again', render: () => (
      <AmendForm t={t} onCancel={() => setAmending(false)} onSaved={async (d) => { setAmending(false); await done(d) }} />),
  }] : settling ? [{
    id: 'settle', label: 'Settle the difference', render: () => (
      <Section title="Settle the receipt difference">
        {t.receipts.flatMap((r) => r.discrepancies ?? []).length > 0 && (
          <ul className="mb-3 text-xs text-danger-600">{t.receipts.flatMap((r) => r.discrepancies ?? []).map((x) => <li key={x}>{x}</li>)}</ul>)}
        <ResolveForm t={t} onCancel={() => setSettling(false)} onSaved={async (d) => { setSettling(false); await done(d) }} />
      </Section>),
  }] : [
    { id: 'overview', label: 'Overview', tone: t.error ? 'danger' : undefined, render: () => (
      <Section title="Transfer">
        <DetailList items={[
          ['From → to', `${t.from_warehouse} → ${t.to_warehouse}`],
          ['943', t.control ? `${t.control} · file ${t.file_status} · 997 ${t.ack_status}` : null], ['Notes', t.notes], ['Problem', t.error],
          ['Difference settled', t.resolution ? `${t.resolution === 'corrected' ? 'Corrected to what arrived' : 'Accepted as it is'}: ${t.resolution_reason ?? ''} (${t.resolved_by_name ?? ''})` : null],
        ]} />
      </Section>
    ) },
    { id: 'lines', label: 'Lines', count: t.lines.length, tone: offTransfer.length ? 'warning' : undefined, render: () => (
      <>
        <p className="mb-2 text-xs text-neutral-500">▸ shows what the warehouse's 944 received for a line: pallet, lot and quantity.</p>
        <LocalTable<TransferLineRead> ariaLabel="Transfer lines" columns={lineColumns} rows={t.lines} rowKey={(l) => l.id}
          expand={(l) => { const rows = packedFor(l.line_no); return rows.length ? <Received rows={rows} /> : null }}
          footer={[null, 'Total', sent, received, null]} />
        {offTransfer.length > 0 && (
          <Section title="Received but not on the transfer" plain>
            <Received rows={offTransfer} label="Received, not on the transfer" />
          </Section>
        )}
      </>
    ) },
    { id: 'receipts', label: 'Receipts (944)', count: t.receipts.length, tone: discrepant ? 'warning' : undefined,
      empty: t.receipts.length === 0 && 'No 944 yet', render: () => t.receipts.map((r) => (
        <Section key={r.id} title={<>944 {r.document_name} · <DateCell value={r.receipt_date} /> · {r.applied || r.shadow ? r.outcome : 'not applied (second 944)'}</>}>
          {r.discrepancies && r.discrepancies.length > 0 && <ul className="text-xs text-danger-600">{r.discrepancies.map((x) => <li key={x}>{x}</li>)}</ul>}
          <LocalTable<ReceiptRowRead> ariaLabel={`944 ${r.document_name ?? r.id} lines`} columns={rowColumns} rows={r.rows}
            rowKey={(x) => x.position} sortable={['item_code', 'lot', 'transfer_line_no']}
            footer={[null, null, 'Total', null, r.rows.reduce((n, x) => n + x.qty_ea, 0), null]} />
        </Section>
      )) },
    { id: 'documents', label: 'Documents', count: t.documents.length,
      tone: t.documents.some((x) => x.direction === 'OUT' && ['Failed', 'Suppressed'].includes(x.file_status ?? '')) ? 'danger' : undefined,
      render: () => (
        <>
          <Section title="Printable">
            <div className="flex flex-wrap gap-2">
              <ViewFileButton source={transferPdf(t.id, t.transfer_number)}>Transfer order (PDF)</ViewFileButton>
            </div>
          </Section>
          <Attachments type="transfer" id={t.id} />
          <Section title="Files received and sent">
            <RecordFilesTable documents={t.documents} empty="No file yet: the 943 goes when it is ready to send." />
          </Section>
        </>
      ) },
    { id: 'activity', label: 'Activity', count: t.timeline.length, empty: t.timeline.length === 0 && 'Nothing recorded yet',
      render: () => <RecordActivity entries={t.timeline} /> },
  ]
  return (
    <>
      {viewer}
      <RecordSheet
        title={row.transfer_number}
        subtitle={`${row.from_warehouse ?? '?'} → ${row.to_warehouse ?? '?'}`}
        badges={<StatusCell value={t?.status ?? row.status} />}
        loading={q.isLoading} error={q.error} errorMessages={errorMessages}
        refresh={sheetRefresh}
        facts={t ? [
          { label: 'Status', value: t.status, tone: factTone(t.status) },
          { label: 'Sent EA', value: sent },
          { label: 'Received EA', value: received, tone: t.receipts.length > 0 && received < sent ? 'warning' : 'neutral' },
          { label: '943', value: t.control ?? '—', hint: t.file_status ?? undefined },
          { label: '997', value: t.ack_status ?? '—' },
          ...(t.receipt_date ? [{ label: 'Received on', value: <DateCell value={t.receipt_date} /> }] : []),
        ] : undefined}
        actions={t && !editing ? (
          <>
            {t.status === 'Draft' && <ActionButton size="sm" permission={MANAGE} errorMessages={errorMessages} done="Ready to send: the 943 goes on the next pass" onAction={() => ready.mutateAsync()}>Ready to send</ActionButton>}
            {t.status === 'Draft' && <ActionButton size="sm" variant="secondary" permission={MANAGE} onAction={() => setEditing(true)}>Edit draft</ActionButton>}
            {t.status === 'Failed' && <ActionButton size="sm" permission={MANAGE} errorMessages={errorMessages} done="943 queued again" onAction={() => resend.mutateAsync()}>Resend 943</ActionButton>}
            {t.status === 'On Hold' && <ActionButton size="sm" permission={MANAGE} onAction={() => setReleasing(true)}>Release hold…</ActionButton>}
            {AMENDABLE.includes(t.status) && t.receipts.every((r) => !r.applied) && !amending && !settling && (
              <ActionButton size="sm" variant="secondary" permission={MANAGE} onAction={() => setAmending(true)}>Amend and send again…</ActionButton>)}
            {['Discrepancy', 'Partially Received'].includes(t.status) && !settling && (
              <ActionButton size="sm" permission={MANAGE} onAction={() => setSettling(true)}>Settle the difference…</ActionButton>)}
          </>
        ) : undefined}
        more={t && !editing ? [
          { label: 'Transfer order (PDF)', onSelect: () => view(transferPdf(t.id, t.transfer_number)), opensDialog: true },
          ...(CANCELLABLE.includes(t.status)
            ? [{ label: 'Cancel transfer', permission: MANAGE, onSelect: () => setCancelling(true), opensDialog: true }] : []),
        ] : undefined}
        tabs={tabs}
      />
      {t && (
        <ConfirmAction open={cancelling} onOpenChange={setCancelling} title={`Cancel transfer ${t.transfer_number}?`} destructive
          confirmLabel="Cancel transfer" reason={{ label: 'Why' }} errorMessages={errorMessages}
          description="Allowed before its 943 is sent, or after it was rejected. A queued 943 is withdrawn."
          onConfirm={async (why) => done(await cancelTransfer(id, t.version, why))} />
      )}
      {t && (
        <ConfirmAction open={releasing} onOpenChange={setReleasing} title={`Release transfer ${t.transfer_number}?`}
          confirmLabel={`Back to ${t.status_before_hold ?? 'its status'}`} reason={{ label: 'What you decided (compared both 944s)' }} errorMessages={errorMessages}
          description="A second, different 944 put it On Hold. The first 944 stays applied; the second stays kept, not applied."
          onConfirm={async (why) => done(await releaseTransfer(id, t.version, why))} />
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
