import { useState } from 'react'
import { useMutation, useQueries, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { z } from 'zod'
import {
  ADDRESS_FIELDS, ActionButton, Autocomplete, ErrorMessage, MasterDetailForm, Skeleton, addressSchema, cleanAddress, numberColumn, readColumn,
  selectColumn, type EditableColumn, type FieldSpec,
} from '@/platform-ui'
import { itemOptions, itemUnits, manualPartnerOptions } from '../configuration/lookups'
import {
  createManual, previewManual, updateManual, type LinePreview, type ManualLine, type ManualOrderIn, type OrderDetail, type OrderRead,
} from './ordersApi'

const OPERATE = 'sales.order.operate'
const blank: ManualLine = { item_code: '', quantity: 1, unit: 'EA' }

const schema = z.object({
  partner_code: z.string().min(1, 'Choose the customer'),
  customer_reference: z.string().trim().max(100).nullable().optional(),
  requested_delivery_date: z.string().nullable().optional(),
  notes: z.string().trim().max(2000).nullable().optional(),
  ship_to: addressSchema({ phone: true }),
})
// The form's ship-to: a draft's own, else blank in Canada (every part a string for the inputs).
const shipToDefaults = (a: Record<string, string | null> | null | undefined) =>
  Object.fromEntries(ADDRESS_FIELDS.map((k) => [k, a?.[k] ?? (k === 'country' ? 'CA' : '')]))
const fields: FieldSpec[] = [
  { name: 'partner_code', label: 'Customer', type: 'reference', required: true, loadOptions: (q) => manualPartnerOptions(q),
    hint: 'Who the order is for. Manual orders use partners whose channel is Manual (Configuration → Partners).' },
  { name: 'customer_reference', label: 'Customer PO / reference', hint: "The customer's own number for this order, if they gave one." },
  { name: 'requested_delivery_date', label: 'Requested delivery', type: 'date' },
  { name: 'notes', label: 'Notes', type: 'textarea' },
]

const lineProblem = (l: ManualLine) =>
  !l.item_code ? 'choose an item' : !(Number(l.quantity) > 0) ? 'a quantity above 0' : !l.unit ? 'a unit' : null

/** A manual order (FR-OB-19a), new or a Draft being edited: the customer, where
 *  it ships (required — the 940 carries it), the lines — each unit chosen from the item's own units — and their EA preview;
 *  saved only when every line converts. */
export function ManualOrderForm({ draft, onSaved, onCancel }: {
  draft?: OrderDetail; onSaved: (order: OrderRead) => void; onCancel: () => void
}) {
  const [preview, setPreview] = useState<LinePreview[] | null>(null)
  const [lines, setLines] = useState<ManualLine[]>(() => draft
    ? draft.lines.filter((l) => !l.removed).map((l) => ({ item_code: l.item_code ?? '', quantity: Number(l.ordered_qty), unit: l.order_unit ?? 'EA' }))
    : [{ ...blank }])
  const customers = useQuery({ queryKey: ['sales', 'lookup', 'manual-customers'], queryFn: () => manualPartnerOptions(''), enabled: !draft })
  const codes = [...new Set(lines.map((l) => l.item_code).filter(Boolean))]
  const unitQueries = useQueries({ queries: codes.map((c) => ({ queryKey: ['sales', 'lookup', 'units', c], queryFn: () => itemUnits(c), staleTime: 60_000 })) })
  const units: Record<string, { value: string; label: string }[]> = Object.fromEntries(codes.map((c, i) => [c, unitQueries[i]?.data ?? []]))
  const check = useMutation({ mutationFn: (ls: ManualLine[]) => previewManual(ls), onSuccess: setPreview })

  if (!draft && customers.isLoading) return <Skeleton className="h-40 w-full" />
  if (!draft && customers.data && customers.data.length === 0) {
    return (
      <p role="status" className="text-sm text-neutral-700">
        No customer can take a manual order yet: manual orders use partners whose channel is Manual. Add one under{' '}
        <Link to="/config/partners" className="font-medium text-primary-700 underline">Configuration → Partners</Link>, then come back.
      </p>
    )
  }
  const single = !draft && customers.data?.length === 1 ? customers.data[0].value : ''
  const columns: EditableColumn<ManualLine>[] = [
    { id: 'item', header: 'Item', cell: ({ row, index, update, invalid }) => (
      <Autocomplete aria-label={`Item ${index + 1}`} aria-invalid={invalid || undefined} value={row.item_code || null}
        onChange={(v) => update({ item_code: v ?? '', unit: 'EA' })} loadOptions={itemOptions} className="min-w-[16rem]" />
    ) },
    numberColumn<ManualLine>('quantity', 'Quantity', { label: (_, i) => `Quantity ${i + 1}`, get: (r) => r.quantity,
      set: (v) => ({ quantity: Number(v) }), className: 'w-24' }),
    selectColumn<ManualLine>('unit', 'Unit', { label: (_, i) => `Unit ${i + 1}`, get: (r) => r.unit, set: (v) => ({ unit: v ?? 'EA' }),
      options: (r) => units[r.item_code] ?? [], placeholder: 'Choose the item first' }),
    readColumn<ManualLine>('ea', 'EA', (r) => preview?.[lines.indexOf(r)]?.qty_ea ?? ''),
  ]
  return (
    <MasterDetailForm<ManualLine>
      schema={schema}
      fields={fields}
      defaultValues={{ partner_code: draft?.partner_code ?? single, customer_reference: draft?.source_order_number ?? '',
        requested_delivery_date: draft?.requested_delivery_date ?? '', notes: draft?.notes ?? '', ship_to: shipToDefaults(draft?.ship_to) }}
      addresses={[{ name: 'ship_to', legend: 'Ship to', phoneRequired: true }]}
      lines={{ label: 'Order lines', columns, initial: lines, add: () => ({ ...blank }), removable: true,
        problem: (l, i) => lineProblem(l) ?? preview?.[i]?.problem ?? null }}
      // The EA depends on the lines only: a header or address change keeps the preview.
      onChange={(st) => { if (st.lines !== lines) { setLines(st.lines); setPreview(null) } }}
      blocked={() => (preview === null ? 'Preview the EA conversion before saving.' : null)}
      preview={(st) => (
        <div className="flex flex-col gap-1">
          <ActionButton size="sm" variant="secondary" permission={OPERATE}
            onAction={() => check.mutateAsync(st.lines).catch(() => {})}>Preview EA</ActionButton>
          <ErrorMessage error={check.error} />
        </div>
      )}
      submitLabel={draft ? 'Save changes' : 'Save draft'}
      onCancel={onCancel}
      onSubmit={async (v, ls) => {
        const body: ManualOrderIn = { partner_code: String(v.partner_code), customer_reference: (v.customer_reference as string) ?? null,
          requested_delivery_date: (v.requested_delivery_date as string) ?? null, notes: (v.notes as string) ?? null,
          ship_to: cleanAddress(v.ship_to as Record<string, unknown>), lines: ls }
        onSaved(draft ? await updateManual(draft.id, body, draft.version) : await createManual(body))
      }}
    />
  )
}
