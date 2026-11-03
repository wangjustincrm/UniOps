import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import {
  ActionButton, ConfirmAction, DateCell, DetailList, ErrorMessage, ListPage, LocalTable, MasterDetailForm, MoneyCell,
  RecordSheet, Section, StatusCell, formatDate, formatDateTime, formatMoney, numberColumn, queryKeys, readColumn, textColumn,
  type ColumnDef, type EditableColumn, type FieldSpec, type RecordTab,
} from '@/platform-ui'
import { editInvoice, fetchInvoice, invoicesClient, releaseInvoice, sendInvoice, type InvoiceDetail, type InvoiceRead } from './invoicesApi'
import { PartnerCell } from '@/features/common/PartnerCell'
import { partnerLabel } from '@/features/common/labels'

const MANAGE = 'sales.invoice.manage'
const errorMessages: Record<string, string> = {
  'invoice.shadow': 'The shadow run is on: nothing is invoiced (Zoho is live).',
  'invoice.shadow_order': 'This order was received during the shadow run: Zoho invoices it.',
  'invoice.not_sendable': 'The invoice cannot be sent in its current status.',
  'invoice.not_editable': 'The invoice cannot be edited in its current status.',
  'entity.stale': 'Someone else changed this invoice. Close it and open it again.',
  'auth.forbidden': 'You do not have permission to manage invoices.',
  'invoice.reason_required': 'An invoice price changed: give the reason for the price change.',
}

const columns: ColumnDef<InvoiceRead, unknown>[] = [
  { accessorKey: 'invoice_number', header: 'Invoice' },
  { accessorKey: 'order_ref', header: 'Order ref', cell: ({ row }) => row.original.order_ref ?? '' },
  { accessorKey: 'order_number', header: 'Internal no.' },
  { accessorKey: 'partner_name', header: 'Partner', cell: ({ row }) => <PartnerCell name={row.original.partner_name} code={row.original.partner_code} /> },
  { accessorKey: 'po', header: 'PO' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => <StatusCell value={row.original.status} /> },
  { accessorKey: 'method', header: 'Method' },
  { accessorKey: 'invoice_date', header: 'Date', cell: ({ row }) => <DateCell value={row.original.invoice_date} /> },
  { accessorKey: 'total', header: 'Amount', cell: ({ row }) => <MoneyCell amount={row.original.total} currency={row.original.currency} /> },
  { accessorKey: 'send_count', header: 'Sent', cell: ({ row }) => row.original.sent_at ? `${row.original.send_count}× · ${formatDateTime(row.original.sent_at)}` : '—' },
]

type EditLine = { id: number; line_no: number; item: string; amount: string; qty: string; unit_price: string; was: string; po_price: string | null }

// Prices compare as numbers ("48.50000" is 48.5).
const differs = (a: string | null | undefined, b: string | null | undefined) =>
  a != null && b != null && a.trim() !== '' && Number(a) !== Number(b)

/** The PO's price beside the invoice's, marked when they differ. */
function PriceVsPo({ price, po, currency }: { price: string; po: string | null; currency: string }) {
  if (!differs(price, po)) return null
  const d = Number(price) - Number(po)
  return <span className="ml-1 text-xs font-medium text-warning-700">{d > 0 ? '+' : ''}{formatMoney(d.toFixed(2), currency)} vs PO</span>
}

const editColumns = (currency: string): EditableColumn<EditLine>[] => [
  readColumn<EditLine>('line_no', '#', (l) => l.line_no),
  readColumn<EditLine>('item', 'Item', (l) => l.item),
  numberColumn<EditLine>('qty', 'Qty (CA)', { label: (l) => `Quantity line ${l.line_no}`, get: (l) => l.qty, set: (v) => ({ qty: v }), min: 0, className: 'w-20' }),
  readColumn<EditLine>('po_price', 'PO price', (l) => (l.po_price == null ? '—' : formatMoney(l.po_price, currency))),
  textColumn<EditLine>('unit_price', 'Invoice price', { label: (l) => `Unit price line ${l.line_no}`, get: (l) => l.unit_price, set: (v) => ({ unit_price: v }) }),
  readColumn<EditLine>('vs', '', (l) => <PriceVsPo price={l.unit_price} po={l.po_price} currency={currency} />),
  readColumn<EditLine>('amount', 'Amount', (l) => formatMoney(l.amount, currency)),
]

function lineProblem(l: EditLine): string | null {
  if (!Number.isInteger(Number(l.qty)) || Number(l.qty) < 0) return 'a whole number of cases, 0 or more'
  if (l.unit_price.trim() === '' || Number.isNaN(Number(l.unit_price)) || Number(l.unit_price) < 0) return 'a price of 0 or more'
  return null
}

const editSchema = z.object({
  invoice_date: z.string().min(1, 'Required'),
  delivery_number: z.string().trim().max(50, 'At most 50 characters').nullable().optional(),
  reason: z.string().trim().max(500).nullable().optional(),
})
const editFields: FieldSpec[] = [
  { name: 'invoice_date', label: 'Delivery date (the invoice date)', type: 'date', required: true },
  { name: 'delivery_number', label: 'Delivery number', hint: "The retailer's proof-of-delivery or receiving number. On the invoice PDF (the 810 has no field for it)." },
  { name: 'reason', label: 'Reason for a price change', type: 'textarea', hint: 'Needed when an invoice price changes; kept in the history.' },
]

const lineColumns = (currency: string): ColumnDef<InvoiceDetail['lines'][number], unknown>[] => [
  { accessorKey: 'line_no', header: '#' },
  { id: 'item', header: 'Item', cell: ({ row: { original: l } }) => `${l.item_code ?? ''}${l.buyer_part ? ` (${l.buyer_part})` : ''}` },
  { accessorKey: 'qty', header: 'Qty (CA)' },
  { accessorKey: 'po_price', header: 'PO price', cell: ({ row }) => (row.original.po_price == null ? '—'
    : <MoneyCell amount={row.original.po_price} currency={currency} />) },
  { accessorKey: 'unit_price', header: 'Invoice price', cell: ({ row: { original: l } }) => (
    <span className={differs(l.unit_price, l.po_price) ? 'rounded bg-warning-50 px-1' : ''}>
      <MoneyCell amount={l.unit_price} currency={currency} /><PriceVsPo price={l.unit_price} po={l.po_price} currency={currency} />
    </span>
  ) },
  { accessorKey: 'amount', header: 'Amount', cell: ({ row }) => <MoneyCell amount={row.original.amount} currency={currency} /> },
]

/** FR-OB-35: quantities, unit prices and the date — the standard form, in the sheet. */
function InvoiceForm({ inv, onSaved, onCancel }: { inv: InvoiceDetail; onSaved: (d: InvoiceDetail) => void; onCancel: () => void }) {
  return (
    <MasterDetailForm<EditLine>
      schema={editSchema} fields={editFields}
      defaultValues={{ invoice_date: inv.invoice_date, delivery_number: inv.delivery_number ?? '', reason: '' }}
      lines={{ label: 'Invoice lines', columns: editColumns(inv.currency), rowKey: (l) => l.id, problem: lineProblem, minRows: 0,
        initial: inv.lines.map((l) => ({ id: l.id, line_no: l.line_no, item: `${l.item_code ?? ''}${l.buyer_part ? ` (${l.buyer_part})` : ''}`,
          amount: l.amount, qty: String(l.qty), unit_price: String(Number(l.unit_price)), was: String(Number(l.unit_price)),
          po_price: l.po_price })) }}
      // A changed price needs its reason (the server refuses it without one).
      blocked={({ values, lines }) => (lines.some((l) => differs(l.unit_price, l.was)) && !String(values.reason ?? '').trim()
        ? 'An invoice price changed: give the reason for the price change.' : null)}
      submitLabel="Save changes" errorMessages={errorMessages} onCancel={onCancel}
      onSubmit={async (v, lines) => onSaved(await editInvoice(inv.id, inv.version, {
        invoice_date: String(v.invoice_date), delivery_number: (v.delivery_number as string | null) ?? null,
        reason: (v.reason as string | null) ?? null,
        lines: lines.map((l) => ({ id: l.id, qty: Number(l.qty), unit_price: l.unit_price })) }))}
    />
  )
}

function Detail({ row }: { row: InvoiceRead }) {
  const id = row.id
  const qc = useQueryClient()
  const q = useQuery({ queryKey: queryKeys.detail(invoicesClient, id), queryFn: () => fetchInvoice(id) })
  const [editing, setEditing] = useState(false)
  const [releasing, setReleasing] = useState(false)
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: queryKeys.detail(invoicesClient, id) })
    await qc.invalidateQueries({ queryKey: queryKeys.list(invoicesClient) })
  }
  const send = useMutation({ mutationFn: () => sendInvoice(id), onSuccess: refresh })
  const inv = q.data
  const sendable = inv && (inv.status === 'Ready' || inv.status === 'Failed' || inv.status === 'Sent')
  const tabs: RecordTab[] = !inv ? [] : editing ? [{
    id: 'edit', label: 'Edit invoice', render: () => (
      <InvoiceForm inv={inv} onCancel={() => setEditing(false)}
        onSaved={async (d) => { qc.setQueryData(queryKeys.detail(invoicesClient, id), d); setEditing(false); await refresh() }} />),
  }] : [
    { id: 'overview', label: 'Overview', tone: inv.status === 'Blocked' || inv.error ? 'warning' : undefined, render: () => (
      <>
        {inv.status === 'Blocked' && (
          <p role="status" className="rounded-md border border-warning-200 bg-warning-50 p-2 text-sm text-warning-700">
            On hold: see the task for why. Release it once decided.
          </p>
        )}
        <Section title="Invoice">
          <DetailList items={[
            ['Order ref', inv.order_ref], ['Internal no.', inv.order_number == null ? null : String(inv.order_number)], ['PO', inv.po],
            ['Partner', partnerLabel(inv.partner_name, inv.partner_code)],
            ['Delivery number', inv.delivery_number],
            ['Method', inv.method], ['Delivery confirmed (the invoice date)', inv.confirmed_date ? formatDate(inv.confirmed_date) : null],
            ['Invoice date', formatDate(inv.invoice_date)], ['Terms', inv.payment_terms],
            ['Sent', inv.sent_at ? `${inv.send_count}× · last ${formatDateTime(inv.sent_at)}` : null], ['Problem', inv.error],
          ]} />
        </Section>
      </>
    ) },
    { id: 'lines', label: 'Lines', count: inv.lines.length, render: () => (
      <LocalTable ariaLabel="Invoice lines" columns={lineColumns(inv.currency)} rows={inv.lines} rowKey={(l) => l.id}
        footer={[null, null, null, null, 'Total', formatMoney(inv.total, inv.currency)]} />
    ) },
  ]
  return (
    <>
      <RecordSheet
        title={inv?.invoice_number ?? row.invoice_number}
        subtitle={[row.order_ref, row.order_number != null && `internal no. ${row.order_number}`,
          partnerLabel(row.partner_name, row.partner_code)].filter(Boolean).join(' · ')}
        badges={<StatusCell value={inv?.status ?? row.status} />}
        loading={q.isLoading} error={q.error} errorMessages={errorMessages}
        onRefresh={() => q.refetch()} updatedAt={q.dataUpdatedAt} fetching={q.isFetching}
        facts={inv ? [
          { label: 'Total', value: formatMoney(inv.total, inv.currency), hint: inv.currency },
          { label: 'Status', value: inv.status ?? '—', tone: inv.status === 'Blocked' || inv.status === 'Failed' ? 'warning' : inv.status === 'Sent' ? 'success' : 'neutral' },
          { label: 'Method', value: inv.method },
          { label: 'Invoice date', value: formatDate(inv.invoice_date) },
          { label: 'Delivery no.', value: inv.delivery_number ?? '—' },
          { label: 'Sent', value: inv.sent_at ? `${inv.send_count}×` : 'Not yet', hint: inv.sent_at ? formatDateTime(inv.sent_at) : undefined },
        ] : undefined}
        actions={inv && !editing ? (
          <>
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
            <ErrorMessage error={send.error} messages={errorMessages} />
          </>
        ) : undefined}
        tabs={tabs}
      />
      {inv && (
        <ConfirmAction open={releasing} onOpenChange={setReleasing} title={`Release invoice ${inv.invoice_number}?`}
          description="It goes back to Ready, to be sent." confirmLabel="Release" reason={{ label: 'Why it can go ahead' }}
          errorMessages={errorMessages} onConfirm={async (why) => { await releaseInvoice(inv.order_id, why); await refresh() }} />
      )}
    </>
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
      renderDetail={(i) => <Detail row={i} />}
    />
  )
}
