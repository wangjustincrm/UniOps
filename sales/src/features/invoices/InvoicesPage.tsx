import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import {
  ActionButton, ConfirmAction, DateCell, DetailList, ErrorMessage, ErrorView, ListPage, LocalTable, MasterDetailForm, MoneyCell,
  Skeleton, StatusCell, formatDate, formatDateTime, formatMoney, numberColumn, queryKeys, readColumn, textColumn,
  type ColumnDef, type EditableColumn, type FieldSpec,
} from '@/platform-ui'
import { editInvoice, fetchInvoice, invoicesClient, releaseInvoice, sendInvoice, type InvoiceDetail, type InvoiceRead } from './invoicesApi'

const MANAGE = 'sales.invoice.manage'
const errorMessages: Record<string, string> = {
  'invoice.shadow': 'The shadow run is on: nothing is invoiced (Zoho is live).',
  'invoice.shadow_order': 'This order was received during the shadow run: Zoho invoices it.',
  'invoice.not_sendable': 'The invoice cannot be sent in its current status.',
  'invoice.not_editable': 'The invoice cannot be edited in its current status.',
  'entity.stale': 'Someone else changed this invoice. Close it and open it again.',
  'auth.forbidden': 'You do not have permission to manage invoices.',
}

const columns: ColumnDef<InvoiceRead, unknown>[] = [
  { accessorKey: 'invoice_number', header: 'Invoice' },
  { accessorKey: 'partner_code', header: 'Partner' },
  { accessorKey: 'po', header: 'PO' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusCell value={row.original.status} /> },
  { accessorKey: 'method', header: 'Method' },
  { accessorKey: 'invoice_date', header: 'Date', cell: ({ row }) => <DateCell value={row.original.invoice_date} /> },
  { accessorKey: 'total', header: 'Total', cell: ({ row }) => <MoneyCell amount={row.original.total} currency={row.original.currency} /> },
  { accessorKey: 'send_count', header: 'Sent', cell: ({ row }) => row.original.sent_at ? `${row.original.send_count}× · ${formatDateTime(row.original.sent_at)}` : '—' },
]

type EditLine = { id: number; line_no: number; item: string; amount: string; qty: string; unit_price: string }

const editColumns = (currency: string): EditableColumn<EditLine>[] => [
  readColumn<EditLine>('line_no', '#', (l) => l.line_no),
  readColumn<EditLine>('item', 'Item', (l) => l.item),
  numberColumn<EditLine>('qty', 'Qty (CA)', { label: (l) => `Quantity line ${l.line_no}`, get: (l) => l.qty, set: (v) => ({ qty: v }), min: 0, className: 'w-20' }),
  textColumn<EditLine>('unit_price', 'Unit price', { label: (l) => `Unit price line ${l.line_no}`, get: (l) => l.unit_price, set: (v) => ({ unit_price: v }) }),
  readColumn<EditLine>('amount', 'Amount', (l) => formatMoney(l.amount, currency)),
]

function lineProblem(l: EditLine): string | null {
  if (!Number.isInteger(Number(l.qty)) || Number(l.qty) < 0) return 'a whole number of cases, 0 or more'
  if (l.unit_price.trim() === '' || Number.isNaN(Number(l.unit_price)) || Number(l.unit_price) < 0) return 'a price of 0 or more'
  return null
}

const editSchema = z.object({ invoice_date: z.string().min(1, 'Required') })
const editFields: FieldSpec[] = [{ name: 'invoice_date', label: 'Invoice date', type: 'date', required: true }]

const lineColumns = (currency: string): ColumnDef<InvoiceDetail['lines'][number], unknown>[] => [
  { accessorKey: 'line_no', header: '#' },
  { id: 'item', header: 'Item', cell: ({ row: { original: l } }) => `${l.item_code ?? ''}${l.buyer_part ? ` (${l.buyer_part})` : ''}` },
  { accessorKey: 'qty', header: 'Qty (CA)' },
  { accessorKey: 'unit_price', header: 'Unit price', cell: ({ row }) => <MoneyCell amount={row.original.unit_price} currency={currency} /> },
  { accessorKey: 'amount', header: 'Amount', cell: ({ row }) => <MoneyCell amount={row.original.amount} currency={currency} /> },
]

/** FR-OB-35: quantities, unit prices and the date — the standard form, in the sheet. */
function InvoiceForm({ inv, onSaved, onCancel }: { inv: InvoiceDetail; onSaved: (d: InvoiceDetail) => void; onCancel: () => void }) {
  return (
    <MasterDetailForm<EditLine>
      schema={editSchema} fields={editFields} defaultValues={{ invoice_date: inv.invoice_date }}
      lines={{ label: 'Invoice lines', columns: editColumns(inv.currency), rowKey: (l) => l.id, problem: lineProblem, minRows: 0,
        initial: inv.lines.map((l) => ({ id: l.id, line_no: l.line_no, item: `${l.item_code ?? ''}${l.buyer_part ? ` (${l.buyer_part})` : ''}`,
          amount: l.amount, qty: String(l.qty), unit_price: String(Number(l.unit_price)) })) }}
      submitLabel="Save changes" errorMessages={errorMessages} onCancel={onCancel}
      onSubmit={async (v, lines) => onSaved(await editInvoice(inv.id, inv.version, {
        invoice_date: String(v.invoice_date), lines: lines.map((l) => ({ id: l.id, qty: Number(l.qty), unit_price: l.unit_price })) }))}
    />
  )
}

function Detail({ id }: { id: number }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: queryKeys.detail(invoicesClient, id), queryFn: () => fetchInvoice(id) })
  const [editing, setEditing] = useState(false)
  const [releasing, setReleasing] = useState(false)
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: queryKeys.detail(invoicesClient, id) })
    await qc.invalidateQueries({ queryKey: queryKeys.list(invoicesClient) })
  }
  const send = useMutation({ mutationFn: () => sendInvoice(id), onSuccess: refresh })
  if (q.isLoading) return <Skeleton className="h-40 w-full" />
  if (q.error || !q.data) return <ErrorView error={q.error} />
  const inv = q.data
  const sendable = inv.status === 'Ready' || inv.status === 'Failed' || inv.status === 'Sent'
  if (editing) {
    return <InvoiceForm inv={inv} onCancel={() => setEditing(false)}
      onSaved={async (d) => { qc.setQueryData(queryKeys.detail(invoicesClient, id), d); setEditing(false); await refresh() }} />
  }
  return (
    <div className="flex flex-col gap-4 text-sm">
      <DetailList items={[
        ['Status', <StatusCell key="s" value={inv.status} />], ['Order', `${inv.order_number} · ${inv.partner_code} PO ${inv.po ?? ''}`],
        ['Method', inv.method], ['Delivery confirmed', inv.confirmed_date ? formatDate(inv.confirmed_date) : null],
        ['Invoice date', formatDate(inv.invoice_date)], ['Terms', inv.payment_terms], ['Total', formatMoney(inv.total, inv.currency)],
        ['Sent', inv.sent_at ? `${inv.send_count}× · last ${formatDateTime(inv.sent_at)}` : null], ['Problem', inv.error],
      ]} />
      {inv.status === 'Blocked' && <p className="text-warning-700">On hold: see the task for why. Release it once decided.</p>}
      <LocalTable ariaLabel="Invoice lines" columns={lineColumns(inv.currency)} rows={inv.lines} rowKey={(l) => l.id}
        footer={[null, null, null, 'Total', formatMoney(inv.total, inv.currency)]} />
      <div className="flex flex-wrap gap-2">
        {sendable && (
          <ActionButton size="sm" permission={MANAGE} onAction={() => send.mutateAsync().catch(() => {})}>
            {inv.status === 'Sent' ? 'Send again' : 'Send'}
          </ActionButton>
        )}
        {['Ready', 'Failed', 'Sent', 'Blocked'].includes(inv.status ?? '') && (
          <ActionButton size="sm" variant="secondary" permission={MANAGE} onAction={() => setEditing(true)}>Edit invoice</ActionButton>
        )}
        {inv.status === 'Blocked' && (
          <ActionButton size="sm" permission={MANAGE} onAction={() => setReleasing(true)}>Release</ActionButton>
        )}
      </div>
      <ErrorMessage error={send.error} messages={errorMessages} />
      <ConfirmAction open={releasing} onOpenChange={setReleasing} title={`Release invoice ${inv.invoice_number}?`}
        description="It goes back to Ready, to be sent." confirmLabel="Release" reason={{ label: 'Why it can go ahead' }}
        errorMessages={errorMessages} onConfirm={async (why) => { await releaseInvoice(inv.order_id, why); await refresh() }} />
    </div>
  )
}

/** Invoicing → Invoices: built at delivery confirmation (FR-OB-31), edited
 *  before or after sending (FR-OB-35), sent by the partner's method (FR-OB-30). */
export default function InvoicesPage() {
  return (
    <ListPage<InvoiceRead>
      resource={invoicesClient}
      title="Invoices"
      description="One per confirmed order, priced per case at the 850 price. Edit before sending; send by the partner's method (810 or email with PDFs)."
      columns={columns}
      emptyTitle="No invoices yet"
      detailTitle={(i) => `Invoice ${i.invoice_number}`}
      detailClassName="max-w-3xl"
      renderDetail={(i) => <Detail id={i.id} />}
    />
  )
}
