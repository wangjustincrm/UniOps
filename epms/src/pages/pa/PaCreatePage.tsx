import { useState, useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, AlertTriangle, CreditCard, Info, Package, FileText, CircleDot } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { useCreatePa, usePas, usePa } from '@/hooks/usePas'
import { paService } from '@/services/pa'
import { usePos } from '@/hooks/usePos'
import { useAuthStore } from '@/stores/auth.store'
import { useInvoices } from '@/hooks/useInvoices'
import { useGrs } from '@/hooks/useGrs'
import type { ApiPo } from '@/services/po'

export default function PaCreatePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const createPa = useCreatePa()
  const { user } = useAuthStore()
  const { data: posData } = usePos()
  const allPos = posData?.items ?? []
  // 从 Prepayment 详情页「Settle」进入：加载来源预付以预填并锁定上下文
  const settleFromId = searchParams.get('settleFrom') ?? ''
  const lockedFromSettle = Boolean(settleFromId)
  const { data: sourcePrepay } = usePa(settleFromId)
  // ── Step 1 — PO selection ──────────────────────────────────────────────────
  const [poSearch, setPoSearch]         = useState('')
  const [selectedPoId, setSelectedPoId] = useState(searchParams.get('poId') ?? '')
  // Tracks the PO we've already applied matched-invoice/GR defaults for, so the
  // auto-selection runs once per PO and never re-checks boxes the user cleared.
  const autoSelectedForPoRef = useRef<string | null>(null)

  // ── Step 2 — Link invoices / GRs + type + PO lines ───────────────────────
  const [selectedInvoiceIds, setSelectedInvoiceIds] = useState<Set<string>>(new Set())
  const [selectedLineIds, setSelectedLineIds]       = useState<Set<string>>(new Set())
  const [selectedGrIds, setSelectedGrIds]           = useState<Set<string>>(new Set())
  const [paType, setPaType]                         = useState<'regular' | 'prepayment' | 'settlement' | 'balance'>('regular')
  const [prepaymentPct, setPrepaymentPct]           = useState('50')
  const [expectedSettlement, setExpectedSettlement] = useState('')
  const [prepaymentPaId, setPrepaymentPaId]         = useState('')

  // ── Step 3 — Charge breakdown & details ───────────────────────────────────
  const [title, setTitle]               = useState('')
  const [subtotal, setSubtotal]         = useState('')
  const [taxAmount, setTaxAmount]       = useState('')
  const [taxManuallyEdited, setTaxManuallyEdited] = useState(false)
  const [shippingAmount, setShipping]   = useState('')
  const [otherCharges, setOther]        = useState('')
  const [otherChargesNote, setOtherNote]= useState('')
  const [prepaymentApplied, setPrepaymentApplied] = useState('')
  const [notes, setNotes]               = useState('')
  const [submitted, setSubmitted]       = useState(false)

  // ── Derived ────────────────────────────────────────────────────────────────
  // A plain requester may only pay against POs linked to a PR they raised — even
  // if a special role assignment (e.g. finance_bp via Role Management) widened
  // their backend PO scope to all POs. Privileged roles keep full visibility.
  const requesterScoped = user?.role === 'requester'
  const eligiblePos = allPos.filter((p) =>
    ['approved', 'issued', 'partially_received', 'fully_received', 'closed'].includes(p.status) &&
    (p.is_prepaid || p.has_unpaid_invoice) &&
    (!requesterScoped || p.pr_requester_id === user?.id)
  )

  const filteredPos = eligiblePos.filter((p) =>
    !poSearch ||
    p.number.toLowerCase().includes(poSearch.toLowerCase()) ||
    p.vendor_name.toLowerCase().includes(poSearch.toLowerCase()) ||
    p.title.toLowerCase().includes(poSearch.toLowerCase())
  )

  // Prefer the eligible-list match; fall back to the full PO set so a pre-selected
  // PO (e.g. via Settle deep-link) still resolves even if outside the choosable list.
  const selectedPo: ApiPo | undefined =
    eligiblePos.find((p) => p.id === selectedPoId) ?? allPos.find((p) => p.id === selectedPoId)

  const { data: invoicesData } = useInvoices(selectedPoId ? { po_id: selectedPoId } : undefined)
  const { data: grsData } = useGrs(selectedPoId ? { po_id: selectedPoId } : undefined)
  const { data: poActivePas } = usePas(selectedPoId ? { po_id: selectedPoId } : undefined)
  const poInvoices = invoicesData?.items ?? []

  // settlement/balance「Original Prepayment PA」下拉数据源:本 PO 下未作废的预付 PA
  const linkablePrepayments = (poActivePas?.items ?? []).filter(
    (p) => p.pa_type === 'prepayment' && !['cancelled', 'rejected'].includes(p.status)
  )

  // Invoices already claimed by an active PA (not cancelled/rejected) on this PO
  const lockedInvoiceIds = new Set<string>(
    (poActivePas?.items ?? [])
      .filter((pa) => !['cancelled', 'rejected'].includes(pa.status))
      .flatMap((pa) => pa.invoice_ids)
  )
  const poGrs = (grsData?.items ?? []).filter((g) => g.status !== 'cancelled')

  // Auto-subtotal from selected PO lines
  const selectedPoLines = selectedPo ? selectedPo.line_items.filter((l) => selectedLineIds.has(l.id)) : []
  const autoSubtotal = selectedPoLines.reduce((s, l) => s + Number(l.line_total), 0)

  // Per-line received qty (sum across all GRs for this PO)
  const receivedQtyByPoLineId: Record<string, number> = {}
  for (const gr of poGrs) {
    for (const line of gr.line_items) {
      receivedQtyByPoLineId[line.po_line_id] = (receivedQtyByPoLineId[line.po_line_id] ?? 0) + Number(line.qty_received)
    }
  }

  // Computed total
  const subtotalNum  = parseFloat(subtotal)  || 0
  const taxNum       = parseFloat(taxAmount) || 0
  const shippingNum  = parseFloat(shippingAmount) || 0
  const otherNum     = parseFloat(otherCharges) || 0
  const appliedNum   = parseFloat(prepaymentApplied) || 0
  const grossTotal   = subtotalNum + taxNum + shippingNum + otherNum
  const isSettlementType = paType === 'settlement'
  // Net payable = 全额 − 预付抵扣(余款);非 settlement 类型即全额
  const netPayable   = isSettlementType ? Math.max(grossTotal - appliedNum, 0) : grossTotal
  const isOverpaid   = isSettlementType && grossTotal - appliedNum < -0.01
  // net==0 settlement = pure reconciliation: no cash, no bank, no standard approval
  const isReconcileOnly  = isSettlementType && grossTotal > 0 && netPayable === 0

  // Tax follows the PO being paid: the PO already snapshotted its rate/code from
  // Finance Tax Settings (mdm-api), so PA inherits it rather than re-deriving.
  // Falls back to 13% only for legacy POs with no rate recorded.
  const paTaxRate = selectedPo ? Number(selectedPo.tax_rate) : 0.13
  const paTaxCode = selectedPo?.tax_code ?? null

  // Reset when PO changes. Skipped in Settle mode — there the source-prepayment
  // effect below owns PO/type/prepayment prefill and the PO is locked.
  useEffect(() => {
    if (lockedFromSettle) return
    const po = allPos.find((p) => p.id === selectedPoId)
    autoSelectedForPoRef.current = null
    setSelectedInvoiceIds(new Set())
    setSelectedGrIds(new Set())
    setSelectedLineIds(new Set())
    setPaType(po?.is_prepaid ? 'prepayment' : 'regular')
    setPrepaymentPct('50')
    setExpectedSettlement('')
    setPrepaymentPaId('')
    setPrepaymentApplied('')
    setTitle('')
    setSubtotal('')
    setTaxAmount('')
    setTaxManuallyEdited(false)
    setShipping('')
    setOther('')
    setOtherNote('')
  }, [selectedPoId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Settle 模式:来源预付加载后锁定并预填 PO / 类型 / Original Prepayment PA / 抵扣额
  useEffect(() => {
    if (!sourcePrepay) return
    setSelectedPoId(sourcePrepay.po_id)
    setPaType('settlement')
    setPrepaymentPaId(sourcePrepay.id)
    setPrepaymentApplied(String(sourcePrepay.payment_amount ?? ''))
  }, [sourcePrepay?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Pre-select the PO's matched invoices (and the GRs they were matched against)
  // once their data has loaded. Matched invoices are the ones ready to pay, so
  // defaulting them in saves the user re-checking what the 3-way match already
  // confirmed. Runs once per PO (guarded by the ref) so manual de-selection sticks.
  useEffect(() => {
    if (!selectedPoId) return
    if (autoSelectedForPoRef.current === selectedPoId) return
    // Wait until every list query for this PO has resolved.
    if (invoicesData === undefined || grsData === undefined || poActivePas === undefined) return

    const matchedInvoices = poInvoices.filter(
      (inv) => inv.status === 'matched' && !lockedInvoiceIds.has(inv.id)
    )

    // GRs referenced by those matched invoices (the receipts proven by the match).
    const matchedGrIds = new Set<string>()
    for (const inv of matchedInvoices) {
      if (inv.gr_ids?.length) inv.gr_ids.forEach((id) => matchedGrIds.add(id))
      else if (inv.gr_id) matchedGrIds.add(inv.gr_id)
    }
    const matchedGrs = poGrs.filter((g) => matchedGrIds.has(g.id))

    autoSelectedForPoRef.current = selectedPoId
    if (matchedInvoices.length > 0) setSelectedInvoiceIds(new Set(matchedInvoices.map((i) => i.id)))
    if (matchedGrs.length > 0) setSelectedGrIds(new Set(matchedGrs.map((g) => g.id)))
  }, [selectedPoId, invoicesData, grsData, poActivePas]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-fill title when PO is selected (if not yet typed).
  // Also depends on selectedPo?.id so it re-runs when React Query data loads
  // after navigating here via ?poId= URL param.
  useEffect(() => {
    if (selectedPo && !title) {
      setTitle(`Payment — ${selectedPo.vendor_name} ${selectedPo.number}`)
    }
  }, [selectedPoId, selectedPo?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-fill subtotal from selected PO lines
  useEffect(() => {
    if (autoSubtotal > 0) {
      setSubtotal(String(autoSubtotal))
    }
  }, [autoSubtotal]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-fill tax at the PO's tax rate (from Finance Tax Settings) when currency
  // is CAD and the user hasn't manually overridden the amount.
  useEffect(() => {
    if (taxManuallyEdited) return
    if (selectedPo?.currency === 'CAD' && subtotalNum > 0) {
      setTaxAmount((subtotalNum * paTaxRate).toFixed(2))
    } else if (!taxManuallyEdited) {
      setTaxAmount('')
    }
  }, [subtotalNum, selectedPo?.currency, paTaxRate, taxManuallyEdited]) // eslint-disable-line react-hooks/exhaustive-deps

  // Validation
  const errors: string[] = []
  if (submitted) {
    if (!selectedPo)     errors.push('Please select a PO')
    if (!title.trim())   errors.push('PA Title is required')
    if (subtotalNum <= 0) errors.push('Pre-tax amount must be greater than zero')
    if (taxNum < 0)      errors.push('Tax amount cannot be negative')
    if (paType === 'prepayment' && (!prepaymentPct || parseFloat(prepaymentPct) <= 0 || parseFloat(prepaymentPct) > 100))
      errors.push('Prepayment percentage must be between 1 and 100')
    if (paType === 'prepayment' && !expectedSettlement)
      errors.push('Expected settlement date is required for prepayment')
    if (isSettlementType && !prepaymentPaId)
      errors.push('Original Prepayment PA is required')
    // A settlement may net to zero (fully covered by the prepayment — pure
    // reconciliation). Only non-settlement PAs require a positive amount; the
    // invoice value itself is already guarded by the pre-tax check above.
    if (!isSettlementType && netPayable <= 0)
      errors.push('Total payment amount must be greater than zero')
    if (isSettlementType && appliedNum > grossTotal + 0.01)
      errors.push('Prepayment applied cannot exceed the invoice total')
  }

  const toggleInvoice = (id: string) => {
    setSelectedInvoiceIds((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id); else n.add(id)
      return n
    })
  }

  const toggleGr = (id: string) => {
    setSelectedGrIds((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id); else n.add(id)
      return n
    })
  }

  const toggleLine = (id: string) => {
    setSelectedLineIds((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id); else n.add(id)
      return n
    })
  }

  const handleSubmit = async () => {
    setSubmitted(true)
    if (!selectedPo) return
    if (!title.trim() || subtotalNum <= 0 || taxNum < 0) return
    if (!isSettlementType && netPayable <= 0) return
    if (isSettlementType && (appliedNum > grossTotal + 0.01)) return
    if (paType === 'prepayment' && (!prepaymentPct || !expectedSettlement)) return
    if (isSettlementType && !prepaymentPaId) return
    try {
      const paLineItems = selectedPo.line_items
        .filter((l) => selectedLineIds.has(l.id))
        .map((l) => ({
          po_line_id: l.id,
          description: l.description,
          qty: Number(l.qty),
          unit: l.unit,
          unit_price: Number(l.unit_price),
          line_total: Number(l.line_total),
        }))
      const newPa = await createPa.mutateAsync({
        title: title.trim(),
        pa_type: paType,
        currency: selectedPo.currency,
        subtotal: subtotalNum,
        tax_amount: taxNum,
        tax_code: taxNum > 0 ? paTaxCode ?? undefined : null,
        tax_rate: taxNum > 0 ? paTaxRate : null,
        shipping_amount: shippingNum || undefined,
        other_charges: otherNum || undefined,
        other_charges_note: otherNum > 0 ? otherChargesNote.trim() || undefined : undefined,
        vendor_id: selectedPo.vendor_id,
        vendor_name: selectedPo.vendor_name,
        po_id: selectedPo.id,
        invoice_ids: Array.from(selectedInvoiceIds),
        gr_ids: Array.from(selectedGrIds),
        prepayment_pct: paType === 'prepayment' ? parseFloat(prepaymentPct) : undefined,
        expected_settlement_date: paType === 'prepayment' ? expectedSettlement : undefined,
        prepayment_pa_id: isSettlementType ? prepaymentPaId || undefined : undefined,
        // Cap applied at the invoice total: you can't apply more prepayment than
        // the invoice is worth. Any excess prepaid is the overpayment, detected
        // backend-side (prepaid vs final) and routed to a credit-note confirmation.
        prepayment_applied: isSettlementType ? (Math.min(appliedNum, grossTotal) || undefined) : undefined,
        line_items: paLineItems,
        notes: notes.trim() || undefined,
      })
      // net==0 settlements are finalized by the backend at creation (auto-reconciled
      // or queued for finance confirmation) and are no longer 'draft' — only submit
      // documents that still need the standard approval flow.
      if (newPa.status === 'draft') {
        await paService.action(newPa.id, { action: 'submit' })
      }
      await queryClient.invalidateQueries({ queryKey: ['pas'] })
      navigate('/pa')
    } catch {
      // error handled by mutation
    }
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button onClick={() => navigate('/pa')} className="p-2 rounded-lg hover:bg-neutral-100 text-neutral-500">
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">New Payment Application</h1>
          <p className="text-sm text-neutral-500 mt-0.5">Create a payment request linked to a PO</p>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Main form */}
        <div className="col-span-2 flex flex-col gap-5">

          {/* ── Step 1 — Select PO ─────────────────────────────────────────── */}
          <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-4">
            <h2 className="text-sm font-semibold text-neutral-800 flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-600 text-white text-[10px] font-bold">1</span>
              {lockedFromSettle ? 'Purchase Order' : 'Select Purchase Order'}
            </h2>

            {lockedFromSettle ? (
              selectedPo ? (
                <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs font-semibold text-primary-700">{selectedPo.number}</span>
                    <span className="font-mono text-xs font-semibold text-neutral-900">{formatAmount(selectedPo.total, selectedPo.currency)}</span>
                  </div>
                  <div className="text-sm text-neutral-700 mt-0.5">{selectedPo.title}</div>
                  <div className="text-xs text-neutral-400 mt-0.5">{selectedPo.vendor_name}</div>
                  <p className="text-[11px] text-neutral-400 mt-2">Locked — settling prepayment {sourcePrepay?.pa_number}.</p>
                </div>
              ) : (
                <p className="px-3 py-4 text-xs text-neutral-400 text-center">Loading prepayment…</p>
              )
            ) : (
              <div className="flex flex-col gap-2">
                <input
                  value={poSearch}
                  onChange={(e) => setPoSearch(e.target.value)}
                  placeholder="Search PO # or vendor…"
                  className="h-9 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                />
                <div className="rounded-lg border border-neutral-200 max-h-48 overflow-y-auto">
                  {filteredPos.length === 0 ? (
                    <p className="px-3 py-4 text-xs text-neutral-400 text-center">No eligible POs found</p>
                  ) : (
                    filteredPos.map((po) => (
                      <button
                        key={po.id}
                        type="button"
                        onClick={() => setSelectedPoId(po.id)}
                        className={cn(
                          'w-full px-4 py-3 text-left border-b border-neutral-100 last:border-0 hover:bg-primary-50 transition-colors',
                          selectedPoId === po.id && 'bg-primary-50'
                        )}
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-xs font-semibold text-primary-700">{po.number}</span>
                          <span className="font-mono text-xs font-semibold text-neutral-900">{formatAmount(po.total, po.currency)}</span>
                        </div>
                        <div className="text-sm text-neutral-700 mt-0.5">{po.title}</div>
                        <div className="text-xs text-neutral-400 mt-0.5">{po.vendor_name}</div>
                      </button>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>

          {/* ── Step 2 — Link Invoices / GRs + PA Type ─────────────────────── */}
          {selectedPo && (
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
                    {poInvoices.map((inv) => {
                      const isLocked = lockedInvoiceIds.has(inv.id)
                      return (
                        <label
                          key={inv.id}
                          className={cn(
                            'flex items-center gap-3 px-4 py-3 transition-colors',
                            isLocked
                              ? 'cursor-not-allowed bg-neutral-50 opacity-60'
                              : 'cursor-pointer hover:bg-primary-50',
                            !isLocked && selectedInvoiceIds.has(inv.id) && 'bg-primary-50'
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={selectedInvoiceIds.has(inv.id)}
                            onChange={() => !isLocked && toggleInvoice(inv.id)}
                            disabled={isLocked}
                            className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-600 disabled:opacity-50"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between gap-2">
                              <div className="flex items-center gap-2 min-w-0">
                                <span className="font-mono text-xs font-semibold text-primary-700">{inv.internal_ref}</span>
                                {isLocked && (
                                  <span className="shrink-0 inline-flex items-center rounded-full bg-neutral-200 px-1.5 py-0.5 text-[10px] font-medium text-neutral-600">
                                    Already in PA
                                  </span>
                                )}
                              </div>
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
                      )
                    })}
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
                      {selectedPo.line_items.map((line, idx) => {
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
                              {formatAmount(line.line_total, selectedPo.currency)}
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
                        {formatAmount(autoSubtotal, selectedPo.currency)}
                      </span>
                    </div>
                  )}
                </div>
              </div>

              {/* PA Type */}
              <div className="flex flex-col gap-2">
                <p className="text-xs font-medium text-neutral-600">Payment Type</p>
                <div className="grid grid-cols-2 gap-3">
                  {[
                    { value: 'regular' as const,     label: 'Regular Payment',  desc: 'Standard payment against invoice / GR' },
                    { value: 'prepayment' as const,  label: 'Prepayment',       desc: 'Advance payment before full GR/invoice' },
                    { value: 'settlement' as const,  label: 'Settlement',       desc: 'Reconcile a prepayment; pays only the remaining balance' },
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

                {isSettlementType && (
                  <div className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 mt-1">
                    <p className="text-xs text-amber-700 font-medium">
                      Settlement reconciles a prepayment after delivery; it pays only the remaining balance (final invoice − prepaid).
                    </p>
                    <div className="flex flex-col gap-1">
                      <label className="text-xs font-medium text-neutral-700">
                        Original Prepayment PA <span className="text-danger-600">*</span>
                      </label>
                      <select
                        value={prepaymentPaId}
                        onChange={(e) => setPrepaymentPaId(e.target.value)}
                        disabled={lockedFromSettle}
                        className={cn(
                          'h-9 w-full rounded-lg border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-500',
                          submitted && !prepaymentPaId ? 'border-danger-400' : 'border-neutral-300'
                        )}
                      >
                        <option value="">Select a prepayment PA…</option>
                        {linkablePrepayments.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.pa_number} · {formatAmount(p.payment_amount, p.currency)}
                            {p.settlement_status ? ` · ${p.settlement_status}` : ''}
                          </option>
                        ))}
                      </select>
                      {linkablePrepayments.length === 0 && (
                        <p className="text-[11px] text-neutral-400">No prepayment PAs found for this PO.</p>
                      )}
                      <p className="text-[11px] text-neutral-400">
                        The prepayment this {paType} reconciles. Auto-loaded from the selected PO.
                      </p>
                    </div>
                  </div>
                )}

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
          )}

          {/* ── Step 3 — Charge Breakdown & Details ────────────────────────── */}
          {selectedPo && (
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
                <p className="text-xs font-semibold text-neutral-600 uppercase tracking-wide">Charge Breakdown ({selectedPo.currency})</p>

                <div className="grid grid-cols-2 gap-3">
                  {/* Pre-tax */}
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

                  {/* Tax */}
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-neutral-700">
                      Tax Amount <span className="text-danger-600">*</span>
                      {!taxManuallyEdited && selectedPo?.currency === 'CAD' && subtotalNum > 0 && (
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

                  {/* Shipping */}
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-neutral-700">
                      Shipping / Freight
                      <span className="text-neutral-400 font-normal ml-1">(optional)</span>
                    </label>
                    <input
                      type="number" min={0} step={0.01}
                      value={shippingAmount}
                      onChange={(e) => setShipping(e.target.value)}
                      placeholder="0.00"
                      className="h-10 px-3 rounded-lg border border-neutral-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600"
                    />
                  </div>

                  {/* Other charges */}
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-neutral-700">
                      Other Charges
                      <span className="text-neutral-400 font-normal ml-1">(optional)</span>
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

                {/* Other charges description */}
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

                {/* Prepayment applied (settlement / balance only) */}
                {isSettlementType && (
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-neutral-700">
                      Prepayment Applied (deducted)
                    </label>
                    <input
                      type="number" min={0} step={0.01}
                      value={prepaymentApplied}
                      onChange={(e) => setPrepaymentApplied(e.target.value)}
                      placeholder="0.00"
                      className="h-10 px-3 rounded-lg border border-neutral-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600"
                    />
                    <p className="text-[11px] text-neutral-400">
                      Amount already paid via the prepayment PA. Net payable = total − this.
                    </p>
                    {isOverpaid && (
                      <p className="text-xs text-warning-700">
                        Prepayment exceeds the final amount — net payable is 0; a credit note is expected.
                      </p>
                    )}
                  </div>
                )}

                {/* Total */}
                <div className="flex flex-col gap-1 rounded-xl border-2 border-primary-200 bg-primary-50 px-5 py-3">
                  {isSettlementType && appliedNum > 0 && (
                    <>
                      <div className="flex items-center justify-between text-xs text-neutral-500">
                        <span>Gross total</span>
                        <span className="font-mono">{formatAmount(grossTotal, selectedPo.currency)}</span>
                      </div>
                      <div className="flex items-center justify-between text-xs text-neutral-500">
                        <span>Prepayment applied</span>
                        <span className="font-mono">−{formatAmount(appliedNum, selectedPo.currency)}</span>
                      </div>
                    </>
                  )}
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-neutral-700">
                      {isSettlementType ? 'Net Payable' : 'Total Payment Amount'}
                    </span>
                    <span className={cn(
                      'font-mono font-bold text-xl',
                      netPayable > 0 ? 'text-primary-700' : 'text-neutral-300'
                    )}>
                      {netPayable > 0 ? formatAmount(netPayable, selectedPo.currency) : formatAmount(0, selectedPo.currency)}
                    </span>
                  </div>
                  {isReconcileOnly && (
                    <p className="text-[11px] text-neutral-500 mt-1">
                      Fully covered by the prepayment — <strong>no payment will be made</strong>. This only
                      reconciles the prepayment{isOverpaid ? ' (overpaid — finance will confirm the credit note)' : ''}.
                    </p>
                  )}
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
          )}

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
            <Button variant="secondary" onClick={() => navigate('/pa')}>Cancel</Button>
            <Button onClick={handleSubmit} disabled={!selectedPo} className="gap-2">
              <CreditCard className="h-4 w-4" />
              {isReconcileOnly ? 'Reconcile Prepayment' : 'Submit Payment Application'}
            </Button>
          </div>
        </div>

        {/* ── Sidebar — Payment Summary ───────────────────────────────────────── */}
        <div className="flex flex-col gap-4">
          <div className="rounded-xl border border-neutral-200 bg-white p-4 shadow-sm sticky top-6">
            <p className="text-xs font-semibold text-neutral-600 uppercase tracking-wide mb-3">Payment Summary</p>

            {!selectedPo ? (
              <p className="text-xs text-neutral-400 text-center py-4">Select a PO to see summary</p>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="flex flex-col gap-1.5 text-xs">
                  <div className="flex justify-between">
                    <span className="text-neutral-500">Vendor</span>
                    <span className="text-neutral-800 font-medium text-right max-w-[60%]">{selectedPo.vendor_name}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-neutral-500">PO Total</span>
                    <span className="font-mono font-semibold text-neutral-900">{formatAmount(selectedPo.total, selectedPo.currency)}</span>
                  </div>
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

                {/* Charge breakdown in sidebar */}
                {subtotalNum > 0 && (
                  <div className="border-t border-neutral-200 pt-3 flex flex-col gap-1.5 text-xs">
                    <div className="flex justify-between text-neutral-500">
                      <span>Pre-tax</span>
                      <span className="font-mono">{formatAmount(subtotalNum, selectedPo.currency)}</span>
                    </div>
                    <div className="flex justify-between text-neutral-500">
                      <span>Tax</span>
                      <span className="font-mono">{formatAmount(taxNum, selectedPo.currency)}</span>
                    </div>
                    {shippingNum > 0 && (
                      <div className="flex justify-between text-neutral-500">
                        <span>Shipping</span>
                        <span className="font-mono">{formatAmount(shippingNum, selectedPo.currency)}</span>
                      </div>
                    )}
                    {otherNum > 0 && (
                      <div className="flex justify-between text-neutral-500">
                        <span>Other</span>
                        <span className="font-mono">{formatAmount(otherNum, selectedPo.currency)}</span>
                      </div>
                    )}
                    {isSettlementType && appliedNum > 0 && (
                      <div className="flex justify-between text-neutral-500">
                        <span>Prepayment applied</span>
                        <span className="font-mono">−{formatAmount(appliedNum, selectedPo.currency)}</span>
                      </div>
                    )}
                    <div className="flex justify-between font-semibold text-neutral-800 border-t border-neutral-200 pt-1.5 mt-0.5">
                      <span>{isSettlementType ? 'Net Payable' : 'Total'}</span>
                      <span className="font-mono">{formatAmount(netPayable, selectedPo.currency)}</span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
