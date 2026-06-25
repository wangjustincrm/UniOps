import { useState, useEffect } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { ArrowLeft, Upload, X, Calendar, Search, Receipt } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { PrLineItems, lineItemsTotal, validateLineItems } from '@/components/pr/PrLineItems'
import { useConfig } from '@/hooks/useConfig'
import { formatAmount, cn } from '@/lib/utils'
import type { ProcurementType, PrLineItem, Currency } from '@/types'
import { CURRENCIES } from '@/types'
import { useCreatePo } from '@/hooks/usePos'
import { poService } from '@/services/po'
import { usePr } from '@/hooks/usePrs'
import { useVendors } from '@/hooks/useVendors'
import type { ApiVendor } from '@/services/vendors'
import { useTaxCodes } from '@/hooks/useTaxCodes'

const TYPE_LABELS: Record<number, string> = {
  1: 'Type 1 — Raw Mat./Packaging',
  2: 'Type 2 — Consumables',
  3: 'Type 3 — Spare Parts',
  4: 'Type 4 — Service',
  5: 'Type 5 — Fixed Asset',
  6: 'Type 6 — Project',
}

function defaultLine(): PrLineItem {
  return {
    id: crypto.randomUUID(),
    description: '',
    materialId: '',
    supplierItemId: '',
    qty: 1,
    unit: 'pcs',
    unitPrice: 0,
    lineTotal: 0,
    notes: '',
  }
}

export default function PoCreatePage() {
  const replaceTab = useReplaceTab(epmsRoutes)
  const [searchParams] = useSearchParams()
  const prId = searchParams.get('prId')

  const createPo = useCreatePo()
  const { data: config } = useConfig()
  const { data: linkedPrData } = usePr(prId ?? '')

  // Form state
  const [title, setTitle] = useState('')
  const [selectedVendor, setSelectedVendor] = useState<ApiVendor | null>(null)
  const [vendorQuery, setVendorQuery] = useState('')
  const [vendorOpen, setVendorOpen] = useState(false)

  const { data: vendorsData } = useVendors({
    active_only: true,
    search: vendorQuery || undefined,
    page_size: 50,
  })
  const vendors = vendorsData?.items ?? []
  const [procurementType, setProcurementType] = useState<ProcurementType | null>(null)
  const [budgetCode, setBudgetCode] = useState('')
  const [currency, setCurrency] = useState<Currency>('CAD')
  const [taxRate, setTaxRate] = useState(0.13)
  const [taxCode, setTaxCode] = useState<string | null>(null)
  const taxCodes = useTaxCodes()
  const [expectedDelivery, setExpectedDelivery] = useState('')
  const [deliveryAddress, setDeliveryAddress] = useState('')
  const [notes, setNotes] = useState('')
  const [isPrepaid, setIsPrepaid] = useState(false)
  const [lineItems, setLineItems] = useState<PrLineItem[]>([defaultLine()])
  const [lineErrors, setLineErrors] = useState<Record<string, { description?: string; qty?: string; unitPrice?: string }>>({})
  const [attachments, setAttachments] = useState<{ name: string; size: string }[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [isSubmitting, setIsSubmitting] = useState(false)

  useEffect(() => {
    if (config?.delivery_address) setDeliveryAddress(config.delivery_address)
  }, [config?.delivery_address])

  // Default-select the tax code matching the default rate (e.g. 13% → HST_ON)
  // once the MDM tax codes load, so a brand-new CAD PO records a code, not just
  // a bare rate. Only runs while nothing has been chosen yet.
  useEffect(() => {
    if (currency !== 'CAD' || taxCode || taxCodes.length === 0) return
    const match = taxCodes.find((c) => Number(c.rate) === taxRate) ?? taxCodes[0]
    if (match) { setTaxCode(match.code); setTaxRate(Number(match.rate)) }
  }, [taxCodes, currency]) // eslint-disable-line react-hooks/exhaustive-deps

  // Pre-fill from linked PR
  useEffect(() => {
    if (!prId) return
    const pr = linkedPrData
    if (!pr) return
    setTitle(pr.title)
    setBudgetCode(pr.budget_code)
    setProcurementType(pr.type)
    setIsPrepaid(pr.is_prepaid ?? false)
    if (pr.required_by) setExpectedDelivery(pr.required_by)
    setDeliveryAddress(pr.delivery_address || config?.delivery_address || '')
    setCurrency(pr.currency ?? 'CAD')
    setLineItems(pr.line_items.map((li) => {
      const qty = Number(li.qty)
      const unitPrice = Number(li.unit_price)
      return {
        id: crypto.randomUUID(),
        description: li.description,
        materialId: li.material_id ?? '',
        supplierItemId: li.supplier_item_id ?? '',
        qty,
        unit: li.unit,
        unitPrice,
        lineTotal: Math.round(qty * unitPrice * 100) / 100,
        notes: li.notes ?? '',
      }
    }))
    const vendor = vendors.find((v) => v.id === pr.vendor_id)
    if (vendor) setSelectedVendor(vendor)
    else if (pr.vendor_id && pr.vendor_name) {
      // Vendor exists in DB but not yet in loaded vendor list — use real ID
      setSelectedVendor({ id: pr.vendor_id, code: '', name: pr.vendor_name })
    }
  }, [prId, linkedPrData])

  const filteredVendors = vendors

  const subtotal = lineItemsTotal(lineItems)
  // Tax only applies to CAD; all other currencies are 0%
  const effectiveTaxRate = currency === 'CAD' ? taxRate : 0
  const taxAmount = Math.round(subtotal * effectiveTaxRate * 100) / 100
  const total = subtotal + taxAmount

  const validate = (status: 'draft' | 'submitted'): boolean => {
    const e: Record<string, string> = {}
    if (!title.trim()) e.title = 'Title is required'
    if (!selectedVendor) e.vendor = 'Please select a vendor'
    if (status === 'submitted' && !expectedDelivery) e.expectedDelivery = 'Expected delivery date is required'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = async (status: 'draft' | 'submitted') => {
    if (status === 'submitted') {
      const lineErrs = validateLineItems(lineItems)
      if (Object.keys(lineErrs).length > 0) {
        setLineErrors(lineErrs)
        return
      }
    }
    if (!validate(status)) return

    setIsSubmitting(true)
    try {
      const newPo = await createPo.mutateAsync({
        title,
        type: procurementType ?? 2,
        vendor_id: selectedVendor!.id,
        currency,
        tax_rate: effectiveTaxRate,
        tax_code: effectiveTaxRate > 0 ? taxCode ?? undefined : null,
        expected_delivery: expectedDelivery || undefined,
        delivery_address: deliveryAddress || undefined,
        notes: notes || undefined,
        is_prepaid: isPrepaid,
        pr_id: prId ?? undefined,
        budget_code: budgetCode || undefined,
        line_items: lineItems.map((item) => ({
          description: item.description,
          material_id: item.materialId || undefined,
          supplier_item_id: item.supplierItemId || undefined,
          qty: item.qty,
          unit: item.unit,
          unit_price: item.unitPrice,
          notes: item.notes || undefined,
        })),
      })
      if (status === 'submitted') {
        await poService.action(newPo.id, { action: 'submit' })
      }
      replaceTab(`/po/${newPo.id}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    setAttachments((prev) => [
      ...prev,
      ...files.map((f) => ({ name: f.name, size: `${(f.size / 1024 / 1024).toFixed(1)} MB` })),
    ])
  }

  const linkedPr = linkedPrData

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <Link to="/po">
          <Button variant="ghost" size="icon-sm">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">New Purchase Order</h1>
          {linkedPr && (
            <p className="text-sm text-neutral-400 mt-0.5">
              Pre-filled from{' '}
              <Link to={`/pr/${prId}`} className="text-primary-600 hover:underline">
                {linkedPr.number}
              </Link>
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Main form */}
        <div className="lg:col-span-9 flex flex-col gap-6">

          {/* Basic Details */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-5">
            <h2 className="text-base font-semibold text-neutral-900">Order Details</h2>

            {/* Title */}
            <FormField label="PO Title / Description" required htmlFor="title" error={errors.title}>
              <Input
                id="title"
                placeholder="Brief description of this purchase order"
                value={title}
                onChange={(e) => { setTitle(e.target.value); setErrors((p) => ({ ...p, title: '' })) }}
                error={!!errors.title}
              />
            </FormField>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              {/* Vendor */}
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">
                  Vendor <span className="text-danger-600">*</span>
                </label>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                  <input
                    type="text"
                    placeholder="Search vendor by name or code…"
                    value={selectedVendor ? selectedVendor.name : vendorQuery}
                    onFocus={() => { setVendorOpen(true); if (selectedVendor) setVendorQuery('') }}
                    onChange={(e) => { setVendorQuery(e.target.value); setSelectedVendor(null); setVendorOpen(true); setErrors((p) => ({ ...p, vendor: '' })) }}
                    className={cn(
                      'h-10 w-full rounded-md border bg-white pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                      errors.vendor ? 'border-danger-600' : 'border-neutral-300'
                    )}
                  />
                  {selectedVendor && (
                    <button
                      type="button"
                      onClick={() => { setSelectedVendor(null); setVendorQuery('') }}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                  {vendorOpen && !selectedVendor && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setVendorOpen(false)} />
                      <div className="absolute z-20 mt-1 w-full rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
                        {filteredVendors.map((v) => (
                          <button
                            key={v.id}
                            type="button"
                            className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-primary-50 text-left"
                            onClick={() => { setSelectedVendor(v); setVendorOpen(false); setVendorQuery(''); setErrors((p) => ({ ...p, vendor: '' })) }}
                          >
                            <span className="font-mono text-xs rounded bg-neutral-100 px-1.5 py-0.5 text-neutral-600">{v.code}</span>
                            {v.name}
                          </button>
                        ))}
                        {filteredVendors.length === 0 && (
                          <p className="px-3 py-2 text-sm text-neutral-400">No vendors found</p>
                        )}
                      </div>
                    </>
                  )}
                </div>
                {selectedVendor && <p className="text-xs text-success-600">✓ {selectedVendor.name} ({selectedVendor.code})</p>}
                {errors.vendor && <p className="text-xs text-danger-600">{errors.vendor}</p>}
              </div>

              {/* Procurement Type */}
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Procurement Type</label>
                <select
                  value={procurementType ?? ''}
                  onChange={(e) => setProcurementType(e.target.value ? Number(e.target.value) as ProcurementType : null)}
                  className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                >
                  <option value="">Select type…</option>
                  {[1, 2, 3, 4, 5, 6].map((t) => (
                    <option key={t} value={t}>{TYPE_LABELS[t]}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              {/* Budget Code */}
              <FormField label="Budget Code" htmlFor="budgetCode">
                <Input
                  id="budgetCode"
                  placeholder="e.g. CRM003-01"
                  value={budgetCode}
                  onChange={(e) => setBudgetCode(e.target.value)}
                />
              </FormField>

              {/* Expected Delivery */}
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">
                  Expected Delivery Date <span className="text-danger-600">*</span>
                </label>
                <div className="relative">
                  <Calendar className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400 pointer-events-none" />
                  <input
                    type="date"
                    value={expectedDelivery}
                    onChange={(e) => { setExpectedDelivery(e.target.value); setErrors((p) => ({ ...p, expectedDelivery: '' })) }}
                    className={cn(
                      'h-10 w-full rounded-md border bg-white pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                      errors.expectedDelivery ? 'border-danger-600' : 'border-neutral-300'
                    )}
                  />
                </div>
                {errors.expectedDelivery && <p className="text-xs text-danger-600">{errors.expectedDelivery}</p>}
              </div>
            </div>

            {/* Delivery Address */}
            <FormField
              label="Delivery Address / Location"
              htmlFor="deliveryAddress"
              hint={config?.delivery_address ? `Default: ${config.delivery_address}` : 'Leave blank to use company default'}
            >
              <Input
                id="deliveryAddress"
                placeholder={config?.delivery_address || 'e.g., Technical Warehouse, Building A'}
                value={deliveryAddress}
                onChange={(e) => setDeliveryAddress(e.target.value)}
              />
            </FormField>
          </div>

          {/* Line Items */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
            <PrLineItems
              procurementType={procurementType}
              items={lineItems}
              onChange={setLineItems}
              errors={lineErrors}
            />
          </div>

          {/* Currency, Tax & Total */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-4">
            <div className="flex items-center gap-2">
              <Receipt className="h-4 w-4 text-neutral-400" />
              <h2 className="text-base font-semibold text-neutral-900">Currency, Tax &amp; Total</h2>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {/* Currency */}
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Currency <span className="text-danger-600">*</span></label>
                <select
                  value={currency}
                  onChange={(e) => {
                    const c = e.target.value as Currency
                    setCurrency(c)
                    if (c !== 'CAD') { setTaxRate(0); setTaxCode(null) }
                    else setTaxRate(0.13)
                  }}
                  className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                >
                  {[
                    ...CURRENCIES.filter((c) => (config?.enabled_currencies ?? ['CAD','USD','EUR','RMB']).includes(c.value)),
                  ].map((c) => (
                    <option key={c.value} value={c.value}>{c.value} — {c.label}</option>
                  ))}
                </select>
              </div>

              {/* Tax — from Finance Tax Settings (mdm-api), only enabled for CAD */}
              <div className="flex flex-col gap-1.5">
                <label className={cn('text-sm font-medium', currency === 'CAD' ? 'text-neutral-700' : 'text-neutral-400')}>Tax</label>
                {currency === 'CAD' ? (
                  <select
                    value={taxCode ?? ''}
                    onChange={(e) => {
                      const c = taxCodes.find((tc) => tc.code === e.target.value)
                      setTaxCode(c?.code ?? null)
                      setTaxRate(c ? Number(c.rate) : 0)
                    }}
                    className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                  >
                    {taxCodes.length === 0 && <option value="">Loading tax codes…</option>}
                    {taxCodes.map((c) => (
                      <option key={c.code} value={c.code}>
                        {+(Number(c.rate) * 100).toFixed(3)}% — {c.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <div className="flex h-10 items-center rounded-md border border-neutral-200 bg-neutral-100 px-3 text-sm text-neutral-400">
                    0% — Not applicable for {currency}
                  </div>
                )}
              </div>
            </div>

            <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-5 py-4 flex flex-col gap-2 max-w-xs ml-auto w-full">
              <div className="flex justify-between text-sm text-neutral-700">
                <span>Subtotal</span>
                <span className="amount">{formatAmount(subtotal, currency)}</span>
              </div>
              <div className="flex justify-between text-sm text-neutral-700">
                <span>Tax ({Math.round(effectiveTaxRate * 100)}%)</span>
                <span className="amount">{formatAmount(taxAmount, currency)}</span>
              </div>
              <div className="border-t border-neutral-200 mt-1 pt-2 flex justify-between">
                <span className="text-sm font-bold text-neutral-900">Total ({currency})</span>
                <span className={cn('amount text-base font-bold', total > 0 ? 'text-neutral-900' : 'text-neutral-400')}>
                  {formatAmount(total, currency)}
                </span>
              </div>
            </div>
          </div>

          {/* Terms & Notes */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-4">
            <h2 className="text-base font-semibold text-neutral-900">Terms &amp; Notes</h2>
            <FormField label="Buyer Notes / Terms &amp; Conditions" htmlFor="notes">
              <textarea
                id="notes"
                rows={3}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Payment terms, delivery instructions, special conditions…"
                className="w-full resize-none rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
              />
            </FormField>

            {/* Prepayment Required */}
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="prepayment-po"
                checked={isPrepaid}
                onChange={(e) => setIsPrepaid(e.target.checked)}
                className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-600"
              />
              <label htmlFor="prepayment-po" className="text-sm text-neutral-700 cursor-pointer">
                Prepayment Required
              </label>
            </div>

            {/* Attachments */}
            <div className="flex flex-col gap-2">
              <label className="text-sm font-medium text-neutral-700">Supporting Documents</label>
              <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-neutral-300 bg-neutral-50 py-6 text-center transition-colors hover:border-primary-400 hover:bg-primary-50">
                <Upload className="h-6 w-6 text-neutral-400" />
                <span className="text-sm text-neutral-500">Drag &amp; drop or click to upload</span>
                <span className="text-xs text-neutral-400">Any format · Max 25 MB per file</span>
                <input type="file" multiple className="hidden" onChange={handleFileInput} />
              </label>
              {attachments.length > 0 && (
                <ul className="flex flex-col gap-1.5">
                  {attachments.map((f, i) => (
                    <li key={i} className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm">
                      <span className="flex items-center gap-2 text-neutral-700">
                        <Upload className="h-3.5 w-3.5 text-neutral-400" />
                        {f.name}
                        <span className="text-neutral-400">({f.size})</span>
                      </span>
                      <button
                        type="button"
                        onClick={() => setAttachments((a) => a.filter((_, j) => j !== i))}
                        className="text-neutral-400 hover:text-danger-500"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {/* Footer actions */}
          <div className="flex items-center justify-between rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] px-6 py-4">
            <Link to="/po">
              <Button variant="ghost">Cancel</Button>
            </Link>
            <div className="flex items-center gap-3">
              <Button
                variant="secondary"
                onClick={() => handleSubmit('draft')}
                disabled={isSubmitting}
              >
                Save as Draft
              </Button>
              <Button
                onClick={() => handleSubmit('submitted')}
                disabled={isSubmitting}
              >
                {isSubmitting ? 'Submitting…' : 'Submit for Approval'}
              </Button>
            </div>
          </div>
        </div>

        {/* Sidebar — Tips */}
        <div className="lg:col-span-3 flex flex-col gap-4">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5 sticky top-6">
            <h3 className="text-sm font-semibold text-neutral-700 mb-3">💡 Tips</h3>
            <ul className="flex flex-col gap-2.5 text-xs text-neutral-500 leading-relaxed">
              <li>• Tax rate defaults to 13% HST. Adjust for your province or for tax-exempt purchases.</li>
              <li>• Line items are pre-filled from the PR when linked — adjust quantities or prices if negotiated differently.</li>
              <li>• Delivery address defaults from the PR. Leave blank if the vendor will confirm separately.</li>
              <li>• Attach vendor quotes, SOWs, or contracts before submitting for approval.</li>
              <li>• After approval, use <strong>Issue PO</strong> to send the official order to the vendor.</li>
            </ul>

            {linkedPr && (
              <div className="mt-4 border-t border-neutral-100 pt-4">
                <p className="text-xs font-medium text-neutral-500 mb-2">Linked PR</p>
                <Link
                  to={`/pr/${prId}`}
                  className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-primary-700 hover:bg-primary-50 transition-colors"
                >
                  <span className="font-mono">{linkedPr.number}</span>
                  <span className="text-neutral-400">→</span>
                  <span className="truncate">{linkedPr.title}</span>
                </Link>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
