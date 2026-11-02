import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ActionButton, Autocomplete, EditableTable, ErrorMessage, FormField, Input, numberColumn, queryKeys, readColumn, textColumn, useToast,
  type EditableColumn,
} from '@/platform-ui'
import { itemOptions, manualPartnerOptions } from '../configuration/lookups'
import { OrderList } from './OrdersPage'
import {
  cancelManual, createManual, ordersClient, previewManual, submitManual,
  type LinePreview, type ManualLine, type OrderRead,
} from './ordersApi'

const OPERATE = 'sales.order.operate'
const blank: ManualLine = { item_code: '', quantity: 1, unit: 'EA' }

/** New manual order: the lines' EA preview before saving (FR-OB-19a). */
function NewManualOrder() {
  const qc = useQueryClient()
  const toast = useToast()
  const [partner, setPartner] = useState('')
  const [reference, setReference] = useState('')
  const [lines, setLines] = useState<ManualLine[]>([{ ...blank }])
  const [preview, setPreview] = useState<LinePreview[] | null>(null)
  const change = (next: ManualLine[]) => { setPreview(null); setLines(next) }
  const columns: EditableColumn<ManualLine>[] = [
    { id: 'item', header: 'Item', cell: ({ row, index, update, invalid }) => (
      <Autocomplete aria-label={`Item ${index + 1}`} aria-invalid={invalid || undefined} value={row.item_code || null}
        onChange={(v) => update({ item_code: v ?? '' })} loadOptions={itemOptions} />
    ) },
    numberColumn<ManualLine>('quantity', 'Quantity', { label: (_, i) => `Quantity ${i + 1}`, get: (r) => r.quantity,
      set: (v) => ({ quantity: Number(v) }), className: 'w-24' }),
    textColumn<ManualLine>('unit', 'Unit', { label: (_, i) => `Unit ${i + 1}`, get: (r) => r.unit, set: (v) => ({ unit: v }), className: 'w-24' }),
    readColumn<ManualLine>('ea', 'EA', (r) => preview?.[lines.indexOf(r)]?.qty_ea ?? ''),
  ]
  const check = useMutation({ mutationFn: () => previewManual(lines), onSuccess: setPreview })
  const save = useMutation({
    mutationFn: () => createManual({ partner_code: partner, customer_reference: reference || null, lines }),
    onSuccess: async (o) => {
      toast({ variant: 'success', title: `Draft ${o.order_number} saved` })
      setLines([{ ...blank }]); setPreview(null); setReference('')
      await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) })
    },
  })
  const clean = preview !== null && preview.every((p) => p.problem === null)
  return (
    <section aria-label="New manual order" className="flex flex-col gap-3 rounded-lg border border-neutral-200 bg-white p-4">
      <h2 className="font-semibold text-neutral-800">New manual order</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <FormField label="Partner (MANUAL channel)" htmlFor="m-partner" required>
          <Autocomplete id="m-partner" aria-label="Partner" value={partner || null} onChange={(v) => setPartner(v ?? '')} loadOptions={manualPartnerOptions} minChars={0} />
        </FormField>
        <FormField label="Customer reference" htmlFor="m-ref"><Input id="m-ref" value={reference} onChange={(e) => setReference(e.target.value)} /></FormField>
      </div>
      <EditableTable<ManualLine> ariaLabel="Order lines" rows={lines} onChange={change} columns={columns}
        errors={(_, i) => preview?.[i]?.problem} onAdd={() => ({ ...blank })} removable />
      <div className="flex flex-wrap gap-2">
        <ActionButton size="sm" variant="secondary" permission={OPERATE} onAction={() => check.mutateAsync().catch(() => {})}>Preview EA</ActionButton>
        <ActionButton size="sm" permission={OPERATE} disabled={!clean || !partner} onAction={() => save.mutateAsync().catch(() => {})}>Save draft</ActionButton>
      </div>
      <ErrorMessage error={check.error ?? save.error} />
    </section>
  )
}

function DraftActions({ o, close }: { o: OrderRead; close: () => void }) {
  const qc = useQueryClient()
  const done = async () => { await qc.invalidateQueries({ queryKey: queryKeys.list(ordersClient) }); close() }
  const submit = useMutation({ mutationFn: () => submitManual(o.id, o.version), onSuccess: done })
  const cancel = useMutation({ mutationFn: () => cancelManual(o.id, o.version), onSuccess: done })
  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <ActionButton size="sm" permission={OPERATE} onAction={() => submit.mutateAsync().catch(() => {})}>Submit</ActionButton>
        <ActionButton size="sm" variant="secondary" permission={OPERATE} onAction={() => cancel.mutateAsync().catch(() => {})}>Cancel draft</ActionButton>
      </div>
      <ErrorMessage error={submit.error ?? cancel.error} />
    </div>
  )
}

/** Manual orders (FR-OB-19a): Draft → Submit; a Draft is never sent. */
export default function ManualOrderPage() {
  return (
    <div className="flex flex-col gap-6">
      <NewManualOrder />
      <OrderList
        title="Manual drafts" description="Drafts are never sent. Submit makes the order Pending; it then follows the same flow as any other."
        emptyTitle="No drafts" fixed={[{ field: 'source_type', op: 'eq', value: 'MANUAL' }, { field: 'fulfillment_status', op: 'eq', value: 'Draft' }]}
        detailExtra={(o, close) => <DraftActions o={o} close={close} />}
      />
    </div>
  )
}
