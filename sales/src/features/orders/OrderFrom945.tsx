import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, AddressView, Autocomplete, Callout, DetailList, ErrorMessage, Hint, LocalTable, Section, Skeleton, formatDate, queryKeys, useOpenRecord,
  useNotices, type ColumnDef, columnsOf
 } from '@/platform-ui'
import { documentsClient } from '@/features/operations/operationsApi'
import { partnerOptions } from '@/features/configuration/lookups'
import { createOrderFrom945, fetchShipmentsWithoutOrder, ordersClient, type OrderFrom945Plan } from './ordersApi'

const colLine = columnsOf<Line>()

const OPERATE = 'sales.order.operate'
type Line = OrderFrom945Plan['lines'][number]

const errorMessages: Record<string, string> = {
  'order945.partner_required': 'Choose the partner: this 945 does not name one Sales knows.',
  'order945.has_order': 'This 945 already has an order.',
  'partner.no_abbrev': 'This partner has no abbreviation: set it under Configuration → Partners.',
  'auth.forbidden': 'You do not have permission to create orders.',
}

const columns: ColumnDef<Line, unknown>[] = [
  colLine.text('received', 'Item'),
  { accessorKey: 'qty_ea', header: 'Qty EA', cell: ({ row }) => <span className="tabular-nums">{row.original.qty_ea}</span> },
  { accessorKey: 'shipped_ea', header: 'Shipped EA', cell: ({ row }) => <span className="tabular-nums">{row.original.shipped_ea}</span> },
  { accessorKey: 'problem', header: 'Note', cell: ({ row }) => row.original.problem ?? '' },
]

function One({ p }: { p: OrderFrom945Plan }) {
  const qc = useQueryClient()
  const openRecord = useOpenRecord()
  const notices = useNotices()
  const [partner, setPartner] = useState<string | null>(p.partner_code)
  const m = useMutation({
    mutationFn: () => createOrderFrom945(p.shipment_id, partner),
    onSuccess: async (o) => {
      notices.notify({ key: `from945:${p.shipment_id}`, tone: 'success', text: `Order ${o.source_reference ?? o.order_number} created from this 945.` })
      await qc.invalidateQueries({ queryKey: queryKeys.list(documentsClient) })
      await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
      await qc.invalidateQueries({ queryKey: ['shipments-without-order', p.document_id] })
      openRecord('/orders', o.id)
    },
  })
  const joins = p.order_id != null
  return (
    <Section title={`Warehouse order ${p.order_key ?? '—'}`}>
      <DetailList items={[
        ['945 reference', p.reference && <span key="r" className="break-all">{p.reference}</span>], ['PO', p.po], ['Ship date', p.ship_date ? formatDate(p.ship_date) : null],
        ['Partner on the 945', p.partner_name_received],
      ]} />
      <AddressView value={p.ship_to} note="From the 945 ship-to" />
      <LocalTable<Line> ariaLabel={`Lines of warehouse order ${p.order_key ?? ''}`} columns={columns} rows={p.lines} rowKey={(l) => l.received} />
      {p.refused ? <Callout label="Not possible" title="No order can be created">{p.refused_message}</Callout> : (
        <div className="flex flex-col gap-2">
          {joins ? <Hint as="p">This is another part of order {p.order_ref}: it joins that order.</Hint> : (
            <label className="flex max-w-sm flex-col gap-1 text-sm">
              Partner
              <Autocomplete aria-label="Partner" value={partner} onChange={setPartner} loadOptions={partnerOptions} />
            </label>
          )}
          <Hint as="p">
            A shadow-run order: Zoho still handles it. No prices, and it never gets a 940, a Shopify write, an invoice, an email or a task.
          </Hint>
          <div>
            <ActionButton size="sm" permission={OPERATE} errorMessages={errorMessages} disabled={!joins && !partner} onAction={() => m.mutateAsync()}>
              {joins ? 'Add to the order' : 'Create order'}
            </ActionButton>
          </div>
        </div>
      )}
    </Section>
  )
}

/** A 945 that matched no order (FR-OB-25c): what it shipped, and the order to make from it.
 *  Partner and PO come from the reference Zoho wrote in the 945; without one a person chooses. */
export function OrderFrom945({ documentId }: { documentId: number }) {
  const q = useQuery({ queryKey: ['shipments-without-order', documentId], queryFn: () => fetchShipmentsWithoutOrder(documentId) })
  if (q.isError) return <ErrorMessage error={q.error} />
  if (!q.data) return <Skeleton className="h-24" />
  return <div className="flex flex-col gap-4">{q.data.map((p) => <One key={p.shipment_id} p={p} />)}</div>
}
