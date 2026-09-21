/**
 * AP Subledger Health — which supplier balances in NC can be believed.
 *
 * Once only APPROVED documents are counted, NC's subledger is healthy: 143 CAD
 * supplier positions where open equals billed-minus-paid to the cent, against
 * 3 that differ by 32,988.93 in total.
 *
 * 487 CAD documents NC never approved sit outside that, carrying 33,513,491.02.
 * They are not work: 478 have no payment, and the 31 that appear to have one
 * are paired with payment documents that are themselves unapproved drafts —
 * zero approved payment lines against any of them. So the page does not lead
 * with them. It states how much it excluded and why, and keeps them one click
 * away, because a quietly dropped 33.5M becomes "why does this not match NC"
 * six months later.
 *
 * This page does NOT decide who is right. It cannot: billed-minus-paid is not a
 * truth either — a supplier paid beyond what was billed is holding a prepayment
 * or an unapplied credit, which is normal. So the page reports that NC's own two
 * figures disagree, by how much, and then hands over the documents behind the
 * gap so finance can judge each supplier.
 *
 * Currencies are never summed together. This company transacts in CAD, USD, CNY
 * and EUR, and one combined figure would mean nothing.
 */
import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Download, EyeOff, Loader2, Undo2, X } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

// Shared by the three subtotal rows in the supplier view.
const footCell = 'px-2.5 py-2 text-right font-mono tabular-nums font-semibold text-neutral-800'

interface Side {
  suppliers: number; subledger_open: string; billed_minus_paid?: string; gap: string
  ignored_bal: string; ignored_bills: number; open_after_ignored: string
}
interface Abandoned { bills: number; money_bal: string; superseded_bills: number; superseded_bal: string }
/** What finance has judged not to be real debt, across both health states. */
interface Ignored { bills: number; money_bal: string }
interface CcyRow {
  currency: string; consistent: Side; inconsistent: Side
  ignored: Ignored; abandoned: Abandoned
}
interface SummaryResp { currencies: CcyRow[] }

interface SupplierRow {
  supplier_code: string | null
  supplier_name: string | null
  currency: string
  health: string
  billed: string
  paid: string
  subledger_open: string
  billed_minus_paid: string
  gap: string
  ignored_bal: string
  ignored_bills: number
  /** NC's open balance less what finance set aside — what is actually left. */
  open_after_ignored: string
}
interface ItemsResp { total: number; items: SupplierRow[] }

interface OpenBill {
  bill_no: string; bill_date: string | null; bill_year: string | null
  money_cr: string | null; money_bal: string | null
  /** What NC's own payment documents say was already paid against THIS bill. */
  paid_against: string | null
  /** How many approved payment lines point at this bill. */
  payment_lines: number
  invoice_no: string | null; purchase_order: string | null; trade_type: string | null
  /** Set when finance took this bill off the working list. */
  dismissed: { reason: string; note: string | null; by: string | null; at: string | null } | null
}
interface PayLine {
  bill_no: string; pay_date: string | null; doc_date: string | null; bill_year: string | null
  money_de: string | null
  applied_to_bill_no: string | null
  applied_bill_still_open: string | null
  /** The payable this cleared is STILL flagged open — paid but never applied. */
  applied_bill_is_open: boolean
  scomment: string | null
}
interface BillsTotal {
  bills: number; money_cr: string; money_bal: string; paid_against: string
  ignored_bills: number; ignored_bal: string; money_bal_after_ignored: string
}
interface DismissReason { code: string; label: string }
interface PaymentsTotal { lines: number; money_de: string }
interface DetailResp {
  supplier_code: string; currency: string
  open_bills_total: BillsTotal; payments_total: PaymentsTotal
  open_bills: OpenBill[]; payments: PayLine[]
}

interface AbandonedRow {
  bill_no: string; currency: string; bill_date: string | null; bill_year: string | null
  bill_status: number | null; approve_status: number | null
  supplier_code: string | null; supplier_name: string | null; invoice_no: string | null
  money_cr: string; money_bal: string; superseded: boolean
}
interface AbandonedResp { total: number; items: AbandonedRow[] }

interface GlLine {
  same_currency: boolean
  /** Shared by both halves of a cancelling pair; null when the line is real. */
  offset_group: string | null
  jv_number: string; voucher_date: string | null; fiscal_period: string | null
  subsystem: string | null; account_code: string; summary: string | null
  debit: string | null; credit: string | null; currency: string | null
  matches_gap: boolean
}
interface GlTotal {
  currency: string | null; same_currency: boolean; lines: number
  debit: string; credit: string; net: string; net_matches_gap: boolean
  lines_after_offsets: number; net_after_offsets: string
  net_after_offsets_matches_gap: boolean
}
interface GlResp {
  total: number; shown: number; matching: number; matching_other_currency: number
  offset_lines: number; offsets_capped: boolean
  totals: GlTotal[]; items: GlLine[]
}

function money(v: string | null | undefined) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function csvEscape(v: unknown) {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}
/** Already YYYY-MM-DD from the API — never re-parse a date-only string. */
function day(v: string | null) { return v ? v.slice(0, 10) : '—' }

/**
 * Recording why a payable is being set aside.
 *
 * The reason is mandatory and comes from the server, not from a list copied
 * into this file — the closed set lives in one place so an option can never
 * exist on screen and 422 on submit. A free-text note is optional except under
 * "Other", where a dismissal with no explanation would be unreviewable.
 */
function DismissDialog({ billNos, total, currency, onCancel, onDone }: {
  billNos: string[]; total: number; currency: string
  onCancel: () => void; onDone: (reason: string, note: string) => void
}) {
  const [reason, setReason] = useState('legacy')
  const [note, setNote] = useState('')
  const { data } = useQuery({
    queryKey: ['ap-dismiss-reasons'],
    queryFn: () => financeApi.get<{ reasons: DismissReason[] }>('/ap-ledger-health/dismiss-reasons'),
    staleTime: Infinity,
  })
  const needsNote = reason === 'other' && !note.trim()

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-6">
      <div className="w-full max-w-lg rounded-lg bg-white shadow-xl">
        <div className="border-b border-neutral-200 px-5 py-4">
          <h3 className="text-sm font-semibold text-neutral-900">
            Ignore {billNos.length} {billNos.length === 1 ? 'bill' : 'bills'}
          </h3>
          <p className="mt-0.5 text-xs text-neutral-500">
            {money(String(total))} {currency} comes off the working list. NC is not
            changed — the page keeps showing what NC says and what was set aside,
            separately, and this can be undone.
          </p>
        </div>
        <div className="space-y-3 px-5 py-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-700">Reason</label>
            <select value={reason} onChange={(e) => setReason(e.target.value)}
                    className="w-full rounded-lg border border-neutral-300 px-2.5 py-1.5 text-sm">
              {(data?.reasons ?? []).map((r) => (
                <option key={r.code} value={r.code}>{r.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-700">
              Note {reason === 'other' && <span className="text-amber-700">(required)</span>}
            </label>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3}
                      placeholder="e.g. NC went live 2020, period closed, cannot be cleared there"
                      className="w-full rounded-lg border border-neutral-300 px-2.5 py-1.5 text-sm" />
          </div>
          <div className="max-h-24 overflow-auto rounded border border-neutral-200 bg-neutral-50 px-2.5 py-1.5 font-mono text-[11px] text-neutral-500">
            {billNos.join(', ')}
          </div>
        </div>
        <div className="flex justify-end gap-2 border-t border-neutral-200 px-5 py-3">
          <button onClick={onCancel}
                  className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50">
            Cancel
          </button>
          <button onClick={() => onDone(reason, note.trim())} disabled={needsNote}
                  className="rounded-lg bg-[#085E5E] px-3 py-1.5 text-sm text-white hover:bg-[#064a4a] disabled:opacity-40">
            Ignore {billNos.length}
          </button>
        </div>
      </div>
    </div>
  )
}

function SupplierDetail({ code, name, currency, gap, onClose }: {
  code: string; name: string | null; currency: string; gap: string | null; onClose: () => void
}) {
  const { data, isFetching } = useQuery({
    queryKey: ['ap-ledger-supplier', code, currency],
    queryFn: () => financeApi.get<DetailResp>(
      `/ap-ledger-health/supplier?supplier_code=${encodeURIComponent(code)}&currency=${currency}`),
  })
  // Finance settles some differences by posting straight to the payable
  // account, skipping the payment document — invisible to the payment-side
  // comparison, which is why those gaps come out negative. Only fetched when
  // there is a gap to explain.
  const hasGap = gap !== null && Number(gap) !== 0
  const { data: gl } = useQuery({
    queryKey: ['ap-ledger-gl', name, currency, gap],
    queryFn: () => financeApi.get<GlResp>(
      `/ap-ledger-health/gl-clearing?supplier_name=${encodeURIComponent(name ?? '')}` +
      `&currency=${currency}&gap=${encodeURIComponent(gap ?? '')}&limit=400`),
    enabled: hasGap && !!name,
  })
  // Most of what sits on a payable account cancels itself out — a voucher and
  // its reversal, or two legs of one entry. Hidden by default because that is
  // the complaint this answers, but the count is always on screen and one
  // click brings them back, dimmed. Never silently dropped.
  const [hideOffsets, setHideOffsets] = useState(true)
  // Click a bill or a payment to light up its counterpart. The link exists in
  // NC (a payment carries the payable it cleared) but nothing on a flat pair of
  // tables shows it.
  const [linkedBill, setLinkedBill] = useState<string | null>(null)
  // Same idiom as the offsets toggle below: noise is hidden by default, the
  // count is always on screen, and one click brings it back dimmed. Nothing
  // here is ever silently dropped.
  const [hideIgnored, setHideIgnored] = useState(true)
  const [picked, setPicked] = useState<string[]>([])
  const [confirming, setConfirming] = useState(false)
  const qc = useQueryClient()
  const allBills = data?.open_bills ?? []
  const ignoredBills = allBills.filter((b) => b.dismissed)
  const bills = hideIgnored ? allBills.filter((b) => !b.dismissed) : allBills
  const pays = data?.payments ?? []

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['ap-ledger-supplier', code, currency] })
    // The supplier list and the currency cards both carry the ignored figure,
    // so they go stale the moment this changes.
    qc.invalidateQueries({ queryKey: ['ap-ledger-health-items'] })
    qc.invalidateQueries({ queryKey: ['ap-ledger-health-summary'] })
    qc.invalidateQueries({ queryKey: ['ap-ledger-dismissals'] })
  }
  const dismiss = useMutation({
    mutationFn: (v: { bill_nos: string[]; reason: string; note: string }) =>
      financeApi.post('/ap-ledger-health/dismiss', v),
    onSuccess: () => { setPicked([]); setConfirming(false); refresh() },
  })
  const restore = useMutation({
    mutationFn: (bill_nos: string[]) => financeApi.post('/ap-ledger-health/restore', { bill_nos }),
    onSuccess: refresh,
  })
  const pickedTotal = allBills
    .filter((b) => picked.includes(b.bill_no))
    .reduce((t, b) => t + Number(b.money_bal ?? 0), 0)
  const selectable = bills.filter((b) => !b.dismissed)
  // How often a payment here settled one of the bills still listed opposite.
  // Usually never: a payment closes what it pays, so an open bill and a
  // recorded payment are two nearly disjoint populations. When it is NOT zero,
  // those are bills that were paid and never cleared.
  const paysToOpen = pays.filter((p) => p.applied_bill_is_open).length
  const untied = pays.filter((p) => !p.applied_to_bill_no).length
  // The sharper finding: bills that are fully covered by payments pointing
  // straight at them, and are still flagged open. Nothing to chase — just
  // never cleared.
  const paidButOpen = bills.filter(
    (b) => Number(b.paid_against ?? 0) >= Number(b.money_cr ?? 0) && Number(b.money_cr ?? 0) > 0).length

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-6">
      <div className="w-full max-w-6xl rounded-lg bg-white shadow-xl">
        <div className="flex items-start justify-between border-b border-neutral-200 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-neutral-900">{name ?? code}</h2>
            <p className="mt-0.5 text-xs text-neutral-500">
              {code} · {currency} · {bills.length} bills still flagged open
              {ignoredBills.length > 0 && hideIgnored && (
                <span className="text-neutral-400"> (+{ignoredBills.length} ignored)</span>
              )} · {pays.length} payment lines
              {paidButOpen > 0 && (
                <> · <span className="font-medium text-amber-700">
                  {paidButOpen} of those are already covered by payments pointing at them
                </span></>
              )}
              {untied > 0 && (
                <> · <span className="text-neutral-500">{untied} payments applied to no payable</span></>
              )}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close"
                  className="rounded p-1 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        {isFetching && !data ? (
          <div className="py-16 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="grid gap-5 p-5 lg:grid-cols-2">
            <div className="min-w-0">
              <h3 className="mb-1 text-sm font-semibold text-neutral-800">Bills NC still shows as open</h3>
              {/* Not every open bill is debt. NC went live in 2020 and the
                  periods since have been closed at month-end and year-end, so
                  a leftover from then can no longer be cleared THERE — only
                  judged here. */}
              <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-neutral-500">
                {ignoredBills.length > 0 ? (
                  <label className="inline-flex items-center gap-1.5">
                    <input type="checkbox" checked={hideIgnored}
                           onChange={(e) => { setHideIgnored(e.target.checked); setPicked([]) }} />
                    Hide {ignoredBills.length} ignored
                    <span className="text-neutral-400">
                      ({money(data?.open_bills_total.ignored_bal)} {currency})
                    </span>
                  </label>
                ) : (
                  <span>Select the ones that are not real debt to take them off this list.</span>
                )}
                {picked.length > 0 && (
                  <>
                    <span className="font-medium text-neutral-700">
                      {picked.length} selected · {money(String(pickedTotal))} {currency}
                    </span>
                    <button onClick={() => setConfirming(true)}
                            className="inline-flex items-center gap-1 rounded border border-[#085E5E] px-1.5 py-0.5 font-medium text-[#085E5E] hover:bg-primary-50">
                      <EyeOff className="h-3 w-3" /> Ignore these
                    </button>
                    <button onClick={() => setPicked([])} className="text-neutral-400 hover:text-neutral-600">
                      clear
                    </button>
                  </>
                )}
                {(dismiss.isError || restore.isError) && (
                  <span className="font-medium text-red-600">
                    Could not save that — you may not have permission to set payables aside.
                  </span>
                )}
              </div>
              <div className="max-h-[52vh] overflow-auto rounded-lg border border-neutral-200">
                <table className="w-full min-w-[480px] text-xs">
                  <thead className="sticky top-0 bg-neutral-50">
                    <tr className="border-b border-neutral-100 text-[11px] text-neutral-500">
                      <th className="w-7 px-2 py-2 text-left font-medium">
                        <input type="checkbox" aria-label="Select all"
                               checked={selectable.length > 0 && picked.length === selectable.length}
                               onChange={(e) => setPicked(
                                 e.target.checked ? selectable.map((b) => b.bill_no) : [])} />
                      </th>
                      <th className="px-2.5 py-2 text-left font-medium">Bill</th>
                      <th className="px-2.5 py-2 text-left font-medium">Date</th>
                      <th className="px-2.5 py-2 text-left font-medium">Invoice</th>
                      <th className="px-2.5 py-2 text-right font-medium">Billed</th>
                      <th className="px-2.5 py-2 text-right font-medium">Paid against it</th>
                      <th className="px-2.5 py-2 text-right font-medium">Still open</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bills.length === 0 ? (
                      <tr><td colSpan={7} className="px-2.5 py-6 text-center text-neutral-400">
                        {ignoredBills.length > 0 && hideIgnored
                          ? <>Nothing left — all {ignoredBills.length} open bills have been ignored.</>
                          : <>None.</>}
                      </td></tr>
                    ) : bills.map((b) => {
                      const covered = Number(b.paid_against ?? 0) >= Number(b.money_cr ?? 0)
                        && Number(b.money_cr ?? 0) > 0
                      const gone = !!b.dismissed
                      return (
                        <tr key={b.bill_no}
                            onClick={() => setLinkedBill(linkedBill === b.bill_no ? null : b.bill_no)}
                            className={cn('cursor-pointer border-t border-neutral-100',
                                          covered && !gone && 'bg-amber-50/60',
                                          gone && 'text-neutral-400',
                                          linkedBill === b.bill_no && 'ring-2 ring-inset ring-[#085E5E]')}>
                          <td className="px-2 py-1.5" onClick={(e) => e.stopPropagation()}>
                            {gone ? (
                              <button onClick={() => restore.mutate([b.bill_no])}
                                      title="Put this bill back on the list"
                                      aria-label={`Restore ${b.bill_no}`}
                                      className="text-neutral-400 hover:text-[#085E5E]">
                                <Undo2 className="h-3.5 w-3.5" />
                              </button>
                            ) : (
                              <input type="checkbox" aria-label={`Select ${b.bill_no}`}
                                     checked={picked.includes(b.bill_no)}
                                     onChange={(e) => setPicked(
                                       e.target.checked
                                         ? [...picked, b.bill_no]
                                         : picked.filter((x) => x !== b.bill_no))} />
                            )}
                          </td>
                          <td className="px-2.5 py-1.5 font-mono">
                            {b.bill_no}
                            {b.payment_lines > 0 && (
                              <span className="ml-1.5 rounded bg-primary-100 px-1 py-0.5 text-[10px] font-semibold text-[#085E5E]">
                                {b.payment_lines} pmt
                              </span>
                            )}
                            {/* Who judged it and why, on the row itself: a
                                dismissal nobody can trace is worse than none. */}
                            {b.dismissed && (
                              <span title={[b.dismissed.note, b.dismissed.by, b.dismissed.at?.slice(0, 10)]
                                      .filter(Boolean).join(' · ')}
                                    className="ml-1.5 rounded bg-neutral-200 px-1 py-0.5 text-[10px] font-medium text-neutral-500">
                                ignored
                              </span>
                            )}
                          </td>
                          <td className="px-2.5 py-1.5 text-neutral-600">{day(b.bill_date)}</td>
                          <td className="px-2.5 py-1.5 font-mono text-neutral-600">{b.invoice_no ?? '—'}</td>
                          <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">{money(b.money_cr)}</td>
                          <td className={cn('px-2.5 py-1.5 text-right font-mono tabular-nums',
                                            covered ? 'font-semibold text-amber-700' : 'text-neutral-500')}>
                            {money(b.paid_against)}
                          </td>
                          <td className={cn('px-2.5 py-1.5 text-right font-mono tabular-nums font-semibold',
                                            gone && 'font-normal line-through')}>
                            {money(b.money_bal)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                  {data?.open_bills_total && (
                    <tfoot className="sticky bottom-0 bg-neutral-50">
                      {/* NC's own figure stays on screen whatever finance has
                          set aside, and the subtraction is shown as a line of
                          its own. A netted total with no working is how a
                          number here stops being reconcilable to an NC report. */}
                      <tr className="border-t-2 border-neutral-300">
                        <td className="px-2.5 py-2 text-[11px] font-semibold text-neutral-600" colSpan={4}>
                          {data.open_bills_total.bills} bills · {currency}
                        </td>
                        <td className={footCell}>{money(data.open_bills_total.money_cr)}</td>
                        <td className={footCell}>{money(data.open_bills_total.paid_against)}</td>
                        <td className={footCell}>{money(data.open_bills_total.money_bal)}</td>
                      </tr>
                      {data.open_bills_total.ignored_bills > 0 && (
                        <>
                          <tr className="border-t border-neutral-200 text-neutral-400">
                            <td className="px-2.5 py-1 text-[11px]" colSpan={6}>
                              less {data.open_bills_total.ignored_bills} ignored
                            </td>
                            <td className="px-2.5 py-1 text-right font-mono text-xs tabular-nums">
                              &minus;{money(data.open_bills_total.ignored_bal)}
                            </td>
                          </tr>
                          <tr className="border-t border-neutral-300 bg-white">
                            <td className="px-2.5 py-2 text-[11px] font-semibold text-neutral-700" colSpan={6}>
                              Still to work
                            </td>
                            <td className="px-2.5 py-2 text-right font-mono tabular-nums font-semibold text-[#085E5E]">
                              {money(data.open_bills_total.money_bal_after_ignored)}
                            </td>
                          </tr>
                        </>
                      )}
                    </tfoot>
                  )}
                </table>
              </div>
            </div>

            <div className="min-w-0">
              <h3 className="mb-1 text-sm font-semibold text-neutral-800">Payments recorded for this supplier</h3>
              <p className="mb-2 text-[11px] leading-relaxed text-neutral-500">
                Each payment carries the payable it cleared. Click either side to light up its
                counterpart.{' '}
                {paysToOpen === 0
                  ? <>None of these cleared a bill still listed opposite — expected, since a payment
                      closes what it pays, so the two lists barely overlap.</>
                  : <span className="font-medium text-amber-700">{paysToOpen} of them cleared a bill
                      that is STILL flagged open — paid, but never applied.</span>}
              </p>
              <div className="max-h-[52vh] overflow-auto rounded-lg border border-neutral-200">
                <table className="w-full min-w-[440px] text-xs">
                  <thead className="sticky top-0 bg-neutral-50">
                    <tr className="border-b border-neutral-100 text-[11px] text-neutral-500">
                      <th className="px-2.5 py-2 text-left font-medium">Payment</th>
                      <th className="px-2.5 py-2 text-left font-medium">Document date</th>
                      <th className="px-2.5 py-2 text-right font-medium">Amount</th>
                      <th className="px-2.5 py-2 text-left font-medium">Applied to payable</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pays.length === 0 ? (
                      <tr><td colSpan={4} className="px-2.5 py-6 text-center text-neutral-400">None.</td></tr>
                    ) : pays.map((p, i) => (
                      <tr key={p.bill_no + i}
                          onClick={() => setLinkedBill(
                            linkedBill === p.applied_to_bill_no ? null : p.applied_to_bill_no)}
                          className={cn('border-t border-neutral-100',
                                        p.applied_to_bill_no && 'cursor-pointer',
                                        linkedBill && p.applied_to_bill_no === linkedBill
                                          && 'bg-primary-50 ring-2 ring-inset ring-[#085E5E]')}>
                        <td className="px-2.5 py-1.5 font-mono">{p.bill_no}</td>
                        {/* NC leaves PAYDATE empty on every payment line in this
                            database, so the document date is the only date there
                            is — say which one it is rather than showing a dash. */}
                        <td className="px-2.5 py-1.5 text-neutral-600">{day(p.pay_date ?? p.doc_date)}</td>
                        <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">{money(p.money_de)}</td>
                        <td className="px-2.5 py-1.5 font-mono">
                          {p.applied_to_bill_no ? (
                            <>
                              <span className={cn(p.applied_bill_is_open
                                                    ? 'font-semibold text-amber-700' : 'text-neutral-700')}>
                                {p.applied_to_bill_no}
                              </span>
                              {Number(p.applied_bill_still_open ?? 0) !== 0 && (
                                <span className="ml-1.5 rounded bg-amber-100 px-1 py-0.5 text-[10px] font-semibold text-amber-800">
                                  still open
                                </span>
                              )}
                            </>
                          ) : <span className="text-neutral-400">none</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  {data?.payments_total && (
                    <tfoot className="sticky bottom-0 bg-neutral-50">
                      <tr className="border-t-2 border-neutral-300">
                        <td className="px-2.5 py-2 text-[11px] font-semibold text-neutral-600" colSpan={2}>
                          {data.payments_total.lines} payment lines · {currency}
                        </td>
                        <td className={footCell}>{money(data.payments_total.money_de)}</td>
                        <td></td>
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>
            </div>
          </div>
        )}

        {confirming && (
          <DismissDialog billNos={picked} total={pickedTotal} currency={currency}
                         onCancel={() => setConfirming(false)}
                         onDone={(reason, note) =>
                           dismiss.mutate({ bill_nos: picked, reason, note })} />
        )}

        {hasGap && gl && (
          <div className="border-t border-neutral-200 px-5 pb-5 pt-4">
            <h3 className="mb-1 text-sm font-semibold text-neutral-800">
              Journal entries on this supplier&rsquo;s payable account
            </h3>
            <p className="mb-2.5 max-w-3xl text-xs leading-relaxed text-neutral-500">
              Vouchers that moved the payable without an AP document — where a difference settled
              by a manual entry shows up. {gl.matching > 0
                ? <span className="font-medium text-[#085E5E]">{gl.matching} of them is for exactly
                    the difference.</span>
                : <>None is for exactly the difference, so it is either the net of several or was
                    settled another way.</>}
            </p>
            {gl.offset_lines > 0 && (
              <label className="mb-2 inline-flex items-center gap-1.5 text-xs text-neutral-600">
                <input type="checkbox" checked={hideOffsets}
                       onChange={(e) => setHideOffsets(e.target.checked)} />
                Hide {gl.offset_lines} entries that cancel each other out
                <span className="text-neutral-400">
                  ({gl.total - gl.offset_lines} left)
                </span>
              </label>
            )}
            <div className="max-h-[38vh] overflow-auto rounded-lg border border-neutral-200">
              <table className="w-full min-w-[640px] text-xs">
                <thead className="sticky top-0 bg-neutral-50">
                  <tr className="border-b border-neutral-100 text-[11px] text-neutral-500">
                    <th className="px-2.5 py-2 text-left font-medium">Voucher</th>
                    <th className="px-2.5 py-2 text-left font-medium">Date</th>
                    <th className="px-2.5 py-2 text-left font-medium">Source</th>
                    <th className="px-2.5 py-2 text-left font-medium">Account</th>
                    <th className="px-2.5 py-2 text-right font-medium">Debit</th>
                    <th className="px-2.5 py-2 text-right font-medium">Credit</th>
                    <th className="px-2.5 py-2 text-left font-medium">Narration</th>
                  </tr>
                </thead>
                <tbody>
                  {gl.items.length === 0 ? (
                    <tr><td colSpan={7} className="px-2.5 py-6 text-center text-neutral-400">
                      No journal entries on this supplier&rsquo;s payable account.</td></tr>
                  ) : gl.items
                        .filter((r) => !(hideOffsets && r.offset_group))
                        .map((r, i) => (
                    <tr key={r.jv_number + i}
                        className={cn('border-t border-neutral-100',
                                      r.matches_gap && 'bg-primary-50/70',
                                      r.offset_group && 'text-neutral-400')}>
                      <td className="px-2.5 py-1.5 font-mono">
                        {r.jv_number}
                        {r.offset_group && (
                          <span className="ml-1.5 rounded bg-neutral-200 px-1 py-0.5 text-[10px] font-medium text-neutral-500">
                            offset
                          </span>
                        )}
                        {r.matches_gap && (
                          <span className="ml-1.5 rounded bg-[#085E5E] px-1 py-0.5 text-[10px] font-semibold text-white">
                            matches
                          </span>
                        )}
                      </td>
                      <td className="px-2.5 py-1.5 text-neutral-600">{day(r.voucher_date)}</td>
                      <td className="px-2.5 py-1.5 font-mono text-neutral-500">{r.subsystem ?? '—'}</td>
                      <td className="px-2.5 py-1.5 font-mono text-neutral-600">
                        {r.account_code}
                        {/* A voucher in another currency against this gap is
                            worth seeing, not hiding — sometimes it IS the answer. */}
                        {!r.same_currency && (
                          <span className="ml-1 text-[10px] text-amber-700">{r.currency}</span>
                        )}
                      </td>
                      <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">{money(r.debit)}</td>
                      <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">{money(r.credit)}</td>
                      <td className="px-2.5 py-1.5 text-neutral-600">{r.summary ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
                {/* One subtotal PER CURRENCY. This list is not filtered by
                    currency on purpose, so a single combined figure would add
                    CAD to USD — and for Aptargroup every one of the 66 lines is
                    USD against a CAD gap, which the split makes obvious. */}
                {gl.totals.length > 0 && (
                  <tfoot className="sticky bottom-0 bg-neutral-50">
                    {gl.totals.map((t) => (
                      <tr key={t.currency ?? 'none'} className="border-t-2 border-neutral-300">
                        <td className="px-2.5 py-2 text-[11px] font-semibold text-neutral-600" colSpan={3}>
                          {hideOffsets ? t.lines_after_offsets : t.lines} lines · {t.currency ?? 'no currency'}
                          {t.same_currency
                            ? <span className="ml-1.5 text-[10px] font-normal text-neutral-400">supplier currency</span>
                            : <span className="ml-1.5 text-[10px] font-normal text-amber-700">other currency</span>}
                        </td>
                        <td className="px-2.5 py-2 text-[11px] text-neutral-500">net</td>
                        <td className={footCell}>{money(t.debit)}</td>
                        <td className={footCell}>{money(t.credit)}</td>
                        <td className={cn('px-2.5 py-2 font-mono tabular-nums font-semibold',
                                          t.net_matches_gap ? 'text-[#085E5E]' : 'text-neutral-800')}>
                          {money(hideOffsets ? t.net_after_offsets : t.net)}
                          {(hideOffsets ? t.net_after_offsets_matches_gap : t.net_matches_gap) && (
                            <span className="ml-1.5 rounded bg-[#085E5E] px-1 py-0.5 text-[10px] text-white">
                              = difference
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tfoot>
                )}
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function ApLedgerHealthPage() {
  const { user } = useAuthStore()
  const [currency, setCurrency] = useState('CAD')
  // The approved payable is the page's subject. The abandoned documents are
  // not work — they are an exclusion that has to be declared, not worked.
  const [view, setView] = useState<'suppliers' | 'abandoned'>('suppliers')
  const [open, setOpen] = useState<{ code: string; name: string | null; gap: string | null } | null>(null)

  const { data: summary, isFetching: loadingSummary } = useQuery({
    queryKey: ['ap-ledger-health-summary'],
    queryFn: () => financeApi.get<SummaryResp>('/ap-ledger-health/summary'),
  })
  const { data: items, isFetching: loadingItems } = useQuery({
    queryKey: ['ap-ledger-health-items', currency],
    queryFn: () => financeApi.get<ItemsResp>(
      `/ap-ledger-health/items?currency=${currency}&limit=500`),
    enabled: view === 'suppliers',
  })
  const { data: abandoned, isFetching: loadingAbandoned } = useQuery({
    queryKey: ['ap-ledger-abandoned', currency],
    queryFn: () => financeApi.get<AbandonedResp>(
      `/ap-ledger-health/abandoned?currency=${currency}&limit=500`),
    enabled: view === 'abandoned',
  })

  const ccyRows = summary?.currencies ?? []

  const exportCsv = () => {
    const head = view === 'abandoned'
      ? ['Bill', 'Date', 'Supplier code', 'Supplier', 'Invoice no', 'Currency',
         'Billed', 'Balance carried', 'Bill status', 'Approve status', 'Duplicate of an approved bill']
      : ['Supplier code', 'Supplier', 'Currency', 'Health', 'Billed', 'Paid',
         'Subledger open', 'Billed minus paid', 'Gap',
         'Ignored bills', 'Ignored balance', 'Open after ignored']
    const body = view === 'abandoned'
      ? (abandoned?.items ?? []).map((r) => [
          r.bill_no, r.bill_date, r.supplier_code, r.supplier_name, r.invoice_no,
          r.currency, r.money_cr, r.money_bal, r.bill_status, r.approve_status,
          r.superseded ? 'yes' : 'no'])
      : (items?.items ?? []).map((r) => [
          r.supplier_code, r.supplier_name, r.currency, r.health,
          r.billed, r.paid, r.subledger_open, r.billed_minus_paid, r.gap,
          r.ignored_bills, r.ignored_bal, r.open_after_ignored])
    const csv = [head, ...body].map((row) => row.map(csvEscape).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `ap-subledger-health-${currency}-${view}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/ap-ledger-health"
      title="AP Subledger Health"
      subtitle="Where NC's open balance and its own billed-minus-paid disagree"
    >
      <div className="mx-auto max-w-7xl">
        <div className="mb-5 rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm leading-relaxed text-neutral-700">
          <p className="mb-1.5">
            Counting only the documents NC <strong>approved</strong>, the subledger is healthy — the open
            balance and billed-minus-paid agree to the cent for almost every supplier. That is the
            figure the cash-flow forecast is built on, and this page exists to keep it honest: any
            supplier where the two start to disagree shows up here.
          </p>
          <p className="text-neutral-500">
            Documents NC never approved are <strong>excluded</strong> and are not work: none of them has
            an approved payment against it, so no money ever moved. They are listed only so the
            exclusion is on the record — if a figure here ever differs from an NC report, this is
            the difference. Where the approved side does disagree,
            <strong> the judgement is finance&rsquo;s, per supplier.</strong>
          </p>
        </div>

        {loadingSummary && !summary ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {ccyRows.map((c) => {
              const bad = Number(c.inconsistent.gap) !== 0
              return (
                <button key={c.currency} onClick={() => setCurrency(c.currency)}
                        className={cn('rounded-lg border p-3 text-left transition-colors',
                                      currency === c.currency
                                        ? 'border-[#085E5E] bg-primary-50/60'
                                        : 'border-neutral-200 hover:bg-neutral-50')}>
                  <div className="flex items-center gap-1.5">
                    {/* The health signal is the approved side disagreeing —
                        never the size of the excluded pile, which is inert. */}
                    {bad ? <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                         : <CheckCircle2 className="h-3.5 w-3.5 text-[#085E5E]" />}
                    <span className="font-mono text-sm font-semibold">{c.currency}</span>
                  </div>
                  <dl className="mt-2 space-y-1 text-xs">
                    <div className="flex justify-between gap-2">
                      <dt className="text-neutral-500">Real payable</dt>
                      <dd className="font-mono tabular-nums">{money(c.consistent.subledger_open)}</dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt className="text-neutral-500">Approved, disagrees</dt>
                      <dd className="font-mono tabular-nums">{money(c.inconsistent.gap)}</dd>
                    </div>
                    {/* Reads from the CONSISTENT bucket, because that bucket is
                        what "Real payable" above is — the currency-wide ignored
                        figure includes bills on suppliers whose subledger
                        disagrees, and those contribute nothing to that line.
                        Subtracting one from the other printed a "Net payable"
                        identical to the gross, which is worse than showing
                        nothing. Only rendered once something has been set aside,
                        so an untouched currency reads exactly as it did before. */}
                    {Number(c.consistent.ignored_bal) !== 0 && (
                      <>
                        <div className="flex justify-between gap-2">
                          <dt className="text-neutral-400">Ignored by finance</dt>
                          <dd className="font-mono tabular-nums text-neutral-400">
                            &minus;{money(c.consistent.ignored_bal)}
                          </dd>
                        </div>
                        <div className="flex justify-between gap-2 border-t border-neutral-200 pt-1">
                          <dt className="font-medium text-neutral-600">Net payable</dt>
                          <dd className="font-mono tabular-nums font-semibold text-[#085E5E]">
                            {money(c.consistent.open_after_ignored)}
                          </dd>
                        </div>
                      </>
                    )}
                    <div className="flex justify-between gap-2 border-t border-neutral-200 pt-1">
                      <dt className="text-neutral-400">Excluded (unapproved)</dt>
                      <dd className="font-mono tabular-nums text-neutral-400">{money(c.abandoned.money_bal)}</dd>
                    </div>
                  </dl>
                  <p className="mt-1.5 text-[11px] text-neutral-400">
                    {c.consistent.suppliers} suppliers agree · {c.abandoned.bills} abandoned docs
                    {c.abandoned.superseded_bills > 0 && <>, {c.abandoned.superseded_bills} duplicate</>}
                    {c.ignored.bills > 0 && <> · {c.ignored.bills} bills ignored</>}
                  </p>
                </button>
              )
            })}
          </div>
        )}

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="inline-flex overflow-hidden rounded-lg border border-neutral-300">
            {([['suppliers', 'Approved suppliers'], ['abandoned', 'Excluded documents']] as const).map(([k, label]) => (
              <button key={k} onClick={() => setView(k)}
                      className={cn('px-3 py-1.5 text-sm',
                                    view === k ? 'bg-[#085E5E] text-white' : 'bg-white text-neutral-700 hover:bg-neutral-50')}>
                {label}
              </button>
            ))}
          </div>
          <span className="text-sm text-neutral-500">
            {currency} · {view === 'abandoned' ? `${abandoned?.total ?? 0} documents` : `${items?.total ?? 0} suppliers`}
          </span>
          <button onClick={exportCsv}
                  className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50">
            <Download className="h-4 w-4" /> Export CSV
          </button>
        </div>

        {view === 'abandoned' ? (
          <div className="overflow-x-auto rounded-lg border border-neutral-200">
            <table className="w-full min-w-[880px] text-sm">
              <thead>
                <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                  <th className="px-3 py-2 text-left font-medium">Bill</th>
                  <th className="px-3 py-2 text-left font-medium">Date</th>
                  <th className="px-3 py-2 text-left font-medium">Supplier</th>
                  <th className="px-3 py-2 text-left font-medium">Invoice no.</th>
                  <th className="px-3 py-2 text-right font-medium">Billed</th>
                  <th className="px-3 py-2 text-right font-medium">Balance carried</th>
                  <th className="px-3 py-2 text-left font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {loadingAbandoned && !abandoned ? (
                  <tr><td colSpan={7} className="px-3 py-8 text-center">
                    <Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></td></tr>
                ) : (abandoned?.items.length ?? 0) === 0 ? (
                  <tr><td colSpan={7} className="px-3 py-8 text-center text-neutral-400">
                    No abandoned documents carrying a balance in {currency}.</td></tr>
                ) : abandoned!.items.map((r) => (
                  <tr key={r.bill_no} className="border-t border-neutral-100 hover:bg-neutral-50/60">
                    <td className="px-3 py-2 font-mono text-xs">{r.bill_no}</td>
                    <td className="px-3 py-2 text-xs text-neutral-600">{day(r.bill_date)}</td>
                    <td className="px-3 py-2">
                      <div className="text-neutral-800">{r.supplier_name ?? '—'}</div>
                      <div className="font-mono text-[11px] text-neutral-400">{r.supplier_code ?? '—'}</div>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-neutral-600">{r.invoice_no ?? '—'}</td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-neutral-600">{money(r.money_cr)}</td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums font-semibold text-amber-700">{money(r.money_bal)}</td>
                    <td className="px-3 py-2 text-xs">
                      <span className="font-mono text-neutral-500">{r.bill_status}/{r.approve_status}</span>
                      {/* The cheapest ones to clear: an approved bill already
                          carries this supplier, amount and invoice number. */}
                      {r.superseded && (
                        <span className="ml-1.5 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800">
                          duplicate
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
        <div className="overflow-x-auto rounded-lg border border-neutral-200">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                <th className="px-3 py-2 text-left font-medium">Supplier</th>
                <th className="px-3 py-2 text-right font-medium">Billed</th>
                <th className="px-3 py-2 text-right font-medium">Paid</th>
                <th className="px-3 py-2 text-right font-medium">Subledger open</th>
                <th className="px-3 py-2 text-right font-medium">Billed &minus; paid</th>
                <th className="px-3 py-2 text-right font-medium">Gap</th>
                <th className="px-3 py-2 text-left font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {loadingItems && !items ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></td></tr>
              ) : (items?.items.length ?? 0) === 0 ? (
                <tr><td colSpan={7} className="px-3 py-8 text-center text-neutral-400">
                  No suppliers in this state for {currency}.</td></tr>
              ) : items!.items.map((r) => {
                // A supplier whose whole balance has been judged away is not
                // work any more. It stays listed and states what was ignored —
                // dropping the row would make the page disagree with NC with
                // nothing on screen to explain why.
                const cleared = Number(r.open_after_ignored) === 0 && r.ignored_bills > 0
                return (
                <tr key={(r.supplier_code ?? '') + r.currency}
                    className={cn('border-t border-neutral-100 hover:bg-neutral-50/60',
                                  cleared && 'text-neutral-400')}>
                  <td className="px-3 py-2">
                    <div className={cn(cleared ? 'text-neutral-500' : 'text-neutral-800')}>
                      {r.supplier_name ?? '—'}
                      {cleared && (
                        <span className="ml-1.5 rounded bg-neutral-200 px-1.5 py-0.5 text-[10px] font-medium text-neutral-500">
                          all ignored
                        </span>
                      )}
                    </div>
                    <div className="font-mono text-[11px] text-neutral-400">{r.supplier_code ?? '—'}</div>
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-neutral-600">{money(r.billed)}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums text-neutral-600">{money(r.paid)}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">
                    {money(r.subledger_open)}
                    {r.ignored_bills > 0 && (
                      <div className="text-[11px] text-neutral-400">
                        &minus;{money(r.ignored_bal)} ignored → {money(r.open_after_ignored)}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">{money(r.billed_minus_paid)}</td>
                  <td className={cn('px-3 py-2 text-right font-mono tabular-nums font-semibold',
                                    Number(r.gap) === 0 ? 'text-neutral-400' : 'text-amber-700')}>
                    {money(r.gap)}
                  </td>
                  <td className="px-3 py-2">
                    {r.supplier_code && (
                      <button onClick={() => setOpen({ code: r.supplier_code!, name: r.supplier_name, gap: r.gap })}
                              className="text-xs font-medium text-[#085E5E] hover:underline">
                        Show documents
                      </button>
                    )}
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        )}
      </div>

      {open && (
        <SupplierDetail code={open.code} name={open.name} currency={currency}
                        gap={open.gap} onClose={() => setOpen(null)} />
      )}
    </PortalChromeLayout>
  )
}
