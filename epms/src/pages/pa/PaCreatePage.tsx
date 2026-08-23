import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, AlertTriangle, CreditCard, Info, Package, FileText, CircleDot } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '@/components/ui/badge'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { useCreatePa, usePas, usePa } from '@/hooks/usePas'
import { paService } from '@/services/pa'
import { usePos } from '@/hooks/usePos'
import { useAuthStore } from '@/stores/auth.store'
import { useInvoices } from '@/hooks/useInvoices'
import { useGrs } from '@/hooks/useGrs'
import { useAgreement } from '@/hooks/useAgreements'
import { useRolePermissions } from '@/hooks/useConfig'
import type { ApiPo } from '@/services/po'
import type { ApiInvoice } from '@/services/invoices'
import type { DocumentStatus } from '@/types'

// Invoice multi-select list — shared by the PO route (all invoices for the PO)
// and the agreement route (matched-but-unpaid invoices only). A row already
// claimed by another open PA (`lockedIds`) renders disabled with a tag, same
// convention on both routes.
// Which agreement invoices are safe to tick FOR the operator. A house-account
// invoice is payable only once it carries evidence — the receipts it covers, or
// an explicit no-evidence settlement (epms-api pa.py::_assert_agreement_invoices
// rejects anything else with a 422). Only the receipt-backed ones are
// pre-ticked: settling without evidence is the exception channel and stays a
// deliberate act, and an invoice with neither would have been submitted straight
// into that 422. recurring / milestone invoices have no receipt concept at all,
// so nothing changes for them.
function isPreselectableAgreementInvoice(inv: ApiInvoice): boolean {
  // recurring: an invoice that never claimed a scheduled period is refused by
  // the same gate (pa.py rejects "not linked to a billing period"). It is
  // fixable — the invoice page offers the assignment — but until it is, this
  // invoice cannot be paid and must not be proposed for payment.
  if (inv.agreement_type === 'recurring') return !!inv.schedule_id
  if (inv.agreement_type !== 'house_account') return true
  return (inv.receipt_ids?.length ?? 0) > 0
}

// Says why a row is (or isn't) pre-ticked. Without it the new default reads as
// an arbitrary subset — the operator sees some boxes ticked and no reason for
// the others, which is how "the system missed one" starts.
function ReceiptEvidenceBadge({ invoice }: { invoice: ApiInvoice }) {
  const cls0 = 'shrink-0 inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium'
  if (invoice.agreement_type === 'recurring') {
    // Only worth a badge when it is the thing standing in the way — a period
    // that IS linked needs no announcement.
    return invoice.schedule_id
      ? null
      : <span className={cn(cls0, 'bg-danger-50 text-danger-700')}>No billing period</span>
  }
  if (invoice.agreement_type !== 'house_account') return null
  const n = invoice.receipt_ids?.length ?? 0
  const cls = 'shrink-0 inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium'
  if (n > 0) {
    return <span className={cn(cls, 'bg-success-50 text-success-700')}>{n} receipt{n === 1 ? '' : 's'}</span>
  }
  if (invoice.legacy_settlement) {
    return <span className={cn(cls, 'bg-warning-50 text-warning-700')}>Settled without receipts</span>
  }
  return <span className={cn(cls, 'bg-danger-50 text-danger-700')}>No receipt evidence</span>
}

function InvoiceSelectList({
  invoices, selectedIds, lockedIds, onToggle,
}: {
  invoices: ApiInvoice[]
  selectedIds: Set<string>
  lockedIds: Set<string>
  onToggle: (id: string) => void
}) {
  return (
    <div className="rounded-lg border border-neutral-200 divide-y divide-neutral-100">
      {invoices.map((inv) => {
        const isLocked = lockedIds.has(inv.id)
        return (
          <label
            key={inv.id}
            className={cn(
              'flex items-center gap-3 px-4 py-3 transition-colors',
              isLocked
                ? 'cursor-not-allowed bg-neutral-50 opacity-60'
                : 'cursor-pointer hover:bg-primary-50',
              !isLocked && selectedIds.has(inv.id) && 'bg-primary-50'
            )}
          >
            <input
              type="checkbox"
              checked={selectedIds.has(inv.id)}
              onChange={() => !isLocked && onToggle(inv.id)}
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
                  <ReceiptEvidenceBadge invoice={inv} />
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
  )
}

export default function PaCreatePage() {
  const replaceTab = useReplaceTab(epmsRoutes)
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

  // ── Agreement mode ──────────────────────────────────────────────────────────
  // Deep-linked from the agreement detail page or an agreement-matched invoice:
  // ?agreement_id=<agreement>. No PO, no GR, no receipt gate/override on this
  // route — the agreement is the authorization and the linked invoice(s) are
  // the only evidence of real spend (Phase 1A has no pickup receipts).
  const agreementIdFromUrl = searchParams.get('agreement_id') ?? ''
  const isAgreementMode = Boolean(agreementIdFromUrl)
  const { data: agreement, isError: agreementLoadError } = useAgreement(agreementIdFromUrl)

  // ── Step 1 — PO selection ──────────────────────────────────────────────────
  // A Task Inbox "Create PA" task deep-links ?poId=<po>. Pre-select that PO and
  // present it as already chosen (with a Change affordance) so the user isn't
  // asked to pick it again. Settle mode (?settleFrom=) keeps its own locked PO.
  const poIdFromUrl = searchParams.get('poId') ?? ''
  const [poSearch, setPoSearch]         = useState('')
  const [selectedPoId, setSelectedPoId] = useState(poIdFromUrl)
  const [changingPo, setChangingPo]     = useState(false)
  const fromTask = Boolean(poIdFromUrl) && !lockedFromSettle
  // Tracks the PO we've already applied matched-invoice/GR defaults for, so the
  // auto-selection runs once per PO and never re-checks boxes the user cleared.
  const autoSelectedForPoRef = useRef<string | null>(null)
  // Same idea for agreement mode (no GR side-effect there — there is never a GR).
  const autoSelectedForAgreementRef = useRef<string | null>(null)

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
  const [receiptOverride, setReceiptOverride]             = useState(false)
  const [receiptOverrideReason, setReceiptOverrideReason] = useState('')

  // ── Derived ────────────────────────────────────────────────────────────────
  const myAuthz = useRolePermissions().data
  const perms = myAuthz?.permissions
  // A plain requester may only pay against POs linked to a PR they raised — even
  // if a special role assignment (e.g. finance_bp via Role Management) widened
  // their backend PO scope to all POs — UNLESS they hold a role that authorises
  // paying on someone else's behalf (see epms-api pa.py::_may_create_pa_on_behalf,
  // which this list MUST stay in step with, or the picker offers POs the POST
  // rejects / hides POs it would accept). Privileged roles keep full visibility.
  //
  //   procurement_officer — any PO, company-wide.
  //   dept_admin          — their own department's requisitions. Dropping the
  //     own-requisition filter is right for them because the PO list the API
  //     returns to a dept_admin is ALREADY department-scoped server-side
  //     (access_scope.visible_po_subquery via visible_pr_subquery's dept branch),
  //     so what is left is what the backend accepts. The one residual gap is a PO
  //     pulled in by the scope's task-chain OR-condition from another department;
  //     that one still 403s on submit rather than silently paying.
  const ON_BEHALF_ROLES = ['procurement_officer', 'dept_admin']
  const requesterScoped =
    user?.role === 'requester' &&
    !myAuthz?.roles?.some((r) => ON_BEHALF_ROLES.includes(r))
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

  const { data: invoicesData } = useInvoices(
    selectedPoId
      ? { po_id: selectedPoId }
      : isAgreementMode
        ? { agreement_id: agreementIdFromUrl }
        : undefined
  )
  // GRs never exist on the agreement route — skip the fetch entirely there.
  const { data: grsData } = useGrs(selectedPoId ? { po_id: selectedPoId } : undefined, !isAgreementMode)
  const { data: poActivePas } = usePas(selectedPoId ? { po_id: selectedPoId } : undefined, !isAgreementMode)
  // GET /pa has no agreement_id filter — narrow server-side via `search` on the
  // agreement number (matches PaymentApplication.agreement_number ilike), then
  // re-filter client-side on the real agreement_id column below.
  const { data: agreementActivePas } = usePas(
    agreement ? { search: agreement.number } : undefined,
    isAgreementMode && Boolean(agreement),
  )
  const docInvoices = invoicesData?.items ?? []
  // "Matched-but-unpaid" invoices for the agreement route's invoice picker.
  const agreementInvoiceCandidates = docInvoices.filter((inv) => inv.status === 'matched')

  // Receipt gate — a non-prepayment PA normally requires a matched invoice backed
  // by a goods receipt. Finance-authorized users can override with a reason.
  // The agreement route has no goods receipt, ever (Phase 1A has no pickup
  // receipts) — the matched invoice(s) required below are the only evidence, so
  // this gate and its override never apply there.
  const hasThreeWay = docInvoices.some(
    (inv) => inv.status === 'matched' && (!!inv.gr_id || (inv.gr_ids?.length ?? 0) > 0),
  )
  const isPrepayment = paType === 'prepayment'
  const canOverride = user?.role === 'system_admin' || !!perms?.pa_override_receipt
  const receiptBlocked = !isAgreementMode && !isPrepayment && !hasThreeWay
  const receiptOverrideMissing = receiptBlocked && (!canOverride || !receiptOverride || !receiptOverrideReason.trim())

  // settlement/balance「Original Prepayment PA」下拉数据源:本 PO 下未作废的预付 PA
  // (PO-only — agreement mode only ever creates pa_type='regular')
  const linkablePrepayments = (poActivePas?.items ?? []).filter(
    (p) => p.pa_type === 'prepayment' && !['cancelled', 'rejected'].includes(p.status)
  )

  // Invoices already claimed by an active PA (not cancelled/rejected) — on this
  // PO in PO mode, or on this agreement in agreement mode.
  const agreementPaItems = (agreementActivePas?.items ?? []).filter(
    (pa) => pa.agreement_id === agreementIdFromUrl
  )
  const lockedInvoiceIds = new Set<string>(
    (isAgreementMode ? agreementPaItems : (poActivePas?.items ?? []))
      .filter((pa) => !['cancelled', 'rejected'].includes(pa.status))
      .flatMap((pa) => pa.invoice_ids)
  )
  const poGrs = (grsData?.items ?? []).filter((g) => g.status !== 'cancelled')

  // Auto-subtotal from selected PO lines
  const selectedPoLines = selectedPo ? selectedPo.line_items.filter((l) => selectedLineIds.has(l.id)) : []
  const autoSubtotal = selectedPoLines.reduce((s, l) => s + Number(l.line_total), 0)

  // Selected-invoice aggregates. The PA pays these invoices, so their real
  // pre-tax/tax are the source of truth for the charge breakdown — a PO whose
  // snapshotted tax_rate is 0/null (e.g. a freight vendor set up without a rate)
  // would otherwise auto-fill 0 tax even when the matched invoice carries HST.
  const selectedInvoices  = docInvoices.filter((inv) => selectedInvoiceIds.has(inv.id))
  const hasLinkedInvoices = selectedInvoices.length > 0
  const invoiceSubtotal   = selectedInvoices.reduce((s, inv) => s + Number(inv.amount), 0)
  const invoiceTax        = selectedInvoices.reduce((s, inv) => s + Number(inv.tax_amount), 0)

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

  // The prepayment PA this settlement reconciles — from the manual dropdown or the
  // Settle deep-link source. Its payment_amount is the true ceiling for how much
  // prepayment can be applied (you can't apply more than was actually prepaid).
  const selectedPrepaymentPa =
    linkablePrepayments.find((p) => p.id === prepaymentPaId) ??
    (sourcePrepay?.id === prepaymentPaId ? sourcePrepay : undefined)
  const prepaidAmount = Number(selectedPrepaymentPa?.payment_amount ?? 0)
  // Net payable = 全额 − 预付抵扣(余款);非 settlement 类型即全额
  const netPayable   = isSettlementType ? Math.max(grossTotal - appliedNum, 0) : grossTotal
  const isOverpaid   = isSettlementType && grossTotal - appliedNum < -0.01
  // net==0 settlement = pure reconciliation: no cash, no bank, no standard approval
  const isReconcileOnly  = isSettlementType && grossTotal > 0 && netPayable === 0

  // Tax follows the document being paid — the PO snapshotted its rate/code from
  // Finance Tax Settings (mdm-api) for the PO route; the agreement carries its
  // own tax_code/tax_rate for the agreement route (same mdm-api origin, set at
  // agreement creation). Falls back to 13% only when neither has a rate recorded.
  const paTaxRate = isAgreementMode
    ? (agreement?.tax_rate != null ? Number(agreement.tax_rate) : 0.13)
    : (selectedPo ? Number(selectedPo.tax_rate) : 0.13)
  const paTaxCode = isAgreementMode ? (agreement?.tax_code ?? null) : (selectedPo?.tax_code ?? null)
  // Currency for every amount on this form — the PO's currency in PO mode, the
  // agreement's in agreement mode.
  const formCurrency = isAgreementMode ? (agreement?.currency ?? 'CAD') : (selectedPo?.currency ?? 'CAD')
  const contextSelected = isAgreementMode ? Boolean(agreement) : Boolean(selectedPo)
  // Consumed / Not-to-Exceed for the agreement summary card — both arrive as
  // JSON strings, Number()-coerced here before any arithmetic/comparison. This
  // ceiling only warns; it is never checked in validation/handleSubmit below.
  const agreementConsumed = agreement ? Number(agreement.consumed_amount) : 0
  const agreementCeiling = agreement?.not_to_exceed ? Number(agreement.not_to_exceed) : null
  const agreementOverCeiling = agreementCeiling !== null && agreementConsumed > agreementCeiling

  // Reset when PO changes. Skipped in Settle mode — there the source-prepayment
  // effect below owns PO/type/prepayment prefill and the PO is locked.
  useEffect(() => {
    if (lockedFromSettle || isAgreementMode) return
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
    setSelectedPoId(sourcePrepay.po_id ?? '')
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

    const matchedInvoices = docInvoices.filter(
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

  // Agreement mode's equivalent: pre-select the agreement's matched-but-unpaid
  // invoices (status 'matched', not already claimed by another open PA on this
  // agreement) THAT ARE ACTUALLY READY TO PAY. No GR side-effect — there is
  // never a GR on this route.
  //
  // The readiness half is isPreselectableAgreementInvoice: this used to tick
  // every matched invoice on the agreement, including house-account ones with
  // no receipt attached — which the backend then refuses (422), and which is
  // exactly the wrong default anyway. Ticking an invoice here proposes paying
  // it; proposing payment for something with no evidence behind it is a
  // decision, not a default.
  useEffect(() => {
    if (!isAgreementMode || !agreementIdFromUrl) return
    if (autoSelectedForAgreementRef.current === agreementIdFromUrl) return
    if (invoicesData === undefined || agreementActivePas === undefined) return

    const matchedInvoices = docInvoices.filter(
      (inv) => inv.status === 'matched' && !lockedInvoiceIds.has(inv.id) && isPreselectableAgreementInvoice(inv)
    )
    autoSelectedForAgreementRef.current = agreementIdFromUrl
    if (matchedInvoices.length > 0) setSelectedInvoiceIds(new Set(matchedInvoices.map((i) => i.id)))
  }, [isAgreementMode, agreementIdFromUrl, invoicesData, agreementActivePas]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-fill title when a PO or agreement is selected (if not yet typed).
  // Also depends on selectedPo?.id / agreement?.id so it re-runs when React
  // Query data loads after navigating here via ?poId= / ?agreement_id=.
  useEffect(() => {
    if (title) return
    if (!isAgreementMode && selectedPo) {
      setTitle(`Payment — ${selectedPo.vendor_name} ${selectedPo.number}`)
    } else if (isAgreementMode && agreement) {
      setTitle(`Payment — ${agreement.vendor_name} ${agreement.number}`)
    }
  }, [isAgreementMode, selectedPoId, selectedPo?.id, agreement?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-fill pre-tax: prefer the linked invoices' real pre-tax; fall back to the
  // selected PO lines when the PA links no invoice (GR-only / manual payment).
  useEffect(() => {
    const auto = hasLinkedInvoices ? invoiceSubtotal : autoSubtotal
    if (auto > 0) {
      setSubtotal(String(Number(auto.toFixed(2))))
    }
  }, [hasLinkedInvoices, invoiceSubtotal, autoSubtotal]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-fill tax. When invoices are linked, use their real tax — authoritative
  // and survives a PO whose snapshotted tax_rate is 0/null. Otherwise fall back
  // to the PO's tax rate × pre-tax for CAD. Skipped once the user edits tax.
  useEffect(() => {
    if (taxManuallyEdited) return
    if (hasLinkedInvoices) {
      setTaxAmount(invoiceTax.toFixed(2))
    } else if (formCurrency === 'CAD' && subtotalNum > 0) {
      setTaxAmount((subtotalNum * paTaxRate).toFixed(2))
    } else {
      setTaxAmount('')
    }
  }, [hasLinkedInvoices, invoiceTax, subtotalNum, formCurrency, paTaxRate, taxManuallyEdited]) // eslint-disable-line react-hooks/exhaustive-deps

  // Validation
  const errors: string[] = []
  if (submitted) {
    if (isAgreementMode) {
      if (!agreement) errors.push('Agreement not found')
      if (agreement && selectedInvoiceIds.size === 0)
        errors.push('Select at least one invoice matched to this agreement')
      if (!title.trim()) errors.push('PA Title is required')
      if (subtotalNum <= 0) errors.push('Pre-tax amount must be greater than zero')
      if (taxNum < 0) errors.push('Tax amount cannot be negative')
      if (netPayable <= 0) errors.push('Total payment amount must be greater than zero')
    } else {
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
      if (receiptOverrideMissing)
        errors.push(
          canOverride
            ? 'Check the override box and provide a reason to proceed without a goods receipt'
            : 'No goods receipt linked to a matched invoice — create a Goods Receipt first',
        )
    }
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

  // Everything that happens AFTER POST /pa returns 201. Split out because the
  // failure mode it handles bit a real user: the PA is created first and
  // submitted second, and when the second call failed (approval-api down) the
  // catch below swallowed it — no message, no navigation, the form still armed.
  // The operator, seeing nothing happen, clicked again; the second attempt was
  // refused ("already claimed by an active PA") and the real PA sat in `draft`
  // where nobody was looking for it.
  //
  // So: the created document is ALWAYS navigated to. Submission is the step
  // that may fail, and when it does the operator lands on the PA that exists —
  // which carries its own Submit action — with the reason stated.
  const finishCreate = async (newPa: { id: string; status: string }) => {
    let submitError: string | null = null
    try {
      if (newPa.status === 'draft') {
        await paService.action(newPa.id, { action: 'submit' })
      }
    } catch (err) {
      submitError = err instanceof Error ? err.message : 'Unknown error'
    }
    await queryClient.invalidateQueries({ queryKey: ['pas'] })
    if (isAgreementMode) await queryClient.invalidateQueries({ queryKey: ['agreements'] })
    if (submitError) {
      alert(
        `Payment application created, but it could not be submitted for approval:

${submitError}

` +
        'It has been saved as a draft — open it and use Submit to try again.'
      )
      replaceTab(`/pa/${newPa.id}`)
      return
    }
    replaceTab('/pa')
  }

  const handleSubmit = async () => {
    setSubmitted(true)
    // A second click while the first request is still open creates a SECOND
    // payment application for the same invoices.
    if (createPa.isPending) return
    if (isAgreementMode) {
      if (!agreement) return
      if (!title.trim() || subtotalNum <= 0 || taxNum < 0) return
      if (selectedInvoiceIds.size === 0) return
      if (netPayable <= 0) return
      try {
        const newPa = await createPa.mutateAsync({
          title: title.trim(),
          pa_type: 'regular',
          currency: agreement.currency,
          subtotal: subtotalNum,
          tax_amount: taxNum,
          tax_code: taxNum > 0 ? paTaxCode ?? undefined : null,
          tax_rate: taxNum > 0
            ? (paTaxRate > 0 ? paTaxRate : subtotalNum > 0 ? Number((taxNum / subtotalNum).toFixed(4)) : null)
            : null,
          shipping_amount: shippingNum || undefined,
          other_charges: otherNum || undefined,
          other_charges_note: otherNum > 0 ? otherChargesNote.trim() || undefined : undefined,
          vendor_id: agreement.vendor_id,
          vendor_name: agreement.vendor_name,
          agreement_id: agreement.id,
          invoice_ids: Array.from(selectedInvoiceIds),
          notes: notes.trim() || undefined,
        })
        await finishCreate(newPa)
      } catch {
        // POST /pa itself failed — surfaced by the mutation's error state below.
      }
      return
    }
    if (!selectedPo) return
    if (!title.trim() || subtotalNum <= 0 || taxNum < 0) return
    if (!isSettlementType && netPayable <= 0) return
    if (isSettlementType && (appliedNum > grossTotal + 0.01)) return
    if (paType === 'prepayment' && (!prepaymentPct || !expectedSettlement)) return
    if (isSettlementType && !prepaymentPaId) return
    if (receiptOverrideMissing) return
    try {
      const paLineItems = selectedPo.line_items
        .filter((l) => selectedLineIds.has(l.id))
        .map((l) => ({
          po_line_id: l.id,
          description: l.description,
          qty: Number(l.qty),
          unit: l.unit,
          unit_price: Number(l.unit_price),
        }))
      const newPa = await createPa.mutateAsync({
        title: title.trim(),
        pa_type: paType,
        currency: selectedPo.currency,
        subtotal: subtotalNum,
        tax_amount: taxNum,
        tax_code: taxNum > 0 ? paTaxCode ?? undefined : null,
        // Keep the rate snapshot consistent with the actual tax: use the PO rate
        // when it's usable, otherwise derive it from the invoice-driven amounts so
        // we never persist tax_amount > 0 alongside a 0 rate.
        tax_rate: taxNum > 0
          ? (paTaxRate > 0 ? paTaxRate : subtotalNum > 0 ? Number((taxNum / subtotalNum).toFixed(4)) : null)
          : null,
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
        // Cap applied at the actual prepaid amount: you can't apply more prepayment
        // than the source prepayment PA was worth. Any shortfall (final > prepaid)
        // is the remaining balance to pay; any excess prepaid is the overpayment,
        // detected backend-side and routed to a credit-note confirmation.
        prepayment_applied: isSettlementType ? (Math.min(appliedNum, prepaidAmount) || undefined) : undefined,
        line_items: paLineItems,
        notes: notes.trim() || undefined,
        receipt_override: receiptBlocked && receiptOverride,
        receipt_override_reason: receiptBlocked && receiptOverride ? receiptOverrideReason.trim() : null,
      })
      // net==0 settlements are finalized by the backend at creation (auto-reconciled
      // or queued for finance confirmation) and are no longer 'draft' — only submit
      // documents that still need the standard approval flow. finishCreate holds
      // that rule, and the recovery when the submit half fails.
      await finishCreate(newPa)
    } catch {
      // POST /pa itself failed — surfaced by the mutation's error state below.
    }
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button onClick={() => replaceTab('/pa')} className="p-2 rounded-lg hover:bg-neutral-100 text-neutral-500">
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">New Payment Application</h1>
          <p className="text-sm text-neutral-500 mt-0.5">
            {isAgreementMode ? 'Create a payment request against an agreement' : 'Create a payment request linked to a PO'}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Main form */}
        <div className="col-span-2 flex flex-col gap-5">

          {/* ── Step 1 — Select PO / Agreement ─────────────────────────────── */}
          {isAgreementMode ? (
            <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-4">
              <h2 className="text-sm font-semibold text-neutral-800 flex items-center gap-2">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-600 text-white text-[10px] font-bold">1</span>
                Agreement
              </h2>
              {agreementLoadError ? (
                <div className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-3 flex items-start gap-2">
                  <AlertTriangle className="h-4 w-4 text-danger-600 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm font-medium text-danger-800">Could not load this agreement.</p>
                    <p className="text-xs text-danger-700 mt-0.5">
                      It may not exist, or you may not have permission to view it. Check the link and try again.
                    </p>
                  </div>
                </div>
              ) : !agreement ? (
                <p className="px-3 py-4 text-xs text-neutral-400 text-center">Loading agreement…</p>
              ) : (
                <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs font-semibold text-primary-700">{agreement.number}</span>
                    <StatusBadge status={agreement.status as DocumentStatus} />
                  </div>
                  <div className="text-sm text-neutral-700 mt-0.5">{agreement.title}</div>
                  <div className="text-xs text-neutral-400 mt-0.5">{agreement.vendor_name}</div>
                  <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-neutral-500">
                    <span>
                      Consumed {formatAmount(agreementConsumed, agreement.currency)}
                      {agreementCeiling !== null ? ` / NTE ${formatAmount(agreementCeiling, agreement.currency)}` : ' / No ceiling'}
                    </span>
                    {agreementOverCeiling && (
                      <span className="inline-flex items-center gap-1 font-medium text-warning-700">
                        <AlertTriangle className="h-3 w-3" /> Over ceiling — warning only
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-neutral-400 mt-2">
                    No purchase order and no goods receipt on this route — the agreement is the
                    authorization and the matched invoice(s) below are the evidence.
                  </p>
                </div>
              )}
            </div>
          ) : (
          <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-4">
            <h2 className="text-sm font-semibold text-neutral-800 flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-600 text-white text-[10px] font-bold">1</span>
              {lockedFromSettle || (fromTask && !changingPo) ? 'Purchase Order' : 'Select Purchase Order'}
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
            ) : fromTask && !changingPo && (selectedPo || posData === undefined) ? (
              selectedPo ? (
                <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs font-semibold text-primary-700">{selectedPo.number}</span>
                    <span className="font-mono text-xs font-semibold text-neutral-900">{formatAmount(selectedPo.total, selectedPo.currency)}</span>
                  </div>
                  <div className="text-sm text-neutral-700 mt-0.5">{selectedPo.title}</div>
                  <div className="text-xs text-neutral-400 mt-0.5">{selectedPo.vendor_name}</div>
                  <div className="mt-2 flex items-center justify-between">
                    <p className="text-[11px] text-neutral-400">Pre-filled from the linked PO.</p>
                    <button
                      type="button"
                      onClick={() => setChangingPo(true)}
                      className="text-[11px] font-medium text-primary-600 hover:text-primary-700 hover:underline"
                    >
                      Change PO
                    </button>
                  </div>
                </div>
              ) : (
                <p className="px-3 py-4 text-xs text-neutral-400 text-center">Loading purchase order…</p>
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
          )}

          {/* ── Step 2 — Link Invoices / GRs + PA Type (PO mode only) ──────── */}
          {!isAgreementMode && selectedPo && (
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
                {docInvoices.length === 0 ? (
                  <p className="text-xs text-neutral-400 italic px-3 py-2 border border-neutral-200 rounded-lg bg-neutral-50">
                    No invoices found for this PO yet
                  </p>
                ) : (
                  <InvoiceSelectList
                    invoices={docInvoices}
                    selectedIds={selectedInvoiceIds}
                    lockedIds={lockedInvoiceIds}
                    onToggle={toggleInvoice}
                  />
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

              {/* PA Type — the choices follow the PO's prepayment flag: a prepaid
                  PO can only be paid via prepayment/settlement, a standard PO only
                  via a regular payment. Type is never a free-form user choice. */}
              <div className="flex flex-col gap-2">
                <p className="text-xs font-medium text-neutral-600">Payment Type</p>
                <div className="grid grid-cols-2 gap-3">
                  {(selectedPo.is_prepaid
                    ? [
                        { value: 'prepayment' as const,  label: 'Prepayment',       desc: 'Advance payment before full GR/invoice' },
                        { value: 'settlement' as const,  label: 'Settlement',       desc: 'Reconcile a prepayment; pays only the remaining balance' },
                      ]
                    : [
                        { value: 'regular' as const,     label: 'Regular Payment',  desc: 'Standard payment against invoice / GR' },
                      ]
                  ).map((opt) => (
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

                {isSettlementType && !isAgreementMode && (
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

          {/* ── Step 2 (agreement mode) — Link Invoices only ────────────────── */}
          {isAgreementMode && (
            <div className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm flex flex-col gap-4">
              <h2 className="text-sm font-semibold text-neutral-800 flex items-center gap-2">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-600 text-white text-[10px] font-bold">2</span>
                Link Invoices
              </h2>
              <div className="flex flex-col gap-2">
                <p className="text-xs font-medium text-neutral-600 flex items-center gap-1.5">
                  <FileText className="h-3.5 w-3.5" /> Invoices matched to this agreement
                  <span className="text-neutral-400 font-normal">(select all that apply)</span>
                </p>
                <p className="text-[11px] text-neutral-400">
                  {agreement?.agreement_type === 'house_account' ? (
                    <>
                      No purchase order and no goods receipt on this route — the receipts attached
                      to an invoice are its evidence. Only unpaid invoices that already carry
                      receipts are ticked for you; an invoice settled without receipt evidence can
                      still be paid, but tick it deliberately. There is no PO-line selection to make.
                    </>
                  ) : (
                    <>
                      No purchase order and no goods receipt on this route — the matched invoice(s)
                      are the evidence. There is no PO-line or goods-receipt selection to make.
                    </>
                  )}
                </p>
                {agreementInvoiceCandidates.length === 0 ? (
                  <p className="text-xs text-neutral-400 italic px-3 py-2 border border-neutral-200 rounded-lg bg-neutral-50">
                    No unpaid invoices matched to this agreement yet.
                  </p>
                ) : (
                  <InvoiceSelectList
                    invoices={agreementInvoiceCandidates}
                    selectedIds={selectedInvoiceIds}
                    lockedIds={lockedInvoiceIds}
                    onToggle={toggleInvoice}
                  />
                )}
              </div>
            </div>
          )}

          {/* ── Step 3 — Charge Breakdown & Details (shared by both modes) ─── */}
          {contextSelected && (
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
                <p className="text-xs font-semibold text-neutral-600 uppercase tracking-wide">Charge Breakdown ({formCurrency})</p>

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
                      {!taxManuallyEdited && hasLinkedInvoices && (
                        <span className="ml-1.5 text-[10px] font-normal text-neutral-400">from invoice</span>
                      )}
                      {!taxManuallyEdited && !hasLinkedInvoices && formCurrency === 'CAD' && subtotalNum > 0 && (
                        <span className="ml-1.5 text-[10px] font-normal text-neutral-400">{`${(paTaxRate * 100).toFixed(0)}% HST`}</span>
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
                        <span className="font-mono">{formatAmount(grossTotal, formCurrency)}</span>
                      </div>
                      <div className="flex items-center justify-between text-xs text-neutral-500">
                        <span>Prepayment applied</span>
                        <span className="font-mono">−{formatAmount(appliedNum, formCurrency)}</span>
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
                      {netPayable > 0 ? formatAmount(netPayable, formCurrency) : formatAmount(0, formCurrency)}
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

          {/* Receipt gate — no matched, GR-backed invoice on this PO */}
          {receiptBlocked && (
            <div className="rounded-md border border-warning-300 bg-warning-50 px-4 py-3 flex flex-col gap-2">
              <p className="text-sm font-medium text-warning-800">No goods receipt linked to a matched invoice yet.</p>
              <p className="text-xs text-warning-700">
                A Payment Application normally requires goods to be received. Create a Goods Receipt first.
              </p>
              {canOverride && (
                <label className="flex items-start gap-2 text-xs text-warning-800">
                  <input
                    type="checkbox"
                    checked={receiptOverride}
                    onChange={(e) => setReceiptOverride(e.target.checked)}
                    className="mt-0.5"
                  />
                  <span>Override — proceed without goods receipt (a reason is required).</span>
                </label>
              )}
              {canOverride && receiptOverride && (
                <textarea
                  rows={2}
                  value={receiptOverrideReason}
                  onChange={(e) => setReceiptOverrideReason(e.target.value)}
                  placeholder="Reason for override"
                  className="px-3 py-2 rounded-lg border border-warning-300 text-xs focus:outline-none focus:ring-2 focus:ring-primary-600 resize-none"
                />
              )}
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
            <Button variant="secondary" onClick={() => replaceTab('/pa')}>Cancel</Button>
            <Button
              onClick={handleSubmit}
              disabled={!contextSelected || receiptOverrideMissing || createPa.isPending}
              className="gap-2"
            >
              <CreditCard className="h-4 w-4" />
              {createPa.isPending
                ? 'Creating…'
                : isReconcileOnly ? 'Reconcile Prepayment' : 'Submit Payment Application'}
            </Button>
          </div>
        </div>

        {/* ── Sidebar — Payment Summary ───────────────────────────────────────── */}
        <div className="flex flex-col gap-4">
          <div className="rounded-xl border border-neutral-200 bg-white p-4 shadow-sm sticky top-6">
            <p className="text-xs font-semibold text-neutral-600 uppercase tracking-wide mb-3">Payment Summary</p>

            {!contextSelected ? (
              <p className="text-xs text-neutral-400 text-center py-4">
                {isAgreementMode
                  ? (agreementLoadError ? 'Agreement failed to load.' : 'Loading agreement…')
                  : 'Select a PO to see summary'}
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="flex flex-col gap-1.5 text-xs">
                  <div className="flex justify-between">
                    <span className="text-neutral-500">Vendor</span>
                    <span className="text-neutral-800 font-medium text-right max-w-[60%]">
                      {isAgreementMode ? agreement?.vendor_name : selectedPo?.vendor_name}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-neutral-500">{isAgreementMode ? 'Agreement' : 'PO Total'}</span>
                    <span className="font-mono font-semibold text-neutral-900">
                      {isAgreementMode ? agreement?.number : formatAmount(selectedPo?.total ?? 0, formCurrency)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-neutral-500">Invoices linked</span>
                    <span className="text-neutral-700">{selectedInvoiceIds.size}</span>
                  </div>
                  {!isAgreementMode && (
                    <div className="flex justify-between">
                      <span className="text-neutral-500">GRs linked</span>
                      <span className="text-neutral-700">{selectedGrIds.size}</span>
                    </div>
                  )}
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
                      <span className="font-mono">{formatAmount(subtotalNum, formCurrency)}</span>
                    </div>
                    <div className="flex justify-between text-neutral-500">
                      <span>Tax</span>
                      <span className="font-mono">{formatAmount(taxNum, formCurrency)}</span>
                    </div>
                    {shippingNum > 0 && (
                      <div className="flex justify-between text-neutral-500">
                        <span>Shipping</span>
                        <span className="font-mono">{formatAmount(shippingNum, formCurrency)}</span>
                      </div>
                    )}
                    {otherNum > 0 && (
                      <div className="flex justify-between text-neutral-500">
                        <span>Other</span>
                        <span className="font-mono">{formatAmount(otherNum, formCurrency)}</span>
                      </div>
                    )}
                    {isSettlementType && appliedNum > 0 && (
                      <div className="flex justify-between text-neutral-500">
                        <span>Prepayment applied</span>
                        <span className="font-mono">−{formatAmount(appliedNum, formCurrency)}</span>
                      </div>
                    )}
                    <div className="flex justify-between font-semibold text-neutral-800 border-t border-neutral-200 pt-1.5 mt-0.5">
                      <span>{isSettlementType ? 'Net Payable' : 'Total'}</span>
                      <span className="font-mono">{formatAmount(netPayable, formCurrency)}</span>
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
