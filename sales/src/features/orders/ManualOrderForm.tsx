import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { z } from 'zod'
import {
  ADDRESS_FIELDS, ActionButton, Autocomplete, MasterDetailForm, formatNumber, Skeleton, addressSchema, cleanAddress, numberColumn, readColumn, selectColumn, type EditableColumn, type FieldSpec, useLookup, useLookups, ActionErrors, Text, Stack, versionOf,
} from '@/platform-ui'
import { itemOptions, itemUnits, manualPartnerOptions } from '../configuration/lookups'
import {
  MANUAL_SETTINGS_KEY, carriersFor, fetchManualOrderSettings, isCarrier, createManual, previewManual, updateManual, type LinePreview, type ManualLine, type ManualOrderIn, type OrderDetail, type OrderRead,
} from './ordersApi'

const OPERATE = 'sales.order.operate'
const blank: ManualLine = { item_code: '', quantity: 1, unit: 'EA' }

const schema = z.object({
  partner_code: z.string().min(1, 'Choose the customer'),
  customer_reference: z.string().trim().max(50).nullable().optional(),
  carrier: z.string().nullable().optional(),
  reason: z.string().nullable().optional(),
  requested_delivery_date: z.string().nullable().optional(),
  notes: z.string().trim().max(2000).nullable().optional(),
  ship_to: addressSchema({ phone: true }),
})
// The form's ship-to: a draft's own, else blank in Canada (every part a string for the inputs).
const shipToDefaults = (a: Record<string, string | null> | null | undefined) =>
  Object.fromEntries(ADDRESS_FIELDS.map((k) => [k, a?.[k] ?? (k === 'country' ? 'CA' : '')]))
const fieldsFor = (carriers: { value: string; label: string }[], reasons: string[]): FieldSpec[] => [
  { name: 'partner_code', label: 'Customer', type: 'reference', required: true, loadOptions: (q) => manualPartnerOptions(q),
    hint: 'Who the order is for. Manual orders use partners whose channel is Manual (Configuration → Partners).' },
  { name: 'customer_reference', label: 'Customer PO / reference', hint: "The customer's own number for this order, if they gave one." },
  { name: 'carrier', label: 'Ships by', type: 'segmented', options: carriers,
    hint: 'Purolator (Ground or Express): a parcel, sent like a Shopify order (the ship-to phone goes with it). LTL: a pallet shipment, sent like a retailer order. Needed before it is submitted.' },
  { name: 'reason', label: 'Reason', type: 'select', options: reasons.map((r) => ({ value: r, label: r })),
    hint: 'Why this order is made (Gift, Replacement, Sample …): Orders → Manual orders by reason lists them. Needed before it is submitted.' },
  { name: 'requested_delivery_date', label: 'Requested delivery', type: 'date' },
  { name: 'notes', label: 'Notes', type: 'textarea' },
]

const lineProblem = (l: ManualLine) =>
  !l.item_code ? 'choose an item' : !(Number(l.quantity) > 0) ? 'a quantity above 0' : !l.unit ? 'a unit' : null

/** The order's gross weight and the carrier it suggests (user 2026-11-13): above the setting's weight, LTL; else Purolator. */
function WeightHint({ preview, ltlFromLb }: { preview: LinePreview[]; ltlFromLb: number }) {
  const known = preview.filter((p) => p.weight_lb != null)
  if (known.length === 0) return null
  const lb = known.reduce((n, p) => n + Number(p.weight_lb), 0)
  const partial = known.length < preview.length
  return (
    <Text as="p" tone="soft" size="xs">
      About {formatNumber(Math.round(lb))} lb{partial ? ' (some items have no weight)' : ''}: suggested <strong>{lb > ltlFromLb ? 'LTL' : 'Purolator'}</strong> (LTL above {formatNumber(ltlFromLb)} lb).
    </Text>
  )
}

/** A manual order (FR-OB-19a), new or a Draft being edited: the customer, where
 *  it ships (required — the 940 carries it), the lines — each unit chosen from the item's own units — and their EA preview;
 *  saved only when every line converts. */
export function ManualOrderForm({ draft, onSaved, onCancel }: {
  draft?: OrderDetail; onSaved: (order: OrderRead) => void; onCancel: () => void
}) {
  const [preview, setPreview] = useState<LinePreview[] | null>(null)
  const saved = draft?.lines
  const start = useMemo<ManualLine[]>(() => {
    const own = (saved ?? []).filter((l) => !l.removed)
      .map((l) => ({ item_code: l.item_code ?? '', quantity: Number(l.ordered_qty), unit: l.order_unit ?? 'EA' }))
    return own.length ? own : [{ ...blank }]  // a reorder starts with no lines: one empty line to fill
  }, [saved])
  const [lines, setLines] = useState(start)
  const customers = useLookup(['sales', 'lookup', 'manual-customers'], () => manualPartnerOptions(''), { enabled: !draft })
  const codes = [...new Set(lines.map((l) => l.item_code).filter(Boolean))]
  const unitQueries = useLookups(codes.map((c) => ['sales', 'lookup', 'units', c]), (k) => itemUnits(String(k[3])), { staleTime: 60_000 })
  const units: Record<string, { value: string; label: string }[]> = Object.fromEntries(codes.map((c, i) => [c, unitQueries[i]?.data ?? []]))
  const settings = useLookup(MANUAL_SETTINGS_KEY, fetchManualOrderSettings)
  const fields = fieldsFor(carriersFor(settings.data?.express_offered ?? true), settings.data?.reasons ?? [])

  if (!draft && customers.isLoading) return <Skeleton className="h-40 w-full" />
  if (!draft && customers.data && customers.data.length === 0) {
    return (
      <Text as="p" tone="secondary" size="sm">
        No customer can take a manual order yet: manual orders use partners whose channel is Manual. Add one under{' '}
        <Link to="/config/partners" className="font-medium text-primary-700 underline">Configuration → Partners</Link>, then come back.
      </Text>
    )
  }
  const single = !draft && customers.data?.length === 1 ? customers.data[0].value : ''
  const columns: EditableColumn<ManualLine>[] = [
    { id: 'item', header: 'Item', cell: ({ row, index, update, invalid }) => (
      <Autocomplete aria-label={`Item ${index + 1}`} aria-invalid={invalid || undefined} value={row.item_code || null}
        onChange={(v) => update({ item_code: v ?? '', unit: 'EA' })} loadOptions={itemOptions} className="w-full sm:min-w-[16rem]" />
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
      defaultValues={{ partner_code: draft?.partner_code ?? single, customer_reference: draft?.source_order_number ?? '', carrier: draft?.shipping_method ?? '',
        reason: draft?.manual_reason ?? '', requested_delivery_date: draft?.requested_delivery_date ?? '', notes: draft?.notes ?? '', ship_to: shipToDefaults(draft?.ship_to) }}
      addresses={[{ name: 'ship_to', legend: 'Ship to', phoneRequired: true }]}
      lines={{ label: 'Order lines', columns, initial: start, add: () => ({ ...blank }), removable: true,
        problem: (l, i) => lineProblem(l) ?? preview?.[i]?.problem ?? null }}
      // The EA depends on the lines only: a header or address change keeps the preview.
      onChange={(st) => { if (st.lines !== lines) { setLines(st.lines); setPreview(null) } }}
      blocked={() => (preview === null ? 'Preview the EA conversion before saving.' : null)}
      preview={(st) => (
        <ActionErrors>
        <Stack gap={1}>
          <ActionButton size="sm" variant="secondary" permission={OPERATE}
            refreshes={false} after={(r) => setPreview(r as LinePreview[])} onAction={() => previewManual(st.lines)}>Preview EA</ActionButton>
          {preview && <WeightHint preview={preview} ltlFromLb={settings.data?.ltl_from_lb ?? 150} />}
        </Stack>
        </ActionErrors>
      )}
      submitLabel={draft ? 'Save changes' : 'Save draft'}
      version={draft?.version}
      onCancel={onCancel}
      onSubmit={async (v, ls, edit) => {
        const body: ManualOrderIn = { partner_code: String(v.partner_code), customer_reference: (v.customer_reference as string) ?? null,
          requested_delivery_date: (v.requested_delivery_date as string) ?? null, notes: (v.notes as string) ?? null,
          ship_to: cleanAddress(v.ship_to as Record<string, unknown>), lines: ls,
          carrier: isCarrier(v.carrier) ? v.carrier : null, reason: (v.reason as string) || null }
        onSaved(draft ? await updateManual(draft.id, body, versionOf(edit)) : await createManual(body))
      }}
    />
  )
}
