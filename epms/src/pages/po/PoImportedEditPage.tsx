import { useEffect, useRef, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useReplaceTab } from '@uniops/shell'
import { ArrowLeft } from 'lucide-react'
import { epmsRoutes } from '@/app/routes'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { ImportedPoLineItems, type ImportedPoLine } from '@/components/po/ImportedPoLineItems'
import { usePo, useUpdatePoImportedDetails, useRegeneratePoPdf } from '@/hooks/usePos'
import { useTaxCodes } from '@/hooks/useTaxCodes'
import { useConfig } from '@/hooks/useConfig'
import { formatAmount, formatDate } from '@/lib/utils'
import type { ImportedDetailsBody } from '@/services/po'

// Same mapping as PoDetailPage.tsx / PoListPage.tsx.
const TYPE_LABELS: Record<number, string> = {
  1: 'Raw Mat./Pack.',
  2: 'Consumables',
  3: 'Spare Parts',
  4: 'Service',
  5: 'Fixed Asset',
  6: 'Project',
}

/** Buyer-detail form for an NC-imported PO.
 *
 *  Only reachable for source='nc' + status='issued'. Vendor, currency, title,
 *  and budget code and every line quantity/price are display-only: NC owns
 *  them, and the backend endpoint has no field for them at all.
 */
export default function PoImportedEditPage() {
  const { id } = useParams<{ id: string }>()
  const replaceTab = useReplaceTab(epmsRoutes)
  const { data: po, isLoading } = usePo(id ?? '')
  // useTaxCodes() returns the TaxCode[] array directly (not wrapped in a
  // query-result object) — see hooks/useTaxCodes.ts. PoEditPage.tsx consumes
  // it the same way.
  const taxCodes = useTaxCodes()
  const { data: config } = useConfig()
  const saveDetails = useUpdatePoImportedDetails(id ?? '')
  const regeneratePdf = useRegeneratePoPdf(id ?? '')

  const [expectedDelivery, setExpectedDelivery] = useState('')
  const [deliveryAddress, setDeliveryAddress] = useState('')
  const [incoterms, setIncoterms] = useState('')
  const [taxCode, setTaxCode] = useState<string | null>(null)
  const [taxRate, setTaxRate] = useState(0)
  const [isPrepaid, setIsPrepaid] = useState(false)
  const [buyerNotes, setBuyerNotes] = useState('')
  const [lines, setLines] = useState<ImportedPoLine[]>([])
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Tracks which PO id the Delivery Address default has already been decided
  // for (applied, or skipped because the PO has its own) — see the effect
  // below. A ref, not state: this is a one-time-per-PO decision, not a value
  // that should drive re-renders or appear in the effect's own deps.
  const addressDefaultAppliedFor = useRef<string | null>(null)
  // Tracks whether the buyer has interacted with the Delivery Address field
  // for the current PO — separately from what the field currently contains.
  // See the effect below for why this, not the field's value, is the guard.
  const deliveryAddressTouched = useRef(false)

  // The ERP's earliest per-line delivery date. Computed ahead of the prefill
  // effect below so both it and the read-only hint text can use the same
  // value; kept a plain expression (not memoized) since it is cheap and po
  // changes trigger a re-render anyway.
  const erpDeliveryDate = (po?.line_items ?? []).reduce<string | undefined>((earliest, li) => {
    if (!li.planned_arrival_date) return earliest
    return !earliest || li.planned_arrival_date < earliest ? li.planned_arrival_date : earliest
  }, undefined)

  // Prefill from the loaded PO. Same shape as PoEditPage / PaEditPage / PrEditPage,
  // which carry this pattern unsuppressed; suppressed here so this page does not
  // move the repo's lint baseline. Restructuring would diverge from those three.
  useEffect(() => {
    if (!po) return
    // The Expected Delivery input only ever renders when neither a stored
    // header value nor an ERP roll-up exists (see showExpectedDeliveryInput
    // below), so there is nothing to prefill it with — start blank.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setExpectedDelivery('')
    setDeliveryAddress(po.delivery_address ?? '')
    // Reset both per-PO decision refs alongside the field they gate, because
    // this effect just rebuilt the form from the PO: any earlier decision
    // derived from the previous form state (which PO's default was already
    // applied, whether the buyer has touched the field) is now stale and must
    // be re-decided. This also covers React StrictMode's mount/unmount/
    // remount in development — the second mount re-runs this effect and
    // re-blanks deliveryAddress, so addressDefaultAppliedFor must be cleared
    // here too or the company-default effect below sees its "already decided
    // for this PO" guard still set from the first mount and never re-applies.
    deliveryAddressTouched.current = false
    addressDefaultAppliedFor.current = null
    setIncoterms(po.incoterms ?? '')
    setTaxCode(po.tax_code ?? null)
    setTaxRate(Number(po.tax_rate))
    setIsPrepaid(po.is_prepaid ?? false)
    setBuyerNotes(po.buyer_notes ?? '')
    setLines(po.line_items.map((li) => ({
      id: li.id,
      description: li.description,
      materialId: li.material_id ?? null,
      qty: Number(li.qty),
      unit: li.unit,
      unitPrice: Number(li.unit_price),
      lineTotal: Number(li.line_total),
      supplierItemId: li.supplier_item_id ?? '',
      sample: li.sample ?? '',
      plannedArrivalDate: li.planned_arrival_date ?? null,
    })))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [po?.id])

  // Delivery Address prefill from the company default — same pattern as
  // PoCreatePage.tsx. usePo() and useConfig() are independent queries with no
  // ordering guarantee, so config can resolve at any point relative to the
  // buyer's own edits — before them, during them, or after. The guard below
  // deliberately keys on *interaction*, not on the field's current value:
  // an empty field nobody has touched yet is a candidate for the default; an
  // empty field the buyer deliberately cleared is their choice, and the two
  // are indistinguishable by value alone. A value-based guard (checking
  // !deliveryAddress) previously made a cleared field snap the default right
  // back, because clearing it made it look "untouched" again. A value-free
  // guard (no local-state check at all) previously let a late-resolving
  // config overwrite text the buyer had already typed during the gap before
  // config arrived. deliveryAddressTouched.current — set true in the
  // textarea's onChange, independent of what the field currently contains —
  // resolves both: it flips permanently for this PO the instant the buyer
  // does anything, so config can never clobber real edits (typed or
  // cleared), while still being free to apply the default whenever it
  // arrives for a field nobody has touched. Do not "simplify" this back to
  // a check on deliveryAddress itself — that reintroduces one of the two
  // bugs above. addressDefaultAppliedFor additionally tracks the decision
  // itself, keyed on po.id, so navigating to a different PO re-decides (and
  // deliveryAddressTouched is reset alongside it in the prefill effect above).
  useEffect(() => {
    if (!po?.id) return
    if (addressDefaultAppliedFor.current === po.id) return // already decided for this PO
    if (po.delivery_address) {
      // PO has its own — never override, and nothing left to decide later.
      addressDefaultAppliedFor.current = po.id
      return
    }
    if (deliveryAddressTouched.current) return // buyer already acted — their value wins, forever, for this PO
    if (!config?.delivery_address) return // config not in yet; try again when it arrives
    addressDefaultAppliedFor.current = po.id
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDeliveryAddress(config.delivery_address)
  }, [po?.id, po?.delivery_address, config?.delivery_address])

  const editable = po?.source === 'nc' && po?.status === 'issued'
  const isCadOrder = po?.currency === 'CAD'
  // Same rule as PoCreatePage / PoEditPage — tax only applies to CAD orders.
  // On a non-CAD NC PO this page never touches tax (see handleSave), so the
  // preview below must show the PO's own stored tax_amount/total rather than
  // recompute off a rate this page never sends — otherwise it reads as "tax
  // will be cleared on save" when it will not be.
  const effectiveTaxRate = isCadOrder ? taxRate : 0
  const subtotal = Number(po?.subtotal ?? 0)
  const taxAmount = isCadOrder
    ? Math.round(subtotal * effectiveTaxRate * 100) / 100
    : Number(po?.tax_amount ?? 0)
  const total = isCadOrder ? subtotal + taxAmount : Number(po?.total ?? subtotal)
  const currency = po?.currency ?? 'CAD'

  // Expected Delivery is read-only whenever there's already something to
  // show — a stored header value, or the ERP roll-up — per the user's rule:
  // only editable when there is nothing to show. The header value (when
  // present) always wins over the ERP roll-up for display.
  const expectedDeliveryDisplay = po?.expected_delivery
    ? formatDate(po.expected_delivery)
    : erpDeliveryDate
      ? formatDate(erpDeliveryDate)
      : null
  const showExpectedDeliveryInput = !expectedDeliveryDisplay
  const expectedDeliveryHint = po?.expected_delivery
    ? undefined
    : erpDeliveryDate
      ? 'Tracks NC automatically.'
      : undefined

  const handleSave = async () => {
    if (!id) return
    setIsSubmitting(true)
    setError(null)
    try {
      const payload: ImportedDetailsBody = {
        delivery_address: deliveryAddress || null,
        incoterms: incoterms || null,
        is_prepaid: isPrepaid,
        buyer_notes: buyerNotes || null,
        lines: lines.map((l) => ({
          id: l.id,
          // Empty string means "cleared" — the backend distinguishes an
          // absent key from an explicit null, so send null (not '') to
          // actually clear a previously-saved value.
          supplier_item_id: l.supplierItemId || null,
          sample: l.sample || null,
        })),
      }
      // Tax only applies to CAD orders — this page has no input for it on a
      // non-CAD PO (see isCadOrder above). NC POs are not always CAD and not
      // always zero-tax, so omit tax_rate/tax_code entirely rather than
      // sending 0/null: the backend's absent-key contract (model_fields_set)
      // leaves the stored tax_rate/tax_amount/total untouched. Sending zero
      // here would zero out real tax on every non-CAD save, even one that
      // only changed e.g. Incoterms.
      if (isCadOrder) {
        // Mirror PoEditPage's rule: never send a tax code paired with a zero
        // rate.
        payload.tax_code = effectiveTaxRate > 0 ? taxCode : null
        payload.tax_rate = effectiveTaxRate
      }
      // The Expected Delivery input only renders when there's nothing to
      // show (no stored header value, no ERP roll-up) — in every other case
      // it's read-only text and the key must stay absent entirely, so the
      // backend's absent-key contract leaves the stored value (NULL, or an
      // existing override) untouched and display keeps tracking NC.
      if (showExpectedDeliveryInput) {
        payload.expected_delivery = expectedDelivery || null
      }
      await saveDetails.mutateAsync(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save changes')
      setIsSubmitting(false)
      return
    }
    // The PO data is saved at this point. A PDF failure is recoverable via the
    // Regenerate PDF button on the detail page, so it must not read as a
    // failed save.
    try {
      await regeneratePdf.mutateAsync()
    } catch {
      setError('Details saved, but the PO PDF could not be regenerated. '
        + 'Use "Regenerate PDF" on the PO page to retry.')
      setIsSubmitting(false)
      return
    }
    replaceTab(`/po/${id}`)
  }

  if (isLoading) return <div className="p-6 text-sm text-neutral-500">Loading…</div>
  if (!po) return <div className="p-6 text-sm text-neutral-500">PO not found.</div>

  if (!editable) {
    return (
      <div className="p-6">
        <p className="text-sm text-neutral-700">
          Only imported POs in status "issued" can be edited here.
        </p>
        <Link to={`/po/${po.id}`} className="mt-3 inline-flex items-center gap-1 text-sm text-primary-600">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to PO
        </Link>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div>
        <Link to={`/po/${po.id}`} className="inline-flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-900">
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to PO
        </Link>
        <h1 className="mt-2 text-xl font-semibold text-neutral-900">
          Edit Details — {po.number}
        </h1>
        <p className="text-sm text-neutral-500">
          Imported from NC. Vendor, currency and line quantities/prices are read-only.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {error}
        </div>
      )}

      <section className="space-y-4 rounded-lg border border-neutral-200 p-4">
        <h2 className="text-base font-semibold text-neutral-900">Order</h2>
        <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
          <div><dt className="text-xs text-neutral-500">Vendor</dt><dd className="text-neutral-900">{po.vendor_name}</dd></div>
          <div><dt className="text-xs text-neutral-500">Currency</dt><dd className="text-neutral-900">{po.currency}</dd></div>
          <div><dt className="text-xs text-neutral-500">Title</dt><dd className="text-neutral-900">{po.title}</dd></div>
          <div><dt className="text-xs text-neutral-500">Procurement Type</dt><dd className="text-neutral-900">{TYPE_LABELS[po.type] ?? `Type ${po.type}`}</dd></div>
          <div><dt className="text-xs text-neutral-500">Budget Code</dt><dd className="text-neutral-900">{po.budget_code || '—'}</dd></div>
        </dl>

        <div className="grid gap-4 md:grid-cols-2">
          <FormField label="Expected Delivery" htmlFor="expectedDelivery" hint={expectedDeliveryHint}>
            {showExpectedDeliveryInput ? (
              <Input id="expectedDelivery" type="date" value={expectedDelivery}
                     onChange={(e) => setExpectedDelivery(e.target.value)} />
            ) : (
              <p id="expectedDelivery" className="text-sm text-neutral-900">{expectedDeliveryDisplay}</p>
            )}
          </FormField>
          <FormField label="Incoterms" htmlFor="incoterms">
            <Input id="incoterms" type="text" value={incoterms} maxLength={100}
                   placeholder="e.g. FOB Shanghai"
                   onChange={(e) => setIncoterms(e.target.value)} />
          </FormField>
        </div>

        <FormField label="Delivery Address" htmlFor="deliveryAddress">
          <textarea id="deliveryAddress" rows={2} value={deliveryAddress}
                    onChange={(e) => { setDeliveryAddress(e.target.value); deliveryAddressTouched.current = true }}
                    className="w-full rounded border border-neutral-300 bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600" />
        </FormField>

        <div className="grid gap-4 md:grid-cols-2">
          <FormField label="Tax Code" htmlFor="taxCode">
            <select
              id="taxCode"
              value={taxCode ?? ''}
              disabled={po.currency !== 'CAD'}
              onChange={(e) => {
                const chosen = taxCodes.find((t) => t.code === e.target.value)
                setTaxCode(chosen?.code ?? null)
                setTaxRate(chosen ? Number(chosen.rate) : 0)
              }}
              className="h-9 w-full rounded border border-neutral-300 bg-white px-2 text-sm disabled:bg-neutral-50 disabled:text-neutral-400"
            >
              <option value="">No tax</option>
              {/* keep a saved-but-now-inactive code selectable */}
              {taxCode && !taxCodes.some((t) => t.code === taxCode) && (
                <option value={taxCode}>{taxCode} — {(effectiveTaxRate * 100).toFixed(2)}% (inactive)</option>
              )}
              {taxCodes.map((t) => (
                <option key={t.code} value={t.code}>{t.code} — {(Number(t.rate) * 100).toFixed(2)}%</option>
              ))}
            </select>
          </FormField>
          <label className="flex items-end gap-2 pb-2 text-sm text-neutral-700">
            <input type="checkbox" checked={isPrepaid}
                   onChange={(e) => setIsPrepaid(e.target.checked)} />
            Prepaid order
          </label>
        </div>
      </section>

      <section className="space-y-3 rounded-lg border border-neutral-200 p-4">
        <h2 className="text-base font-semibold text-neutral-900">Line Items</h2>
        <ImportedPoLineItems items={lines} onChange={setLines} currency={po.currency} />
        <div className="flex justify-end gap-6 text-sm">
          <span className="text-neutral-500">Subtotal <span className="ml-2 font-mono text-neutral-900">{formatAmount(subtotal, currency)}</span></span>
          <span className="text-neutral-500">Tax <span className="ml-2 font-mono text-neutral-900">{formatAmount(taxAmount, currency)}</span></span>
          <span className="font-semibold text-neutral-900">Total <span className="ml-2 font-mono">{formatAmount(total, currency)}</span></span>
        </div>
      </section>

      <section className="space-y-3 rounded-lg border border-neutral-200 p-4">
        <h2 className="text-base font-semibold text-neutral-900">Buyer Notes</h2>
        <FormField label="Buyer Notes / Terms &amp; Conditions" htmlFor="buyerNotes">
          <textarea id="buyerNotes" rows={4} value={buyerNotes}
                    onChange={(e) => setBuyerNotes(e.target.value)}
                    placeholder="Printed on the PO sent to the vendor."
                    className="w-full rounded border border-neutral-300 bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-600" />
        </FormField>
      </section>

      <div className="flex justify-end gap-2">
        <Link to={`/po/${po.id}`}>
          <Button variant="secondary" type="button">Cancel</Button>
        </Link>
        <Button type="button" onClick={handleSave} disabled={isSubmitting}>
          {isSubmitting ? 'Saving…' : 'Save & Regenerate PDF'}
        </Button>
      </div>
    </div>
  )
}
