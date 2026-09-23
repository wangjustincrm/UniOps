import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import { useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, AlertTriangle, CreditCard, Info, Package, FileText, CircleDot } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { usePa, useUpdatePa } from '@/hooks/usePas'
import { usePaAttachments, useUploadPaAttachment, useDeletePaAttachment } from '@/hooks/usePaAttachments'
import { paAttachmentService } from '@/services/paAttachments'
import { AttachmentsEditor } from '@/components/shared/AttachmentsEditor'
import { paService } from '@/services/pa'
import { usePos, usePosByIds } from '@/hooks/usePos'
import { useAgreement } from '@/hooks/useAgreements'
import { useInvoices, useInvoicesForPos } from '@/hooks/useInvoices'
import { useGrsForPos } from '@/hooks/useGrs'
import type { ApiPo } from '@/services/po'

export default function PaEditPage() {
  const { id } = useParams<{ id: string }>()
  const replaceTab = useReplaceTab(epmsRoutes)
  const queryClient = useQueryClient()
  const { data: pa, isLoading } = usePa(id ?? '')
  useDocTabTitle(pa?.pa_number && `Edit ${pa.pa_number}`)
  const updatePa = useUpdatePa()
  // Attachments hang off the saved PA, so upload/delete apply immediately
  // rather than waiting for Save (same semantics as the Detail page).
  const { data: attachments = [] } = usePaAttachments(id ?? '')
  const uploadAttachment = useUploadPaAttachment(id ?? '')
  const deleteAttachment = useDeletePaAttachment(id ?? '')

  // ── Step 1 — PO is fixed, just fetch it ───────────────────────────────────
  // …unless this PA was raised against an AGREEMENT, which has no PO at all.
  // Every query and every section below used to key off pa.po_id
  // unconditionally, so an agreement PA opened the PO-based editor with an
  // empty PO card, an empty "Invoices for this PO" list, an empty goods-receipt
  // list and an empty PO-line table — the whole form describing a document that
  // does not exist on this route. Saving from it would have sent gr_ids: [] and
  // line_items: [] over a PA that never had either.
  const isAgreementMode = !pa?.po_id && !!pa?.agreement_id
  // A draft may cover several POs and the set is editable here. Initialised
  // from the PA once it loads (see the pre-fill effect below); `null` means
  // "not loaded yet", which is distinct from "no POs".
  const [editPoIds, setEditPoIds] = useState<string[] | null>(null)
  const poIds = editPoIds ?? (pa?.po_ids?.length ? pa.po_ids : (pa?.po_id ? [pa.po_id] : []))
  const { items: linkedPos } = usePosByIds(poIds)
  const pos = linkedPos ?? []
  // The primary PO — every single-PO rule below (payment type, tax snapshot,
  // currency) still reads off it, exactly as before.
  const po = pos[0]
  const { data: agreement } = useAgreement(pa?.agreement_id ?? '')
  const { data: posData } = usePos(undefined, !isAgreementMode)
  const allPos = posData?.items ?? []
  const [addingPo, setAddingPo] = useState(false)
  const [poSearch, setPoSearch] = useState('')

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

  const { data: agreementInvoicesData } = useInvoices(
    isAgreementMode && pa?.agreement_id ? { agreement_id: pa.agreement_id } : undefined,
    isAgreementMode,
  )
  // No goods receipt exists on the agreement route, ever — skip the request
  // rather than fire it with an undefined filter and get every GR in the system.
  const invoicesForPos = useInvoicesForPos(isAgreementMode ? [] : poIds, !isAgreementMode)
  const grsForPos      = useGrsForPos(isAgreementMode ? [] : poIds, !isAgreementMode)
  const poInvoices = isAgreementMode
    ? (agreementInvoicesData?.items ?? [])
    : (invoicesForPos.items ?? [])
  const poGrs      = (grsForPos.items ?? []).filter((g) => g.status !== 'cancelled')

  // Pre-fill form once PA data loads
  useEffect(() => {
    if (!pa || initialized) return
    setEditPoIds(pa.po_ids?.length ? [...pa.po_ids] : (pa.po_id ? [pa.po_id] : []))
    setSelectedInvoiceIds(new Set(pa.invoice_ids))
    setSelectedLineIds(new Set(pa.line_items.map((l) => l.po_line_id)))
    setPrepaymentPct(pa.prepayment_pct?.toString() ?? '50')
    setExpectedSettlement(pa.expected_settlement_date ?? '')
    setTitle(pa.title)
    setSubtotal(pa.subtotal.toString())
    setTaxAmount(pa.tax_amount.toString())
    setTaxManuallyEdited(true) // keep user's existing tax value
    setShipping(pa.shipping_amount != null ? String(pa.shipping_amount) : '')
    setOther(pa.other_charges != null ? String(pa.other_charges) : '')
    setOtherNote(pa.other_charges_note ?? '')
    setNotes(pa.notes ?? '')
    setInitialized(true)
  }, [pa, initialized])

  // PA type is derived from the linked PO (a prepaid PO → prepayment), never a
  // free user choice — keep the on-screen type in sync with the PO.
  useEffect(() => {
    if (po) setPaType(po.is_prepaid ? 'prepayment' : 'regular')
  }, [po?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Derived ────────────────────────────────────────────────────────────────
  const isMultiPo = poIds.length > 1
  const selectedPoLines = pos.flatMap((p) => p.line_items.filter((l) => selectedLineIds.has(l.id)))
  const autoSubtotal    = selectedPoLines.reduce((s, l) => s + l.line_total, 0)

  // Same rules the backend enforces (epms-api pa.py::_assert_pos_coherent), said
  // on the row rather than as a 422 after Save.
  const poJoinBlockedReason = (candidate: ApiPo): string | null => {
    if (!po || po.id === candidate.id) return null
    if (candidate.vendor_id !== po.vendor_id) return 'Different vendor'
    if ((candidate.currency ?? 'CAD') !== (po.currency ?? 'CAD')) return 'Different currency'
    if (paType !== 'regular') return 'Single PO only for this payment type'
    return null
  }
  const addablePos = allPos.filter((p) =>
    !poIds.includes(p.id) &&
    ['approved', 'issued', 'partially_received', 'fully_received', 'closed'].includes(p.status) &&
    (!poSearch ||
      p.number.toLowerCase().includes(poSearch.toLowerCase()) ||
      p.vendor_name.toLowerCase().includes(poSearch.toLowerCase()) ||
      p.title.toLowerCase().includes(poSearch.toLowerCase()))
  )
  // Removing a PO must not leave lines or receipts behind that belong to it —
  // they would be saved onto a payment that no longer covers that order.
  const removePo = (poId: string) => {
    const remaining = poIds.filter((p) => p !== poId)
    setEditPoIds(remaining)
    const dropped = pos.find((p) => p.id === poId)
    if (dropped) {
      const droppedLineIds = new Set(dropped.line_items.map((l) => l.id))
      setSelectedLineIds((prev) => new Set([...prev].filter((lid) => !droppedLineIds.has(lid))))
      const droppedGrIds = new Set(poGrs.filter((g) => g.po_id === poId).map((g) => g.id))
      setSelectedGrIds((prev) => new Set([...prev].filter((gid) => !droppedGrIds.has(gid))))
    }
    // Untick any invoice that no longer has a home on this payment. An invoice
    // belongs to a PO by header link OR by line allocation (a single invoice can
    // be split across POs), so both are consulted before dropping one — an
    // invoice header-linked to the removed PO but allocated to one that stays is
    // still payable here and must survive. The backend rejects the stragglers
    // this cannot see (epms-api pa.py::_assert_invoices_belong_to_pos); doing it
    // here keeps the operator from meeting that 422 for something the screen
    // already knew.
    const stillCovered = (inv: (typeof poInvoices)[number]) =>
      (inv.po_id != null && remaining.includes(inv.po_id)) ||
      (inv.allocations ?? []).some((a) => remaining.includes(a.po_id))
    const droppedInvoiceIds = new Set(
      poInvoices.filter((inv) => !stillCovered(inv)).map((inv) => inv.id)
    )
    setSelectedInvoiceIds((prev) => new Set([...prev].filter((iid) => !droppedInvoiceIds.has(iid))))
  }

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
  // The PA's own currency is authoritative and present on both routes; `po`
  // is undefined for the whole agreement route, and every amount in the
  // sidebar used to be formatted with po.currency behind a `po &&` guard —
  // which silently hid the entire money breakdown for an agreement PA.
  const paCurrency = pa?.currency ?? po?.currency ?? agreement?.currency ?? 'CAD'
  // Tax snapshot: the PO's rate on the PO route; on the agreement route the
  // PA already carries the rate it was created with (PaCreatePage derives it
  // from the linked invoices), and the 0.13 fallback is a PO-route default
  // that must not silently rewrite an agreement PA's tax.
  const paTaxRate = isAgreementMode
    ? Number(pa?.tax_rate ?? 0)
    : (po ? Number(po.tax_rate) : 0.13)
  const paTaxCode = (isAgreementMode ? pa?.tax_code : po?.tax_code) ?? pa?.tax_code ?? null

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
    // `!po` used to be an unconditional bail-out. On the agreement route po is
    // never loaded, so Save and Save-and-Submit both returned here — the
    // buttons went through their motions and wrote nothing, with no error.
    if (!pa) return
    if (!isAgreementMode && !po) return
    if (!isAgreementMode && poIds.length === 0) return
    if (!title.trim() || subtotalNum <= 0 || taxNum < 0 || paymentTotal <= 0) return
    if (paType === 'prepayment' && (!prepaymentPct || !expectedSettlement)) return

    try {
      // Lines from every PO still on the payment.
      const paLineItems = pos.flatMap((p) =>
        p.line_items
          .filter((l) => selectedLineIds.has(l.id))
          .map((l) => ({
            po_line_id: l.id,
            description: l.description,
            qty: l.qty,
            unit: l.unit,
            unit_price: l.unit_price,
          }))
      )

      await updatePa.mutateAsync({
        id: pa.id,
        body: {
          title: title.trim(),
          // pa_type & currency are derived from the PO — not sent from the client.
          // The PO set is sent only on the PO route, and only as a complete
          // replacement (the backend rejects an empty list).
          po_ids: isAgreementMode ? undefined : poIds,
          subtotal: subtotalNum,
          tax_amount: taxNum,
          tax_code: taxNum > 0 ? paTaxCode ?? undefined : null,
          tax_rate: taxNum > 0 ? paTaxRate : null,
          // Sent even when zero. `|| undefined` would drop the field, and a
          // field the body omits is a field the backend leaves alone — so
          // clearing a charge back to 0 would silently keep the old amount.
          shipping_amount: shippingNum,
          other_charges: otherNum,
          invoice_ids: Array.from(selectedInvoiceIds),
          // Omitted entirely on the agreement route rather than sent empty:
          // there is no PO line and no goods receipt to carry, and `[]` is a
          // value — it would overwrite, not skip.
          gr_ids: isAgreementMode ? undefined : Array.from(selectedGrIds),
          prepayment_pct: paType === 'prepayment' ? parseFloat(prepaymentPct) : undefined,
          expected_settlement_date: paType === 'prepayment' ? expectedSettlement : undefined,
          line_items: isAgreementMode ? undefined : paLineItems,
          notes: notes.trim() || undefined,
        },
      })

      if (andSubmit) {
        await paService.action(pa.id, { action: 'submit' })
        await queryClient.invalidateQueries({ queryKey: ['pas'] })
        replaceTab('/pa')
      } else {
        replaceTab(`/pa/${pa.id}`)
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
        <BackLink to={`/pa/${id}`} className="mt-4"><Button variant="secondary">Back</Button></BackLink>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button onClick={() => replaceTab(`/pa/${id}`)} className="p-2 rounded-lg hover:bg-neutral-100 text-neutral-500">
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

          {/* ── PO / Agreement (locked) ────────────────────────────────────── */}
          <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-neutral-800 flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-600 text-white text-[10px] font-bold">1</span>
              {isAgreementMode ? 'Purchase Agreement' : (isMultiPo ? 'Purchase Orders' : 'Purchase Order')}
            </h2>
            {isAgreementMode ? (
              <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3 flex items-center justify-between">
                <div>
                  <p className="font-mono text-xs font-semibold text-primary-700">{pa.agreement_number}</p>
                  <p className="text-xs text-neutral-500 mt-0.5">
                    {`${agreement?.vendor_name ?? pa.vendor_name}${agreement?.title ? ` · ${agreement.title}` : ''}`}
                  </p>
                </div>
                <span className="text-xs text-neutral-400 italic">Locked</span>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {pos.map((p, i) => (
                  <div key={p.id} className="rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3 flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-mono text-xs font-semibold text-primary-700">
                        {p.number}
                        {i === 0 && isMultiPo && (
                          <span className="ml-2 inline-flex items-center rounded-full bg-primary-100 px-1.5 py-0.5 text-[10px] font-medium text-primary-700">
                            Primary
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-neutral-500 mt-0.5">{p.vendor_name} · {p.title}</p>
                    </div>
                    {/* The last PO cannot be removed — a PO-route payment with no
                        purchase order is not a document this app has a place for
                        (the backend refuses an empty set for the same reason). */}
                    {pos.length > 1 && (
                      <button
                        type="button"
                        onClick={() => removePo(p.id)}
                        className="shrink-0 text-[11px] font-medium text-neutral-500 hover:text-danger-600 hover:underline"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                ))}
                {addingPo ? (
                  <div className="flex flex-col gap-2 rounded-lg border border-neutral-200 p-3">
                    <div className="flex items-center gap-2">
                      <input
                        value={poSearch}
                        onChange={(e) => setPoSearch(e.target.value)}
                        placeholder="Search PO # or vendor…"
                        className="h-9 flex-1 px-3 rounded-lg border border-neutral-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                      />
                      <button
                        type="button"
                        onClick={() => { setAddingPo(false); setPoSearch('') }}
                        className="text-[11px] font-medium text-neutral-500 hover:underline"
                      >
                        Cancel
                      </button>
                    </div>
                    <div className="rounded-lg border border-neutral-200 max-h-48 overflow-y-auto">
                      {addablePos.length === 0 ? (
                        <p className="px-3 py-4 text-xs text-neutral-400 text-center">No other POs found</p>
                      ) : (
                        addablePos.map((candidate) => {
                          const blockedReason = poJoinBlockedReason(candidate)
                          return (
                            <button
                              key={candidate.id}
                              type="button"
                              disabled={Boolean(blockedReason)}
                              onClick={() => {
                                setEditPoIds([...poIds, candidate.id])
                                setAddingPo(false)
                                setPoSearch('')
                              }}
                              className={cn(
                                'w-full px-4 py-2.5 text-left border-b border-neutral-100 last:border-0 transition-colors',
                                blockedReason ? 'cursor-not-allowed bg-neutral-50 opacity-60' : 'hover:bg-primary-50',
                              )}
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="flex items-center gap-2 min-w-0">
                                  <span className="font-mono text-xs font-semibold text-primary-700">{candidate.number}</span>
                                  {blockedReason && (
                                    <span className="shrink-0 inline-flex items-center rounded-full bg-neutral-200 px-1.5 py-0.5 text-[10px] font-medium text-neutral-600">
                                      {blockedReason}
                                    </span>
                                  )}
                                </span>
                                <span className="font-mono text-xs text-neutral-900">{formatAmount(candidate.total, candidate.currency)}</span>
                              </div>
                              <div className="text-xs text-neutral-400 mt-0.5">{candidate.vendor_name} · {candidate.title}</div>
                            </button>
                          )
                        })
                      )}
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setAddingPo(true)}
                    className="self-start text-[11px] font-medium text-primary-600 hover:text-primary-700 hover:underline"
                  >
                    + Add another PO to this payment
                  </button>
                )}
              </div>
            )}
          </div>

          {/* ── Step 2 — Link Invoices / GRs + PA Type ─────────────────────── */}
          <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-5">
            <h2 className="text-sm font-semibold text-neutral-800 flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-600 text-white text-[10px] font-bold">2</span>
              {isAgreementMode ? 'Link Invoices' : 'Link Invoices & Goods Receipts'}
            </h2>

            {/* Invoices multi-select */}
            <div className="flex flex-col gap-2">
              <p className="text-xs font-medium text-neutral-600 flex items-center gap-1.5">
                <FileText className="h-3.5 w-3.5" />
                {isAgreementMode
                  ? 'Invoices matched to this agreement'
                  : (isMultiPo ? 'Invoices for these POs' : 'Invoices for this PO')}
                <span className="text-neutral-400 font-normal">(select all that apply)</span>
              </p>
              {poInvoices.length === 0 ? (
                <p className="text-xs text-neutral-400 italic px-3 py-2 border border-neutral-200 rounded-lg bg-neutral-50">
                  {isAgreementMode ? 'No invoices matched to this agreement yet' : 'No invoices found for the selected PO(s) yet'}
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

            {/* GRs multi-select — PO route only; the agreement route never has one */}
            {!isAgreementMode && (
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

            )}

            {/* PO Line Items — nothing to select without a PO */}
            {!isAgreementMode && po && (
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
                      {pos.flatMap((linePo) => [
                        // With more than one PO on the payment the lines must say
                        // which order they belong to, otherwise removing a PO
                        // gives no clue which rows are about to disappear.
                        ...(isMultiPo ? [(
                          <tr key={`hdr-${linePo.id}`} className="bg-neutral-100/70">
                            <td colSpan={5} className="px-3 py-1.5">
                              <span className="font-mono text-[11px] font-semibold text-primary-700">{linePo.number}</span>
                              <span className="ml-2 text-[11px] text-neutral-500">{linePo.title}</span>
                            </td>
                          </tr>
                        )] : []),
                        ...linePo.line_items.map((line, idx) => {
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
                              {formatAmount(line.line_total, linePo.currency)}
                            </td>
                          </tr>
                        )
                        }),
                      ])}
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

            {/* PA Type — derived from the linked PO, read-only. The agreement
                route only ever creates pa_type='regular' (POST /pa 422s the
                others: prepayment/settlement/balance all need a PO to prepay
                against), so there is nothing here to show or choose. */}
            {!isAgreementMode && (
            <div className="flex flex-col gap-2">
              <p className="text-xs font-medium text-neutral-600">Payment Type</p>
              <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-3 flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-neutral-800">
                    {paType === 'prepayment' ? 'Prepayment' : 'Regular Payment'}
                  </p>
                  <p className="text-xs text-neutral-500 mt-0.5">
                    {paType === 'prepayment'
                      ? 'Advance payment before full GR/invoice'
                      : 'Standard payment against invoice / GR'}
                  </p>
                </div>
                <span className="text-xs text-neutral-400 italic">Derived from PO</span>
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
            )}
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

          {/* Attachments */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-4">
            <div>
              <h2 className="text-base font-semibold text-neutral-900">Attachments</h2>
              <p className="text-xs text-neutral-400 mt-1">Uploads and removals are saved immediately.</p>
            </div>
            <AttachmentsEditor
              inputId="pa-edit-file-upload"
              attachments={attachments}
              isUploading={uploadAttachment.isPending}
              isDeleting={deleteAttachment.isPending}
              onUpload={(file) => uploadAttachment.mutateAsync(file)}
              onDelete={(attId) => deleteAttachment.mutate(attId)}
              onDownload={(att) => { void paAttachmentService.download(id!, att.id, att.filename).catch(() => {}) }}
            />
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
            <Button variant="secondary" onClick={() => replaceTab(`/pa/${id}`)}>Cancel</Button>
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
                {isMultiPo && (
                  <div className="flex justify-between">
                    <span className="text-neutral-500">POs</span>
                    <span className="font-mono text-[11px] text-neutral-800 text-right max-w-[60%]">
                      {pos.map((p) => p.number).join(', ')}
                    </span>
                  </div>
                )}
                {po && (
                  <div className="flex justify-between">
                    <span className="text-neutral-500">PO Total</span>
                    {/* Across every PO on the payment — see PaCreatePage. */}
                    <span className="font-mono font-semibold text-neutral-900">
                      {formatAmount(pos.reduce((s, p) => s + Number(p.total), 0), po.currency)}
                    </span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-neutral-500">Invoices linked</span>
                  <span className="text-neutral-700">{selectedInvoiceIds.size}</span>
                </div>
                {!isAgreementMode && (
                  <>
                    <div className="flex justify-between">
                      <span className="text-neutral-500">GRs linked</span>
                      <span className="text-neutral-700">{selectedGrIds.size}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-neutral-500">Type</span>
                      <span className="text-neutral-700 capitalize">{paType}</span>
                    </div>
                  </>
                )}
              </div>

              {subtotalNum > 0 && (
                <div className="border-t border-neutral-200 pt-3 flex flex-col gap-1.5 text-xs">
                  <div className="flex justify-between text-neutral-500">
                    <span>Pre-tax</span>
                    <span className="font-mono">{formatAmount(subtotalNum, paCurrency)}</span>
                  </div>
                  <div className="flex justify-between text-neutral-500">
                    <span>Tax</span>
                    <span className="font-mono">{formatAmount(taxNum, paCurrency)}</span>
                  </div>
                  {shippingNum > 0 && (
                    <div className="flex justify-between text-neutral-500">
                      <span>Shipping</span>
                      <span className="font-mono">{formatAmount(shippingNum, paCurrency)}</span>
                    </div>
                  )}
                  {otherNum > 0 && (
                    <div className="flex justify-between text-neutral-500">
                      <span>Other</span>
                      <span className="font-mono">{formatAmount(otherNum, paCurrency)}</span>
                    </div>
                  )}
                  <div className="flex justify-between font-semibold text-neutral-800 border-t border-neutral-200 pt-1.5 mt-0.5">
                    <span>Total</span>
                    <span className="font-mono">{formatAmount(paymentTotal, paCurrency)}</span>
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
