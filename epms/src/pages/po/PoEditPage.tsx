import { useState, useEffect } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { ArrowLeft, Upload, X, Calendar, Search, Receipt } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { PrLineItems, lineItemsTotal, validateLineItems } from '@/components/pr/PrLineItems'
import { useConfig } from '@/hooks/useConfig'
import { formatAmount, cn } from '@/lib/utils'
import type { ProcurementType, PrLineItem, Currency } from '@/types'
import { CURRENCIES } from '@/types'
import { usePo, useUpdatePo, usePoAction } from '@/hooks/usePos'
import { poService } from '@/services/po'
import { useVendors } from '@/hooks/useVendors'
import { useTaxCodes } from '@/hooks/useTaxCodes'
import type { ApiVendor } from '@/services/vendors'

const TYPE_LABELS: Record<number, string> = {
  1: 'Type 1 — Raw Mat./Packaging',
  2: 'Type 2 — Consumables',
  3: 'Type 3 — Spare Parts',
  4: 'Type 4 — Service',
  5: 'Type 5 — Fixed Asset',
  6: 'Type 6 — Project',
}

export default function PoEditPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const { data: po, isLoading } = usePo(id ?? '')
  const { data: config } = useConfig()
  const updatePo = useUpdatePo()
  const poAction = usePoAction(id ?? '')

  const [title, setTitle] = useState('')
  const [selectedVendor, setSelectedVendor] = useState<ApiVendor | null>(null)
  const [vendorQuery, setVendorQuery] = useState('')
  const [vendorOpen, setVendorOpen] = useState(false)
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
  const [lineItems, setLineItems] = useState<PrLineItem[]>([])
  const [lineErrors, setLineErrors] = useState<Record<string, { description?: string; qty?: string; unitPrice?: string }>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [isSubmitting, setIsSubmitting] = useState(false)

  const { data: vendorsData } = useVendors({
    active_only: true,
    search: vendorQuery || undefined,
    page_size: 50,
  })
  const vendors = vendorsData?.items ?? []

  // Pre-fill from existing PO
  useEffect(() => {
    if (!po) return
    setTitle(po.title)
    setProcurementType(po.type as ProcurementType)
    setBudgetCode(po.budget_code ?? '')
    setCurrency(po.currency as Currency)
    setTaxRate(Number(po.tax_rate))
    setTaxCode(po.tax_code ?? null)
    setExpectedDelivery(po.expected_delivery ?? '')
    setDeliveryAddress(po.delivery_address ?? '')
    setNotes(po.notes ?? '')
    setIsPrepaid(po.is_prepaid ?? false)
    setLineItems(po.line_items.map((li) => ({
      id: crypto.randomUUID(),
      description: li.description,
      materialId: li.material_id ?? '',
      supplierItemId: li.supplier_item_id ?? '',
      qty: Number(li.qty),
      unit: li.unit,
      unitPrice: Number(li.unit_price),
      lineTotal: Number(li.line_total),
      notes: li.notes ?? '',
    })))

    const vendor = vendors.find((v) => v.id === po.vendor_id)
    if (vendor) {
      setSelectedVendor(vendor)
    } else {
      setSelectedVendor({ id: po.vendor_id, code: '', name: po.vendor_name })
    }
  }, [po?.id])

  const subtotal = lineItemsTotal(lineItems)
  const effectiveTaxRate = currency === 'CAD' ? taxRate : 0
  const taxAmount = Math.round(subtotal * effectiveTaxRate * 100) / 100
  const total = subtotal + taxAmount

  const validate = (mode: 'draft' | 'submitted'): boolean => {
    const e: Record<string, string> = {}
    if (!title.trim()) e.title = 'Title is required'
    if (!selectedVendor) e.vendor = 'Please select a vendor'
    if (mode === 'submitted' && !expectedDelivery) e.expectedDelivery = 'Expected delivery date is required'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSave = async (andSubmit: boolean) => {
    const mode = andSubmit ? 'submitted' : 'draft'
    if (andSubmit) {
      const lineErrs = validateLineItems(lineItems)
      if (Object.keys(lineErrs).length > 0) { setLineErrors(lineErrs); return }
    }
    if (!validate(mode)) return

    setIsSubmitting(true)
    try {
      await updatePo.mutateAsync({
        id: id!,
        body: {
          title,
          type: procurementType ?? 2,
          vendor_id: selectedVendor!.id,
          currency,
          tax_rate: effectiveTaxRate,
          tax_code: effectiveTaxRate > 0 ? taxCode ?? undefined : null,
          budget_code: budgetCode || undefined,
          expected_delivery: expectedDelivery || undefined,
          delivery_address: deliveryAddress || undefined,
          notes: notes || undefined,
          is_prepaid: isPrepaid,
          line_items: lineItems.map((item) => ({
            description: item.description,
            material_id: item.materialId || undefined,
            supplier_item_id: item.supplierItemId || undefined,
            qty: item.qty,
            unit: item.unit,
            unit_price: item.unitPrice,
            notes: item.notes || undefined,
          })),
        },
      })
      if (andSubmit) {
        await poService.action(id!, { action: 'submit' })
      }
      navigate(`/po/${id}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  if (isLoading) {
    return <div className="p-8 text-sm text-neutral-400">Loading…</div>
  }

  if (!po || !['draft', 'returned'].includes(po.status)) {
    return (
      <div className="p-8">
        <p className="text-sm text-neutral-500">This PO cannot be edited in its current status.</p>
        <Link to={`/po/${id}`} className="mt-3 inline-block text-sm text-primary-600 hover:underline">← Back to PO</Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link to={`/po/${id}`}>
          <Button variant="ghost" size="icon-sm"><ArrowLeft className="h-4 w-4" /></Button>
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Edit Purchase Order</h1>
          <p className="text-sm text-neutral-400 mt-0.5">{po.number}</p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="lg:col-span-9 flex flex-col gap-6">

          {/* Order Details */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-5">
            <h2 className="text-base font-semibold text-neutral-900">Order Details</h2>

            <FormField label="PO Title / Description" required htmlFor="title" error={errors.title}>
              <Input
                id="title"
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
                    placeholder="Search vendor…"
                    value={selectedVendor ? selectedVendor.name : vendorQuery}
                    onFocus={() => { setVendorOpen(true); if (selectedVendor) setVendorQuery('') }}
                    onChange={(e) => { setVendorQuery(e.target.value); setSelectedVendor(null); setVendorOpen(true); setErrors((p) => ({ ...p, vendor: '' })) }}
                    className={cn(
                      'h-10 w-full rounded-md border bg-white pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                      errors.vendor ? 'border-danger-600' : 'border-neutral-300'
                    )}
                  />
                  {selectedVendor && (
                    <button type="button" onClick={() => { setSelectedVendor(null); setVendorQuery('') }}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                  {vendorOpen && !selectedVendor && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setVendorOpen(false)} />
                      <div className="absolute z-20 mt-1 w-full rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
                        {vendors.map((v) => (
                          <button key={v.id} type="button"
                            className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-primary-50 text-left"
                            onClick={() => { setSelectedVendor(v); setVendorOpen(false); setVendorQuery(''); setErrors((p) => ({ ...p, vendor: '' })) }}>
                            <span className="font-mono text-xs rounded bg-neutral-100 px-1.5 py-0.5 text-neutral-600">{v.code}</span>
                            {v.name}
                          </button>
                        ))}
                        {vendors.length === 0 && <p className="px-3 py-2 text-sm text-neutral-400">No vendors found</p>}
                      </div>
                    </>
                  )}
                </div>
                {selectedVendor && <p className="text-xs text-success-600">✓ {selectedVendor.name}{selectedVendor.code ? ` (${selectedVendor.code})` : ''}</p>}
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
              <FormField label="Budget Code" htmlFor="budgetCode">
                <Input id="budgetCode" value={budgetCode} onChange={(e) => setBudgetCode(e.target.value)} />
              </FormField>

              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Expected Delivery Date</label>
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

            <FormField
              label="Delivery Address / Location"
              htmlFor="deliveryAddress"
              hint={config?.delivery_address ? `Default: ${config.delivery_address}` : undefined}
            >
              <Input id="deliveryAddress" value={deliveryAddress} onChange={(e) => setDeliveryAddress(e.target.value)}
                placeholder={config?.delivery_address || 'e.g., Technical Warehouse, Building A'} />
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
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700">Currency</label>
                <select value={currency}
                  onChange={(e) => { const c = e.target.value as Currency; setCurrency(c); if (c !== 'CAD') { setTaxRate(0); setTaxCode(null) } else setTaxRate(0.13) }}
                  className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600">
                  {CURRENCIES.filter((c) => (config?.enabled_currencies ?? ['CAD','USD','EUR','RMB']).includes(c.value)).map((c) => (
                    <option key={c.value} value={c.value}>{c.value} — {c.label}</option>
                  ))}
                </select>
              </div>
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
                    className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600">
                    {/* keep a saved-but-now-inactive code selectable */}
                    {taxCode && !taxCodes.some((c) => c.code === taxCode) && (
                      <option value={taxCode}>{+(effectiveTaxRate * 100).toFixed(3)}% — {taxCode} (inactive)</option>
                    )}
                    {taxCodes.length === 0 && !taxCode && <option value="">Loading tax codes…</option>}
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
                <span>Subtotal</span><span>{formatAmount(subtotal, currency)}</span>
              </div>
              <div className="flex justify-between text-sm text-neutral-700">
                <span>Tax ({Math.round(effectiveTaxRate * 100)}%)</span><span>{formatAmount(taxAmount, currency)}</span>
              </div>
              <div className="border-t border-neutral-200 mt-1 pt-2 flex justify-between">
                <span className="text-sm font-bold text-neutral-900">Total ({currency})</span>
                <span className={cn('text-base font-bold', total > 0 ? 'text-neutral-900' : 'text-neutral-400')}>
                  {formatAmount(total, currency)}
                </span>
              </div>
            </div>
          </div>

          {/* Notes */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-4">
            <h2 className="text-base font-semibold text-neutral-900">Notes</h2>
            <FormField label="Buyer Notes / Terms &amp; Conditions" htmlFor="notes">
              <textarea id="notes" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)}
                placeholder="Payment terms, delivery instructions, special conditions…"
                className="w-full resize-none rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
            </FormField>

            {/* Prepayment Required */}
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="prepayment-po-edit"
                checked={isPrepaid}
                onChange={(e) => setIsPrepaid(e.target.checked)}
                className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-600"
              />
              <label htmlFor="prepayment-po-edit" className="text-sm text-neutral-700 cursor-pointer">
                Prepayment Required
              </label>
            </div>
          </div>

          {/* Footer actions */}
          <div className="flex items-center justify-between rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] px-6 py-4">
            <Link to={`/po/${id}`}>
              <Button variant="ghost">Cancel</Button>
            </Link>
            <div className="flex items-center gap-3">
              <Button variant="secondary" onClick={() => handleSave(false)} disabled={isSubmitting}>
                Save Draft
              </Button>
              <Button onClick={() => handleSave(true)} disabled={isSubmitting}>
                {isSubmitting ? 'Submitting…' : 'Save & Submit'}
              </Button>
            </div>
          </div>
        </div>

        {/* Sidebar */}
        <div className="lg:col-span-3">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-5 sticky top-6">
            <h3 className="text-sm font-semibold text-neutral-700 mb-3">PO Info</h3>
            <dl className="flex flex-col gap-2 text-xs">
              <div className="flex justify-between">
                <dt className="text-neutral-500">Number</dt>
                <dd className="font-mono text-neutral-700">{po.number}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-neutral-500">Status</dt>
                <dd className="capitalize text-neutral-700">{po.status}</dd>
              </div>
              {po.pr_number && (
                <div className="flex justify-between">
                  <dt className="text-neutral-500">Linked PR</dt>
                  <dd>
                    <Link to={`/pr/${po.pr_id}`} className="text-primary-600 hover:underline font-mono">{po.pr_number}</Link>
                  </dd>
                </div>
              )}
            </dl>
          </div>
        </div>
      </div>
    </div>
  )
}
