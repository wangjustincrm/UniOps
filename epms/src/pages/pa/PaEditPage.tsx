import { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, AlertTriangle, CreditCard, Info, Package, FileText, CircleDot } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { usePa, useUpdatePa } from '@/hooks/usePas'
import { paService } from '@/services/pa'
import { usePo } from '@/hooks/usePos'
import { useInvoices } from '@/hooks/useInvoices'
import { useGrs } from '@/hooks/useGrs'

export default function PaEditPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: pa, isLoading } = usePa(id ?? '')
  const updatePa = useUpdatePa()

  // ── Step 1 — PO is fixed, just fetch it ───────────────────────────────────
  const { data: po } = usePo(pa?.po_id ?? '')

  // ── Step 2 — Link invoices / GRs + type ───────────────────────────────────
  const [selectedInvoiceIds, setSelectedInvoiceIds] = useState<Set<string>>(new Set())
  const [selectedLineIds, setSelectedLineIds]       = useState<Set<string>>(new Set())
  const [selectedGrIds, setSelectedGrIds]           = useState<Set<string>>(new Set())
  const [paType, setPaType]                         = useState<'regular' | 'prepayment'>('regular')
  const [prepaymentPct, setPrepaymentPct]           = useState('50')
  const [expectedSettlement, setExpectedSettlement] = useState('')

  // ── Step 3 — Charge breakdown & details ───────────────────────────────────
  const [title, setTitle]               = useState('')
  const [subtotal, setSubtotal]         = useState('')
  const [taxAmount, setTaxAmount]       = useState('')
  const [taxManuallyEdited, setTaxManuallyEdited] = useState(false)
  const [shippingAmount, setShipping]   = useState('')
  const [otherCharges, setOther]        = useState('')
  const [otherChargesNote, setOtherNote]= useState('')
  const [notes, setNotes]               = useState('')
  const [submitted, setSubmitted]       = useState(false)
  const [initialized, setInitialized]   = useState(false)

  const { data: invoicesData } = useInvoices(pa?.po_id ? { po_id: pa.po_id } : undefined)
  const { data: grsData }      = useGrs(pa?.po_id ? { po_id: pa.po_id } : undefined)
  const poInvoices = invoicesData?.items ?? []
  const poGrs      = (grsData?.items ?? []).filter((g) => g.status !== 'cancelled')

  // Pre-fill form once PA data loads
  useEffect(() => {
    if (!pa || initialized) return
    setSelectedInvoiceIds(new Set(pa.invoice_ids))
    setSelectedLineIds(new Set(pa.line_items.map((l) => l.po_line_id)))
    setPaType(pa.pa_type)
    setPrepaymentPct(pa.prepayment_pct?.toString() ?? '50')
    setExpectedSettlement(pa.expected_settlement_date ?? '')
    setTitle(pa.title)
    setSubtotal(pa.subtotal.toString())
    setTaxAmount(pa.tax_amount.toString())
    setTaxManuallyEdited(true) // keep user's existing tax value
    setNotes(pa.notes ?? '')
    setInitialized(true)
  }, [pa, initialized])

  // ── Derived ────────────────────────────────────────────────────────────────
  const selectedPoLines = po ? po.line_items.filter((l) => selectedLineIds.has(l.id)) : []
  const autoSubtotal    = selectedPoLines.reduce((s, l) => s + l.line_total, 0)

  const receivedQtyByPoLineId: Record<string, number> = {}
  for (const gr of poGrs) {
    for (const line of gr.line_items) {
      receivedQtyByPoLineId[line.po_line_id] = (receivedQtyByPoLineId[line.po_line_id] ?? 0) + line.qty_received
    }
  }

  const subtotalNum  = parseFloat(subtotal)  || 0
  const taxNum       = parseFloat(taxAmount) || 0
  const shippingNum  = parseFloat(shippingAmount) || 0
  const otherNum     = parseFloat(otherCharges) || 0
  const paymentTotal = subtotalNum + taxNum + shippingNum + otherNum

  // Tax follows the linked PO's snapshot (from Finance Tax Settings); fall back
  // to the PA's own saved code, then 13% for legacy records.
  const paTaxRate = po ? Number(po.tax_rate) : 0.13
  const paTaxCode = po?.tax_code ?? pa?.tax_code ?? null

  // Auto-fill tax at the PO's rate when not manually edited
  useEffect(() => {
    if (taxManuallyEdited) return
    if (po?.currency === 'CAD' && subtotalNum > 0) {
      setTaxAmount((subtotalNum * paTaxRate).toFixed(2))
    }
  }, [subtotalNum, po?.currency, paTaxRate, taxManuallyEdited])  // eslint-disable-line react-hooks/exhaustive-deps

  // Validation
  const errors: string[] = []
  if (submitted) {
    if (!title.trim())   errors.push('PA Title is required')
    if (subtotalNum <= 0) errors.push('Pre-tax amount must be greater than zero')
    if (taxNum < 0)      errors.push('Tax amount cannot be negative')
    if (paType === 'prepayment' && (!prepaymentPct || parseFloat(prepaymentPct) <= 0 || parseFloat(prepaymentPct) > 100))
      errors.push('Prepayment percentage must be between 1 and 100')
    if (paType === 'prepayment' && !expectedSettlement)
      errors.push('Expected settlement date is required for prepayment')
    if (paymentTotal <= 0)
      errors.push('Total payment amount must be greater than zero')
  }

  const toggleInvoice = (invId: string) => {
    setSelectedInvoiceIds((prev) => { const n = new Set(prev); if (n.has(invId)) n.delete(invId); else n.add(invId); return n })
  }
  const toggleGr = (grId: string) => {
    setSelectedGrIds((prev) => { const n = new Set(prev); if (n.has(grId)) n.delete(grId); else n.add(grId); return n })
  }
  const toggleLine = (lineId: string) => {
    setSelectedLineIds((prev) => { const n = new Set(prev); if (n.has(lineId)) n.delete(lineId); else n.add(lineId); return n })
  }

  const handleSave = async (andSubmit: boolean) => {
    setSubmitted(true)
    if (!pa || !po) return
    if (!title.trim() || subtotalNum <= 0 || taxNum < 0 || paymentTotal <= 0) return
    if (paType === 'prepayment' && (!prepaymentPct || !expectedSettlement)) return

    try {
      const paLineItems = po.line_items
        .filter((l) => selectedLineIds.has(l.id))
        .map((l) => ({
          po_line_id: l.id,
          description: l.description,
          qty: l.qty,
          unit: l.unit,
          unit_price: l.unit_price,
          line_total: l.line_total,
        }))

      await updatePa.mutateAsync({
        id: pa.id,
        body: {
          title: title.trim(),
          pa_type: paType,
          currency: po.currency,
          subtotal: subtotalNum,
          tax_amount: taxNum,
          tax_code: taxNum > 0 ? paTaxCode ?? undefined : null,
          tax_rate: taxNum > 0 ? paTaxRate : null,
          invoice_ids: Array.from(selectedInvoiceIds),
          prepayment_pct: paType === 'prepayment' ? parseFloat(prepaymentPct) : undefined,
          expected_settlement_date: paType === 'prepayment' ? expectedSettlement : undefined,
          line_items: paLineItems,
          notes: notes.trim() || undefined,
        },
      })

      if (andSubmit) {
        await paService.action(pa.id, { action: 'submit' })
        await queryClient.invalidateQueries({ queryKey: ['pas'] })
        navigate('/pa')
      } else {
        navigate(`/pa/${pa.id}`)
      }
    } catch {
      // error handled by mutation
    }
  }

  if (isLoading || !initialized) {
    return (
      <div className="flex items-center justify-center py-24">
        <p className="text-neutral-400 text-sm">Loading…</p>
      </div>
    )
  }

  if (!pa || !['draft', 'returned'].includes(pa.status)) {
    return (
      <div className="flex flex-col items-center justify-center py-24">
        <p className="text-neutral-500">This PA cannot be edited.</p>
        <Button variant="secondary" className="mt-4" onClick={() => navigate(`/pa/${id}`)}>Back</Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button onClick={() => navigate(`/pa/${id}`)} className="p-2 rounded-lg hover:bg-neutral-100 text-neutral-500">
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Edit Payment Application</h1>
          <p className="text-sm text-neutral-500 mt-0.5">
            {pa.pa_number} · {pa.status === 'returned' ? 'Returned — revise and resubmit' : 'Draft'}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Main form */}
        <div className="col-span-2 flex flex-col gap-5">

          {/* ── PO (locked) ────────────────────────────────────────────────── */}
          <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-neutral-800 flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-600 text-white text-[10px] font-bold">1</span>
              Purchase Order
            </h2>
            <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3 flex items-center justify-between">
              <div>
                <p className="font-mono text-xs font-semibold text-primary-700">{pa.po_number}</p>
                <p className="text-xs text-neutral-500 mt-0.5">{po?.vendor_name ?? pa.vendor_name} · {po?.title}</p>
              </div>
              <span className="text-xs text-neutral-400 italic">Locked</span>
            </div>
          </div>

          {/* ── Step 2 — Link Invoices / GRs + PA Type ─────────────────────── */}
          <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-5">
            <h2 className="text-sm font-semibold text-neutral-800 flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-600 text-white text-[10px] font-bold">2</span>
              Link Invoices & Goods Receipts
            </h2>

            {/* Invoices multi-select */}
            <div className="flex flex-col gap-2">
              <p className="text-xs font-medium text-neutral-600 flex items-center gap-1.5">
                <FileText className="h-3.5 w-3.5" /> Invoices for this PO
                <span className="text-neutral-400 font-normal">(select all that apply)</span>
              </p>
              {poInvoices.length === 0 ? (
                <p className="text-xs text-neutral-400 italic px-3 py-2 border border-neutral-200 rounded-lg bg-neutral-50">
                  No invoices found for this PO yet
                </p>
              ) : (
                <div className="rounded-lg border border-neutral-200 divide-y divide-neutral-100">
                  {poInvoices.map((inv) => (
                    <label
                      key={inv.id}
                      className={cn(
                        'flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-primary-50 transition-colors',
                        selectedInvoiceIds.has(inv.id) && 'bg-primary-50'
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={selectedInvoiceIds.has(inv.id)}
                        onChange={() => toggleInvoice(inv.id)}
                        className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-600"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-xs font-semibold text-primary-700">{inv.internal_ref}</span>
                          <span className="font-mono text-xs font-semibold text-neutral-900">{formatAmount(inv.total_amount, inv.currency)}</span>
                        </div>
                        <div className="text-xs text-neutral-500 mt-0.5">
                          #{inv.vendor_invoice_number} · {formatDate(inv.invoice_date)} · <span className="capitalize">{inv.status}</span>
                        </div>
                        <div className="flex items-center gap-3 mt-1 text-[11px] text-neutral-400">
                          <span>Pre-tax <span className="font-mono text-neutral-600">{formatAmount(inv.amount, inv.currency)}</span></span>
                          <span className="text-neutral-200">·</span>
                          <span>Tax <span className="font-mono text-neutral-600">{formatAmount(inv.tax_amount, inv.currency)}</span></span>
                        </div>
                      </div>
                    </label>
                  ))}
                </div>
              )}
            </div>

            {/* GRs multi-select */}
            <div className="flex flex-col gap-2">
              <p className="text-xs font-medium text-neutral-600 flex items-center gap-1.5">
                <Package className="h-3.5 w-3.5" /> Goods / Service Receipts for this PO
                <span className="text-neutral-400 font-normal">(select all that apply)</span>
              </p>
              {poGrs.length === 0 ? (
                <p className="text-xs text-neutral-400 italic px-3 py-2 border border-neutral-200 rounded-lg bg-neutral-50">
                  No goods receipts found for this PO yet
                </p>
              ) : (
                <div className="rounded-lg border border-neutral-200 divide-y divide-neutral-100">
                  {poGrs.map((gr) => (
                    <label
                      key={gr.id}
                      className={cn(
                        'flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-primary-50 transition-colors',
                        selectedGrIds.has(gr.id) && 'bg-primary-50'
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={selectedGrIds.has(gr.id)}
                        onChange={() => toggleGr(gr.id)}
                        className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-600"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-xs font-semibold text-primary-700">{gr.number}</span>
                          <span className="text-xs capitalize text-neutral-600">{gr.status.replace(/_/g, ' ')}</span>
                        </div>
                        <div className="text-xs text-neutral-500 mt-0.5">
                          {gr.gr_type === 'physical' ? 'Physical' : 'Service'} · Received {formatDate(gr.received_at)}
                        </div>
                      </div>
                    </label>
                  ))}
                </div>
              )}
            </div>

            {/* PO Line Items */}
            {po && (
              <div className="flex flex-col gap-2">
                <p className="text-xs font-medium text-neutral-600 flex items-center gap-1.5">
                  <CircleDot className="h-3.5 w-3.5" /> PO Line Items
                  <span className="text-neutral-400 font-normal">(select items covered by this PA)</span>
                </p>
                <div className="rounded-lg border border-neutral-200 overflow-hidden">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-neutral-200 bg-neutral-50">
                        <th className="w-8 px-3 py-2"></th>
                        <th className="px-3 py-2 text-left font-semibold text-neutral-500 uppercase tracking-wide">Description</th>
                        <th className="px-3 py-2 text-right font-semibold text-neutral-500 uppercase tracking-wide w-20">Ordered</th>
                        <th className="px-3 py-2 text-right font-semibold text-neutral-500 uppercase tracking-wide w-20">Received</th>
                        <th className="px-3 py-2 text-right font-semibold text-neutral-500 uppercase tracking-wide w-28">Line Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {po.line_items.map((line, idx) => {
                        const receivedQty = receivedQtyByPoLineId[line.id] ?? 0
                        const isReceived  = receivedQty > 0
                        const isSelected  = selectedLineIds.has(line.id)
                        return (
                          <tr
                            key={line.id}
                            className={cn(
                              'border-b border-neutral-100 last:border-0 cursor-pointer hover:bg-primary-50 transition-colors',
                              isSelected && 'bg-primary-50',
                              idx % 2 === 1 && !isSelected && 'bg-neutral-50/50'
                            )}
                            onClick={() => toggleLine(line.id)}
                          >
                            <td className="px-3 py-2.5 text-center">
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleLine(line.id)}
                                onClick={(e) => e.stopPropagation()}
                                className="h-3.5 w-3.5 rounded border-neutral-300 text-primary-600 focus:ring-primary-600"
                              />
                            </td>
                            <td className="px-3 py-2.5">
                              <div className="text-neutral-800 font-medium">{line.description}</div>
                              {line.material_id && <div className="text-neutral-400 font-mono mt-0.5">{line.material_id}</div>}
                              <div className={cn(
                                'inline-flex items-center gap-1 mt-0.5 text-[10px] font-medium',
                                isReceived ? 'text-success-600' : 'text-neutral-400'
                              )}>
                                <span className={cn('h-1.5 w-1.5 rounded-full', isReceived ? 'bg-success-500' : 'bg-neutral-300')} />
                                {isReceived ? `Received (${receivedQty} ${line.unit})` : 'Not yet received'}
                              </div>
                            </td>
                            <td className="px-3 py-2.5 text-right font-mono text-neutral-500">{line.qty} {line.unit}</td>
                            <td className="px-3 py-2.5 text-right font-mono font-medium text-neutral-700">
                              {receivedQty > 0 ? `${receivedQty} ${line.unit}` : '—'}
                            </td>
                            <td className="px-3 py-2.5 text-right font-mono font-semibold text-neutral-900">
                              {formatAmount(line.line_total, po.currency)}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                  {selectedLineIds.size > 0 && (
                    <div className="flex items-center justify-between px-4 py-2 border-t border-neutral-200 bg-primary-50">
                      <span className="text-xs text-primary-700 font-medium">{selectedLineIds.size} line{selectedLineIds.size !== 1 ? 's' : ''} selected</span>
                      <span className="text-xs font-mono font-semibold text-primary-700">
                        {formatAmount(autoSubtotal, po.currency)}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* PA Type */}
            <div className="flex flex-col gap-2">
              <p className="text-xs font-medium text-neutral-600">Payment Type</p>
              <div className="grid grid-cols-2 gap-3">
                {[
                  { value: 'regular' as const,    label: 'Regular Payment',  desc: 'Standard payment against invoice / GR' },
                  { value: 'prepayment' as const, label: 'Prepayment',       desc: 'Advance payment before full GR/invoice' },
                ].map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setPaType(opt.value)}
                    className={cn(
                      'rounded-xl border-2 p-3 text-left transition-all',
                      paType === opt.value ? 'border-primary-600 bg-primary-50' : 'border-neutral-200 hover:border-neutral-300'
                    )}
                  >
                    <p className={cn('text-sm font-semibold', paType === opt.value ? 'text-primary-700' : 'text-neutral-800')}>{opt.label}</p>
                    <p className="text-xs text-neutral-500 mt-0.5">{opt.desc}</p>
                  </button>
                ))}
              </div>

              {paType === 'prepayment' && (
                <div className="flex flex-col gap-3 rounded-lg border border-info-200 bg-info-50 p-4 mt-1">
                  <div className="flex items-start gap-2 text-xs text-info-700">
                    <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                    Prepayment requires settlement after goods/services are received and the final invoice is matched.
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="flex flex-col gap-1">
                      <label className="text-xs font-medium text-neutral-700">
                        Prepayment % <span className="text-danger-600">*</span>
                      </label>
                      <div className="relative">
                        <input
                          type="number" min={1} max={100} step={1}
                          value={prepaymentPct}
                          onChange={(e) => setPrepaymentPct(e.target.value)}
                          className={cn(
                            'h-9 w-full pl-3 pr-8 rounded-lg border text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600',
                            submitted && (!prepaymentPct || parseFloat(prepaymentPct) <= 0) ? 'border-danger-400' : 'border-neutral-300'
                          )}
                        />
                        <span className="absolute right-3 top-2 text-sm text-neutral-400">%</span>
                      </div>
                    </div>
                    <div className="flex flex-col gap-1">
                      <label className="text-xs font-medium text-neutral-700">
                        Expected Settlement Date <span className="text-danger-600">*</span>
                      </label>
                      <input
                        type="date"
                        value={expectedSettlement}
                        onChange={(e) => setExpectedSettlement(e.target.value)}
                        className={cn(
                          'h-9 px-3 rounded-lg border text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                          submitted && !expectedSettlement ? 'border-danger-400' : 'border-neutral-300'
                        )}
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* ── Step 3 — Charge Breakdown & Details ────────────────────────── */}
          <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-5">
            <h2 className="text-sm font-semibold text-neutral-800 flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-600 text-white text-[10px] font-bold">3</span>
              Charge Breakdown & Details
            </h2>

            {/* Title */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-700">
                PA Title <span className="text-danger-600">*</span>
              </label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Monthly Cleaning Services — April 2026"
                className={cn(
                  'h-10 px-3 rounded-lg border text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
                  submitted && !title.trim() ? 'border-danger-400' : 'border-neutral-300'
                )}
              />
              {submitted && !title.trim() && (
                <p className="text-xs text-danger-600">PA Title is required</p>
              )}
            </div>

            {/* Charge breakdown */}
            <div className="flex flex-col gap-3">
              <p className="text-xs font-semibold text-neutral-600 uppercase tracking-wide">Charge Breakdown ({po?.currency ?? ''})</p>

              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">
                    Pre-tax Amount <span className="text-danger-600">*</span>
                  </label>
                  <input
                    type="number" min={0} step={0.01}
                    value={subtotal}
                    onChange={(e) => setSubtotal(e.target.value)}
                    placeholder="0.00"
                    className={cn(
                      'h-10 px-3 rounded-lg border text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600',
                      submitted && subtotalNum <= 0 ? 'border-danger-400' : 'border-neutral-300'
                    )}
                  />
                  {submitted && subtotalNum <= 0 && (
                    <p className="text-xs text-danger-600">Pre-tax amount is required</p>
                  )}
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">
                    Tax Amount <span className="text-danger-600">*</span>
                    {!taxManuallyEdited && po?.currency === 'CAD' && subtotalNum > 0 && (
                      <span className="ml-1.5 text-[10px] font-normal text-neutral-400">13% HST</span>
                    )}
                  </label>
                  <input
                    type="number" min={0} step={0.01}
                    value={taxAmount}
                    onChange={(e) => { setTaxAmount(e.target.value); setTaxManuallyEdited(true) }}
                    placeholder="0.00"
                    className={cn(
                      'h-10 px-3 rounded-lg border text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600',
                      submitted && taxNum < 0 ? 'border-danger-400' : 'border-neutral-300'
                    )}
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">
                    Shipping / Freight <span className="text-neutral-400 font-normal ml-1">(optional)</span>
                  </label>
                  <input
                    type="number" min={0} step={0.01}
                    value={shippingAmount}
                    onChange={(e) => setShipping(e.target.value)}
                    placeholder="0.00"
                    className="h-10 px-3 rounded-lg border border-neutral-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600"
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">
                    Other Charges <span className="text-neutral-400 font-normal ml-1">(optional)</span>
                  </label>
                  <input
                    type="number" min={0} step={0.01}
                    value={otherCharges}
                    onChange={(e) => setOther(e.target.value)}
                    placeholder="0.00"
                    className="h-10 px-3 rounded-lg border border-neutral-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600"
                  />
                </div>
              </div>

              {otherNum > 0 && (
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-neutral-700">Other Charges Description</label>
                  <input
                    type="text"
                    value={otherChargesNote}
                    onChange={(e) => setOtherNote(e.target.value)}
                    placeholder="Describe the other charges…"
                    className="h-9 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                  />
                </div>
              )}

              <div className="flex items-center justify-between rounded-xl border-2 border-primary-200 bg-primary-50 px-5 py-3">
                <span className="text-sm font-semibold text-neutral-700">Total Payment Amount</span>
                <span className={cn('font-mono font-bold text-xl', paymentTotal > 0 ? 'text-primary-700' : 'text-neutral-300')}>
                  {paymentTotal > 0 ? formatAmount(paymentTotal, po?.currency ?? '') : '—'}
                </span>
              </div>
            </div>

            {/* Notes */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-neutral-700">
                Notes <span className="text-neutral-400 font-normal">(optional)</span>
              </label>
              <textarea
                rows={3}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Add any payment instructions or notes for the finance team…"
                className="px-3 py-2 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 resize-none"
              />
            </div>
          </div>

          {/* Errors */}
          {errors.length > 0 && (
            <div className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-3 flex flex-col gap-1">
              {errors.map((e) => (
                <p key={e} className="text-xs text-danger-700 flex items-center gap-1.5">
                  <AlertTriangle className="h-3 w-3 shrink-0" /> {e}
                </p>
              ))}
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-3">
            <Button variant="secondary" onClick={() => navigate(`/pa/${id}`)}>Cancel</Button>
            <Button variant="secondary" onClick={() => handleSave(false)} disabled={updatePa.isPending}>
              Save Draft
            </Button>
            <Button onClick={() => handleSave(true)} disabled={updatePa.isPending} className="gap-2">
              <CreditCard className="h-4 w-4" />
              Save & Resubmit
            </Button>
          </div>
        </div>

        {/* ── Sidebar — Summary ───────────────────────────────────────────────── */}
        <div className="flex flex-col gap-4">
          <div className="rounded-xl border border-neutral-200 bg-white p-4 shadow-sm sticky top-6">
            <p className="text-xs font-semibold text-neutral-600 uppercase tracking-wide mb-3">Payment Summary</p>
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5 text-xs">
                <div className="flex justify-between">
                  <span className="text-neutral-500">Vendor</span>
                  <span className="text-neutral-800 font-medium text-right max-w-[60%]">{pa.vendor_name}</span>
                </div>
                {po && (
                  <div className="flex justify-between">
                    <span className="text-neutral-500">PO Total</span>
                    <span className="font-mono font-semibold text-neutral-900">{formatAmount(po.total, po.currency)}</span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-neutral-500">Invoices linked</span>
                  <span className="text-neutral-700">{selectedInvoiceIds.size}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-neutral-500">GRs linked</span>
                  <span className="text-neutral-700">{selectedGrIds.size}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-neutral-500">Type</span>
                  <span className="text-neutral-700 capitalize">{paType}</span>
                </div>
              </div>

              {subtotalNum > 0 && po && (
                <div className="border-t border-neutral-200 pt-3 flex flex-col gap-1.5 text-xs">
                  <div className="flex justify-between text-neutral-500">
                    <span>Pre-tax</span>
                    <span className="font-mono">{formatAmount(subtotalNum, po.currency)}</span>
                  </div>
                  <div className="flex justify-between text-neutral-500">
                    <span>Tax</span>
                    <span className="font-mono">{formatAmount(taxNum, po.currency)}</span>
                  </div>
                  {shippingNum > 0 && (
                    <div className="flex justify-between text-neutral-500">
                      <span>Shipping</span>
                      <span className="font-mono">{formatAmount(shippingNum, po.currency)}</span>
                    </div>
                  )}
                  {otherNum > 0 && (
                    <div className="flex justify-between text-neutral-500">
                      <span>Other</span>
                      <span className="font-mono">{formatAmount(otherNum, po.currency)}</span>
                    </div>
                  )}
                  <div className="flex justify-between font-semibold text-neutral-800 border-t border-neutral-200 pt-1.5 mt-0.5">
                    <span>Total</span>
                    <span className="font-mono">{formatAmount(paymentTotal, po.currency)}</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
