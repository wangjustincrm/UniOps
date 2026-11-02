import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ActionButton, Autocomplete, Button, ErrorMessage, FormField, Input, NumberInput, queryKeys, useToast } from '@/platform-ui'
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
  const set = (i: number, k: keyof ManualLine, v: string) => {
    setPreview(null)
    setLines(lines.map((l, j) => (j === i ? { ...l, [k]: k === 'quantity' ? Number(v) : v } : l)))
  }
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
      <table className="w-full text-sm">
        <thead><tr className="text-left text-xs text-neutral-500"><th>Item</th><th>Quantity</th><th>Unit</th><th>EA</th></tr></thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td><Autocomplete aria-label={`Item ${i + 1}`} value={l.item_code || null} onChange={(v) => set(i, 'item_code', v ?? '')} loadOptions={itemOptions} /></td>
              <td><NumberInput aria-label={`Quantity ${i + 1}`} value={l.quantity} onChange={(e) => set(i, 'quantity', e.target.value)} /></td>
              <td><Input aria-label={`Unit ${i + 1}`} value={l.unit} onChange={(e) => set(i, 'unit', e.target.value)} /></td>
              <td className="px-2 text-xs">{preview?.[i] ? (preview[i].problem ? <span className="text-danger-600">{preview[i].problem}</span> : preview[i].qty_ea) : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="secondary" size="sm" onClick={() => { setLines([...lines, { ...blank }]); setPreview(null) }}>Add line</Button>
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
