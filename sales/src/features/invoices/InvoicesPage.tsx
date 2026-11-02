import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, DateCell, DateInput, DetailList, ErrorMessage, ErrorView, FormField, Input, ListPage, MoneyCell, NumberInput,
  Skeleton, StatusCell, formatDate, formatDateTime, formatMoney, queryKeys, type ColumnDef,
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

function Editor({ inv }: { inv: InvoiceDetail }) {
  const qc = useQueryClient()
  const [date, setDate] = useState(inv.invoice_date)
  const [lines, setLines] = useState(() => inv.lines.map((l) => ({ id: l.id, qty: String(l.qty), unit_price: String(Number(l.unit_price)) })))
  const save = useMutation({
    mutationFn: () => editInvoice(inv.id, inv.version, {
      invoice_date: date, lines: lines.map((l) => ({ id: l.id, qty: Number(l.qty), unit_price: l.unit_price })),
    }),
    onSuccess: (r) => { qc.setQueryData(queryKeys.detail(invoicesClient, inv.id), r); return qc.invalidateQueries({ queryKey: queryKeys.list(invoicesClient) }) },
  })
  const valid = lines.every((l) => Number.isInteger(Number(l.qty)) && Number(l.qty) >= 0 && Number(l.unit_price) >= 0)
  return (
    <section aria-label="Edit invoice" className="flex flex-col gap-2">
      <FormField label="Invoice date" htmlFor="inv-date"><DateInput id="inv-date" value={date} onChange={(e) => setDate(e.target.value)} /></FormField>
      <table className="w-full text-xs">
        <thead><tr className="text-left text-neutral-500"><th>#</th><th>Item</th><th>Qty (CA)</th><th>Unit price</th><th>Amount</th></tr></thead>
        <tbody>
          {inv.lines.map((l, i) => (
            <tr key={l.id} className="border-t border-neutral-100">
              <td className="py-1">{l.line_no}</td>
              <td>{l.item_code} {l.buyer_part ? `(${l.buyer_part})` : ''}</td>
              <td><NumberInput aria-label={`Quantity line ${l.line_no}`} value={lines[i].qty} onChange={(e) => setLines((xs) => xs.map((x, j) => j === i ? { ...x, qty: e.target.value } : x))} className="w-20" /></td>
              <td><Input aria-label={`Unit price line ${l.line_no}`} value={lines[i].unit_price} onChange={(e) => setLines((xs) => xs.map((x, j) => j === i ? { ...x, unit_price: e.target.value } : x))} className="w-28" /></td>
              <td>{formatMoney(l.amount, inv.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ActionButton size="sm" variant="secondary" permission={MANAGE} disabled={!valid} onAction={() => save.mutateAsync().catch(() => {})}>Save changes</ActionButton>
      <ErrorMessage error={save.error} messages={errorMessages} />
    </section>
  )
}

function Detail({ id }: { id: number }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: queryKeys.detail(invoicesClient, id), queryFn: () => fetchInvoice(id) })
  const [reason, setReason] = useState('')
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: queryKeys.detail(invoicesClient, id) })
    await qc.invalidateQueries({ queryKey: queryKeys.list(invoicesClient) })
  }
  const send = useMutation({ mutationFn: () => sendInvoice(id), onSuccess: refresh })
  const release = useMutation({ mutationFn: () => releaseInvoice(q.data!.order_id, reason.trim()), onSuccess: refresh })
  if (q.isLoading) return <Skeleton className="h-40 w-full" />
  if (q.error || !q.data) return <ErrorView error={q.error} />
  const inv = q.data
  const sendable = inv.status === 'Ready' || inv.status === 'Failed' || inv.status === 'Sent'
  return (
    <div className="flex flex-col gap-4 text-sm">
      <DetailList items={[
        ['Status', <StatusCell key="s" value={inv.status} />], ['Order', `${inv.order_number} · ${inv.partner_code} PO ${inv.po ?? ''}`],
        ['Method', inv.method], ['Delivery confirmed', inv.confirmed_date ? formatDate(inv.confirmed_date) : null],
        ['Terms', inv.payment_terms], ['Total', formatMoney(inv.total, inv.currency)],
        ['Sent', inv.sent_at ? `${inv.send_count}× · last ${formatDateTime(inv.sent_at)}` : null], ['Problem', inv.error],
      ]} />
      {inv.status === 'Blocked' && (
        <section aria-label="Release" className="flex flex-col gap-2 rounded-md border border-warning-200 bg-warning-50 p-2">
          <p className="text-warning-700">On hold: see the task for why. Release it once decided.</p>
          <FormField label="Why it can go ahead" htmlFor="inv-release"><Input id="inv-release" value={reason} onChange={(e) => setReason(e.target.value)} /></FormField>
          <ActionButton size="sm" permission={MANAGE} disabled={reason.trim().length < 3} onAction={() => release.mutateAsync().catch(() => {})}>Release</ActionButton>
          <ErrorMessage error={release.error} messages={errorMessages} />
        </section>
      )}
      {['Ready', 'Failed', 'Sent', 'Blocked'].includes(inv.status ?? '') && <Editor key={inv.version} inv={inv} />}
      {sendable && (
        <ActionButton size="sm" permission={MANAGE} onAction={() => send.mutateAsync().catch(() => {})}>
          {inv.status === 'Sent' ? 'Send again' : 'Send'}
        </ActionButton>
      )}
      <ErrorMessage error={send.error} messages={errorMessages} />
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
