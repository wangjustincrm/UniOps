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
import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Download, EyeOff, Loader2, Search, Undo2 } from 'lucide-react'
import { Button, Checkbox, ChoiceCard, Input, Modal as ShellModal, Segmented, Select, SortTh, TBody, TFoot, THead, Table, Td, Textarea, Th, Tr, sortParams, type Sort, useSort } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { invalidateApJudgement } from '@/lib/apJudgement'
import { cn, formatDate } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { ListPager } from '@/components/ListPager'
import { BillInvoices, billPurchaseOrders, invoicesCsv, type BillInvoice } from '@/components/ap/BillInvoices'
import { BillEvidence, type JvMatch, type OtherCurrencyBill } from '@/components/ap/BillEvidence'

const PAGE_SIZE = 100
/** Bulk "Ignore all matching" refuses more than this many (ap_ledger_health.dismiss_matching). */
const MAX_PER_ACTION = 1000
type SupplierSortKey = 'supplier' | 'billed' | 'paid' | 'subledger_open' | 'billed_minus_paid' | 'gap'
type AbandonedSortKey = 'bill_no' | 'bill_date' | 'supplier' | 'invoice_no' | 'money_cr' | 'money_bal' | 'status'
type BillSortKey = 'bill_no' | 'bill_date' | 'supplier' | 'invoice_no' | 'purchase_order' | 'money_cr' | 'money_bal'

/** `&sort=…&dir=…` for a list URL, or '' for the API's default order. */
function sortQs<K extends string>(sort: Sort<K> | null): string {
  const { sort: key, dir } = sortParams(sort)
  return key && dir ? `&sort=${key}&dir=${dir}` : ''
}

// Shared by the three subtotal rows in the supplier view.
const footCell = 'px-2.5 py-2 text-right font-mono tabular-nums font-semibold text-fg'

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

/** A bill whose invoice number matched the search box. */
interface InvoiceHit {
  bill_no: string; invoice_no: string | null; bill_date: string | null
  money_cr: string; money_bal: string; dismissed: boolean
}
interface SupplierRow {
  /** Set only when the search text matched invoice numbers. */
  invoice_hits: InvoiceHit[]
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
  /** One per invoice number on the bill's lines — usually one, often several. */
  invoices: BillInvoice[]; trade_type: string | null
  /** Only for bills with no payment document: JV lines naming one of its invoices. */
  jv_matches: JvMatch[]
  /** The same invoice number on this supplier's bills in another currency. */
  other_currency: OtherCurrencyBill[]
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

/** One open bill, across every supplier — the population a bulk judgement acts on. */
interface FlatBill {
  bill_no: string; currency: string; bill_date: string | null
  supplier_code: string | null; supplier_name: string | null
  invoices: BillInvoice[]; trade_type: string | null
  money_cr: string; money_bal: string
  payment_lines: number
  jv_matches: JvMatch[]; other_currency: OtherCurrencyBill[]
  dismissed: { reason: string; note: string | null; by: string | null; at: string | null } | null
}
interface BillsResp {
  total: number; money_bal: string
  dismissed_bills: number; dismissed_bal: string
  items: FlatBill[]
}
interface ClusterRow {
  bill_date: string; bills: number; suppliers: number
  amount: string; dismissed_bills: number
}
interface ClustersResp { items: ClusterRow[] }
interface PaymentsTotal { lines: number; money_de: string }
/** Payable-account clearings for the supplier that name no invoice
 *  (finance-api nc_ap_bill_evidence.unattributed_clearings). */
interface UnattributedClearings {
  since: string | null; unpaid_bills: number; unpaid_open: string; total: string
  lines: { jv_number: string; voucher_date: string; subsystem: string | null
           amount: string; summary: string | null }[]
}
interface DetailResp {
  unattributed_clearings: UnattributedClearings
  supplier_code: string; currency: string
  open_bills_total: BillsTotal; payments_total: PaymentsTotal
  open_bills: OpenBill[]; payments: PayLine[]
}

interface AbandonedRow {
  bill_no: string; currency: string; bill_date: string | null; bill_year: string | null
  bill_status: number | null; approve_status: number | null
  supplier_code: string | null; supplier_name: string | null; invoices: BillInvoice[]
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
function day(v: string | null) { return v ? formatDate(v.slice(0, 10)) : '—' }

/**
 * Recording why a payable is being set aside.
 *
 * The reason is mandatory and comes from the server, not from a list copied
 * into this file — the closed set lives in one place so an option can never
 * exist on screen and 422 on submit. A free-text note is optional except under
 * "Other", where a dismissal with no explanation would be unreviewable.
 */
function DismissDialog({ billNos, count, total, currency, onCancel, onDone }: {
  /** The bills, or null for "every bill matching the filter" (too many to list). */
  billNos: string[] | null; count: number; total: number; currency: string
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
    <ShellModal open onClose={onCancel} title={<>Ignore {count} {count === 1 ? 'bill' : 'bills'}</>} description={<>{money(String(total))} {currency} comes off the working list. NC is not changed — the page keeps showing what NC says and what was set aside, separately, and this can be undone.</>} closeOnEscape={false} stacked size="full" className="max-w-lg"
      footer={<>
        <Button variant="secondary" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={() => onDone(reason, note.trim())} disabled={needsNote}
               >
          Ignore {count}
        </Button>
      </>}>
      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-fg-secondary">Reason</label>
          <Select value={reason} onChange={(e) => setReason(e.target.value)}
                  className="w-full">
            {(data?.reasons ?? []).map((r) => (
              <option key={r.code} value={r.code}>{r.label}</option>
            ))}
          </Select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-fg-secondary">
            Note {reason === 'other' && <span className="text-warning-fg">(required)</span>}
          </label>
          <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3}
                    placeholder="e.g. NC went live 2020, period closed, cannot be cleared there"
                    className="w-full" />
        </div>
        <div className="max-h-24 overflow-auto rounded border border-line bg-surface-subtle px-2.5 py-1.5 font-mono text-2xs text-fg-muted">
          {billNos ? billNos.join(', ') : `Every bill matching the current filter (${count}).`}
        </div>
      </div>
    </ShellModal>
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

  // Everything built on the open balance goes stale here, not just this modal
  // — AP Cash Flow included. See lib/apJudgement.
  const refresh = () => invalidateApJudgement(qc)
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
  // Settled by journal instead of a payment document — the invoice number in a
  // JV summary is the only link NC has (see BillEvidence).
  const inJv = bills.filter((b) => b.jv_matches.length > 0).length
  const twinned = bills.filter((b) => b.other_currency.length > 0).length
  const paidButOpen = bills.filter(
    (b) => Number(b.paid_against ?? 0) >= Number(b.money_cr ?? 0) && Number(b.money_cr ?? 0) > 0).length

  return (
    // No backdrop / Escape close (as before): only the close button.
    <ShellModal open onClose={onClose} closeOnEscape={false} title={name ?? code} size="full" className="max-w-6xl"
      description={<>
      {code} · {currency} · {bills.length} bills still flagged open
      {ignoredBills.length > 0 && hideIgnored && (
        <span className="text-fg-muted"> (+{ignoredBills.length} ignored)</span>
      )} · {pays.length} payment lines
      {paidButOpen > 0 && (
        <> · <span className="font-medium text-warning-fg">
          {paidButOpen} of those are already covered by payments pointing at them
        </span></>
      )}
      {untied > 0 && (
        <> · <span className="text-fg-muted">{untied} payments applied to no payable</span></>
      )}
      {inJv > 0 && (
        <> · <span className="font-medium text-info-fg">
          {inJv} with no payment document found in a JV
        </span></>
      )}
      {twinned > 0 && (
        <> · <span className="font-medium text-warning-fg">
          {twinned} with the same invoice in another currency
        </span></>
      )}
      </>}>
      {isFetching && !data ? (
        <div className="py-16 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
      ) : (
        <>
        {/* A voucher that cleared the payable without naming an invoice —
            e.g. receivable netted against payable — is invisible to the
            per-bill match. Shown beside the bills for finance to compare,
            never applied to any of them. */}
        {(data?.unattributed_clearings.lines.length ?? 0) > 0 && (
          <div className="mx-5 mt-5 rounded-lg border border-info-line bg-info-soft/60 px-4 py-3 text-xs">
            <div className="font-semibold text-info-fg">
              Cleared on the payable account without naming an invoice
            </div>
            <p className="mt-0.5 text-info-fg">
              {data!.unattributed_clearings.unpaid_bills} open bills since{' '}
              {day(data!.unattributed_clearings.since)} have no payment document,{' '}
              {money(data!.unattributed_clearings.unpaid_open)} {currency} (not counting ignored ones).
              These journal lines reduced this supplier&rsquo;s payable in that time and name none
              of its invoices — {money(data!.unattributed_clearings.total)} {currency} in all.
              Check which bills they settled; nothing here is applied automatically.
            </p>
            <Table density="compact" className="mt-2 text-2xs">
              <TBody>
                {data!.unattributed_clearings.lines.map((l) => (
                  <Tr key={l.jv_number + l.amount} className="border-info-line">
                    <Td className="pr-3 font-mono">{l.jv_number}</Td>
                    <Td className="pr-3 whitespace-nowrap">{day(l.voucher_date)}</Td>
                    <Td className="pr-3 text-info-fg">{l.subsystem ?? '—'}</Td>
                    <Td align="right" className="pr-3 font-mono tabular-nums">{money(l.amount)}</Td>
                    <Td className="text-fg-secondary">{l.summary ?? '—'}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
        )}
        <div className="grid gap-5 p-5 lg:grid-cols-2">
          <div className="min-w-0">
            <h3 className="mb-1 text-sm font-semibold text-fg">Bills NC still shows as open</h3>
            {/* Not every open bill is debt. NC went live in 2020 and the
                periods since have been closed at month-end and year-end, so
                a leftover from then can no longer be cleared THERE — only
                judged here. */}
            <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-fg-muted">
              {ignoredBills.length > 0 ? (
                <label className="inline-flex items-center gap-1.5">
                  <Checkbox checked={hideIgnored}
                         onChange={(e) => { setHideIgnored(e.target.checked); setPicked([]) }} />
                  Hide {ignoredBills.length} ignored
                  <span className="text-fg-muted">
                    ({money(data?.open_bills_total.ignored_bal)} {currency})
                  </span>
                </label>
              ) : (
                <span>Select the ones that are not real debt to take them off this list.</span>
              )}
              {picked.length > 0 && (
                <>
                  <span className="font-medium text-fg-secondary">
                    {picked.length} selected · {money(String(pickedTotal))} {currency}
                  </span>
                  <Button variant="outline" size="xs" onClick={() => setConfirming(true)}
                         >
                    <EyeOff className="h-3 w-3" /> Ignore these
                  </Button>
                  <Button variant="subtle" size="xs" onClick={() => setPicked([])}>
                    clear
                  </Button>
                </>
              )}
              {(dismiss.isError || restore.isError) && (
                <span className="font-medium text-danger-fg">
                  Could not save that — you may not have permission to set payables aside.
                </span>
              )}
            </div>
            <div className="max-h-[52vh] overflow-auto rounded-lg border border-line">
              <Table density="compact" className="min-w-[520px]">
                <THead className="sticky top-0">
                  <Tr className="text-2xs text-fg-muted">
                    <Th className="w-7">
                      <Checkbox aria-label="Select all"
                             checked={selectable.length > 0 && picked.length === selectable.length}
                             onChange={(e) => setPicked(
                               e.target.checked ? selectable.map((b) => b.bill_no) : [])} />
                    </Th>
                    <Th>Bill</Th>
                    <Th>Date</Th>
                    <Th>Invoice</Th>
                    <Th align="right">Billed</Th>
                    <Th align="right">Paid against it</Th>
                    <Th align="right">Still open</Th>
                  </Tr>
                </THead>
                <TBody>
                  {bills.length === 0 ? (
                    <Tr><Td align="center" colSpan={7} className="text-fg-muted">
                      {ignoredBills.length > 0 && hideIgnored
                        ? <>Nothing left — all {ignoredBills.length} open bills have been ignored.</>
                        : <>None.</>}
                    </Td></Tr>
                  ) : bills.map((b) => {
                    const covered = Number(b.paid_against ?? 0) >= Number(b.money_cr ?? 0)
                      && Number(b.money_cr ?? 0) > 0
                    const gone = !!b.dismissed
                    return (
                      <Tr key={b.bill_no}
                          onClick={() => setLinkedBill(linkedBill === b.bill_no ? null : b.bill_no)}
                          className={cn('cursor-pointer border-t border-line-subtle',
                                        covered && !gone && 'bg-warning-soft/60',
                                        gone && 'text-fg-muted',
                                        linkedBill === b.bill_no && 'ring-2 ring-inset ring-primary-700')}>
                        <Td onClick={(e) => e.stopPropagation()}>
                          {gone ? (
                            <Button variant="subtle" size="icon-xs" onClick={() => restore.mutate([b.bill_no])}
                                    title="Put this bill back on the list"
                                    aria-label={`Restore ${b.bill_no}`}
                                   >
                              <Undo2 className="h-3.5 w-3.5" />
                            </Button>
                          ) : (
                            <Checkbox aria-label={`Select ${b.bill_no}`}
                                   checked={picked.includes(b.bill_no)}
                                   onChange={(e) => setPicked(
                                     e.target.checked
                                       ? [...picked, b.bill_no]
                                       : picked.filter((x) => x !== b.bill_no))} />
                          )}
                        </Td>
                        <Td className="font-mono">
                          {b.bill_no}
                          {b.payment_lines > 0 && (
                            <span className="ml-1.5 rounded bg-primary-100 px-1 py-0.5 text-2xs font-semibold text-primary-700">
                              {b.payment_lines} pmt
                            </span>
                          )}
                          {/* Who judged it and why, on the row itself: a
                              dismissal nobody can trace is worse than none. */}
                          {b.dismissed && (
                            <span title={[b.dismissed.note, b.dismissed.by, b.dismissed.at && formatDate(b.dismissed.at)]
                                    .filter(Boolean).join(' · ')}
                                  className="ml-1.5 rounded bg-surface-strong px-1 py-0.5 text-2xs font-medium text-fg-muted">
                              ignored
                            </span>
                          )}
                          <BillEvidence jv={b.jv_matches} other={b.other_currency} />
                        </Td>
                        <Td className="whitespace-nowrap text-fg-secondary">{day(b.bill_date)}</Td>
                        <Td className="font-mono text-fg-secondary"><BillInvoices invoices={b.invoices} /></Td>
                        <Td align="right" className="font-mono tabular-nums">{money(b.money_cr)}</Td>
                        <Td className={cn('px-2.5 py-1.5 text-right font-mono tabular-nums',
                                          covered ? 'font-semibold text-warning-fg' : 'text-fg-muted')}>
                          {money(b.paid_against)}
                        </Td>
                        <Td className={cn('px-2.5 py-1.5 text-right font-mono tabular-nums font-semibold',
                                          gone && 'font-normal line-through')}>
                          {money(b.money_bal)}
                        </Td>
                      </Tr>
                    )
                  })}
                </TBody>
                {data?.open_bills_total && (
                  <TFoot className="sticky bottom-0">
                    {/* NC's own figure stays on screen whatever finance has
                        set aside, and the subtraction is shown as a line of
                        its own. A netted total with no working is how a
                        number here stops being reconcilable to an NC report. */}
                    <Tr className="border-t-2">
                      <Td className="text-2xs font-semibold text-fg-secondary" colSpan={4}>
                        {data.open_bills_total.bills} bills · {currency}
                      </Td>
                      <Td className={footCell}>{money(data.open_bills_total.money_cr)}</Td>
                      <Td className={footCell}>{money(data.open_bills_total.paid_against)}</Td>
                      <Td className={footCell}>{money(data.open_bills_total.money_bal)}</Td>
                    </Tr>
                    {data.open_bills_total.ignored_bills > 0 && (
                      <>
                        <Tr className="text-fg-muted">
                          <Td className="text-2xs" colSpan={6}>
                            less {data.open_bills_total.ignored_bills} ignored
                          </Td>
                          <Td align="right" className="font-mono text-xs tabular-nums">
                            &minus;{money(data.open_bills_total.ignored_bal)}
                          </Td>
                        </Tr>
                        <Tr className="bg-surface">
                          <Td className="text-2xs font-semibold text-fg-secondary" colSpan={6}>
                            Still to work
                          </Td>
                          <Td align="right" className="font-mono tabular-nums font-semibold text-primary-700">
                            {money(data.open_bills_total.money_bal_after_ignored)}
                          </Td>
                        </Tr>
                      </>
                    )}
                  </TFoot>
                )}
              </Table>
            </div>
          </div>

          <div className="min-w-0">
            <h3 className="mb-1 text-sm font-semibold text-fg">Payments recorded for this supplier</h3>
            <p className="mb-2 text-2xs leading-relaxed text-fg-muted">
              Each payment carries the payable it cleared. Click either side to light up its
              counterpart.{' '}
              {paysToOpen === 0
                ? <>None of these cleared a bill still listed opposite — expected, since a payment
                    closes what it pays, so the two lists barely overlap.</>
                : <span className="font-medium text-warning-fg">{paysToOpen} of them cleared a bill
                    that is STILL flagged open — paid, but never applied.</span>}
            </p>
            <div className="max-h-[52vh] overflow-auto rounded-lg border border-line">
              <Table density="compact" className="min-w-[440px]">
                <THead className="sticky top-0">
                  <Tr className="text-2xs text-fg-muted">
                    <Th>Payment</Th>
                    <Th>Document date</Th>
                    <Th align="right">Amount</Th>
                    <Th>Applied to payable</Th>
                  </Tr>
                </THead>
                <TBody>
                  {pays.length === 0 ? (
                    <Tr><Td align="center" colSpan={4} className="text-fg-muted">None.</Td></Tr>
                  ) : pays.map((p, i) => (
                    <Tr key={p.bill_no + i}
                        onClick={() => setLinkedBill(
                          linkedBill === p.applied_to_bill_no ? null : p.applied_to_bill_no)}
                        className={cn('border-t border-line-subtle',
                                      p.applied_to_bill_no && 'cursor-pointer',
                                      linkedBill && p.applied_to_bill_no === linkedBill
                                        && 'bg-primary-50 ring-2 ring-inset ring-primary-700')}>
                      <Td className="font-mono">{p.bill_no}</Td>
                      {/* NC leaves PAYDATE empty on every payment line in this
                          database, so the document date is the only date there
                          is — say which one it is rather than showing a dash. */}
                      <Td className="text-fg-secondary">{day(p.pay_date ?? p.doc_date)}</Td>
                      <Td align="right" className="font-mono tabular-nums">{money(p.money_de)}</Td>
                      <Td className="font-mono">
                        {p.applied_to_bill_no ? (
                          <>
                            <span className={cn(p.applied_bill_is_open
                                                  ? 'font-semibold text-warning-fg' : 'text-fg-secondary')}>
                              {p.applied_to_bill_no}
                            </span>
                            {Number(p.applied_bill_still_open ?? 0) !== 0 && (
                              <span className="ml-1.5 rounded bg-warning-soft px-1 py-0.5 text-2xs font-semibold text-warning-fg">
                                still open
                              </span>
                            )}
                          </>
                        ) : <span className="text-fg-muted">none</span>}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
                {data?.payments_total && (
                  <TFoot className="sticky bottom-0">
                    <Tr className="border-t-2">
                      <Td className="text-2xs font-semibold text-fg-secondary" colSpan={2}>
                        {data.payments_total.lines} payment lines · {currency}
                      </Td>
                      <Td className={footCell}>{money(data.payments_total.money_de)}</Td>
                      <Td></Td>
                    </Tr>
                  </TFoot>
                )}
              </Table>
            </div>
          </div>
        </div>
        </>
      )}

      {confirming && (
        <DismissDialog billNos={picked} count={picked.length} total={pickedTotal} currency={currency}
                       onCancel={() => setConfirming(false)}
                       onDone={(reason, note) =>
                         dismiss.mutate({ bill_nos: picked, reason, note })} />
      )}

      {hasGap && gl && (
        <div className="border-t border-line px-5 pb-5 pt-4">
          <h3 className="mb-1 text-sm font-semibold text-fg">
            Journal entries on this supplier&rsquo;s payable account
          </h3>
          <p className="mb-2.5 max-w-3xl text-xs leading-relaxed text-fg-muted">
            Vouchers that moved the payable without an AP document — where a difference settled
            by a manual entry shows up. {gl.matching > 0
              ? <span className="font-medium text-primary-700">{gl.matching} of them is for exactly
                  the difference.</span>
              : <>None is for exactly the difference, so it is either the net of several or was
                  settled another way.</>}
          </p>
          {gl.offset_lines > 0 && (
            <label className="mb-2 inline-flex items-center gap-1.5 text-xs text-fg-secondary">
              <Checkbox checked={hideOffsets}
                     onChange={(e) => setHideOffsets(e.target.checked)} />
              Hide {gl.offset_lines} entries that cancel each other out
              <span className="text-fg-muted">
                ({gl.total - gl.offset_lines} left)
              </span>
            </label>
          )}
          <div className="max-h-[38vh] overflow-auto rounded-lg border border-line">
            <Table density="compact" className="min-w-[640px]">
              <THead className="sticky top-0">
                <Tr className="text-2xs text-fg-muted">
                  <Th>Voucher</Th>
                  <Th>Date</Th>
                  <Th>Source</Th>
                  <Th>Account</Th>
                  <Th align="right">Debit</Th>
                  <Th align="right">Credit</Th>
                  <Th>Narration</Th>
                </Tr>
              </THead>
              <TBody>
                {gl.items.length === 0 ? (
                  <Tr><Td align="center" colSpan={7} className="text-fg-muted">
                    No journal entries on this supplier&rsquo;s payable account.</Td></Tr>
                ) : gl.items
                      .filter((r) => !(hideOffsets && r.offset_group))
                      .map((r, i) => (
                  <Tr key={r.jv_number + i}
                      className={cn('border-t border-line-subtle',
                                    r.matches_gap && 'bg-primary-50/70',
                                    r.offset_group && 'text-fg-muted')}>
                    <Td className="font-mono">
                      {r.jv_number}
                      {r.offset_group && (
                        <span className="ml-1.5 rounded bg-surface-strong px-1 py-0.5 text-2xs font-medium text-fg-muted">
                          offset
                        </span>
                      )}
                      {r.matches_gap && (
                        <span className="ml-1.5 rounded bg-primary-700 px-1 py-0.5 text-2xs font-semibold text-white">
                          matches
                        </span>
                      )}
                    </Td>
                    <Td className="text-fg-secondary">{day(r.voucher_date)}</Td>
                    <Td className="font-mono text-fg-muted">{r.subsystem ?? '—'}</Td>
                    <Td className="font-mono text-fg-secondary">
                      {r.account_code}
                      {/* A voucher in another currency against this gap is
                          worth seeing, not hiding — sometimes it IS the answer. */}
                      {!r.same_currency && (
                        <span className="ml-1 text-2xs text-warning-fg">{r.currency}</span>
                      )}
                    </Td>
                    <Td align="right" className="font-mono tabular-nums">{money(r.debit)}</Td>
                    <Td align="right" className="font-mono tabular-nums">{money(r.credit)}</Td>
                    <Td className="text-fg-secondary">{r.summary ?? '—'}</Td>
                  </Tr>
                ))}
              </TBody>
              {/* One subtotal PER CURRENCY. This list is not filtered by
                  currency on purpose, so a single combined figure would add
                  CAD to USD — and for Aptargroup every one of the 66 lines is
                  USD against a CAD gap, which the split makes obvious. */}
              {gl.totals.length > 0 && (
                <TFoot className="sticky bottom-0">
                  {gl.totals.map((t) => (
                    <Tr key={t.currency ?? 'none'} className="border-t-2">
                      <Td className="text-2xs font-semibold text-fg-secondary" colSpan={3}>
                        {hideOffsets ? t.lines_after_offsets : t.lines} lines · {t.currency ?? 'no currency'}
                        {t.same_currency
                          ? <span className="ml-1.5 text-2xs font-normal text-fg-muted">supplier currency</span>
                          : <span className="ml-1.5 text-2xs font-normal text-warning-fg">other currency</span>}
                      </Td>
                      <Td className="text-2xs text-fg-muted">net</Td>
                      <Td className={footCell}>{money(t.debit)}</Td>
                      <Td className={footCell}>{money(t.credit)}</Td>
                      <Td className={cn('px-2.5 py-2 font-mono tabular-nums font-semibold',
                                        t.net_matches_gap ? 'text-primary-700' : 'text-fg')}>
                        {money(hideOffsets ? t.net_after_offsets : t.net)}
                        {(hideOffsets ? t.net_after_offsets_matches_gap : t.net_matches_gap) && (
                          <span className="ml-1.5 rounded bg-primary-700 px-1 py-0.5 text-2xs text-white">
                            = difference
                          </span>
                        )}
                      </Td>
                    </Tr>
                  ))}
                </TFoot>
              )}
            </Table>
          </div>
        </div>
      )}
    
    </ShellModal>
  )
}

/**
 * Open bills across every supplier, filtered by date — and judged in bulk.
 *
 * The supplier view answers "is this supplier's balance believable". This one
 * answers the question the data kept raising instead: which DOCUMENTS are not
 * real debt. They cluster by date, not by supplier — 88 CAD bills carrying
 * 3,058,570.38 all dated 2020-08-31, NC's opening-balance load at go-live,
 * spread across 44 suppliers. One supplier at a time that is a week of
 * clicking; by date it is one filter.
 *
 * The cluster strip exists because nobody would think to type 2020-08-31. The
 * date has to announce itself.
 *
 * Bulk is where this could do real damage, so the action states the exact
 * count and amount, and the server refuses it if the set has moved since the
 * screen was drawn.
 */
function BillsByDate({ currency }: { currency: string }) {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [showIgnored, setShowIgnored] = useState(false)
  // Ticked bills, by bill_no -> open balance. Kept across pages (the total of
  // the selection must not depend on which page is on screen); cleared when
  // the filter changes, as before.
  const [picked, setPicked] = useState<Record<string, string>>({})
  const [confirming, setConfirming] = useState<'picked' | 'all' | null>(null)
  const qc = useQueryClient()

  const qs = `currency=${currency}&include_dismissed=${showIgnored}` +
    `${from ? `&date_from=${from}` : ''}${to ? `&date_to=${to}` : ''}`
  // Paged and sorted by the API; a new filter or sort goes back to page 1.
  const [pageAt, setPageAt] = useState({ key: qs, page: 1 })
  const page = pageAt.key === qs ? pageAt.page : 1
  const setPage = (p: number) => setPageAt({ key: qs, page: p })
  const [sort, onSort] = useSort<BillSortKey>(null, () => setPage(1))

  const { data: clusters } = useQuery({
    queryKey: ['ap-ledger-clusters', currency],
    queryFn: () => financeApi.get<ClustersResp>(
      `/ap-ledger-health/date-clusters?currency=${currency}&limit=10`),
  })
  const { data, isFetching, error } = useQuery({
    queryKey: ['ap-ledger-bills', currency, from, to, showIgnored, page, sort],
    queryFn: () => financeApi.get<BillsResp>(
      `/ap-ledger-health/bills?${qs}&limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}${sortQs(sort)}`),
  })

  const refresh = () => {
    setPicked({})
    setConfirming(null)
    invalidateApJudgement(qc)
  }
  const dismissSome = useMutation({
    mutationFn: (v: { bill_nos: string[]; reason: string; note: string }) =>
      financeApi.post('/ap-ledger-health/dismiss', v),
    onSuccess: refresh,
  })
  // The filter-wide action. Carries the count the screen showed, so a set that
  // moved underneath is refused rather than silently over-applied.
  const dismissAll = useMutation({
    mutationFn: (v: { reason: string; note: string }) =>
      financeApi.post('/ap-ledger-health/dismiss-matching', {
        ...v, currency, date_from: from || null, date_to: to || null,
        // The server-side count the screen showed, not the rows on this page.
        expected_bills: live,
      }),
    onSuccess: refresh,
  })
  const restore = useMutation({
    mutationFn: (bill_nos: string[]) => financeApi.post('/ap-ledger-health/restore', { bill_nos }),
    onSuccess: refresh,
  })

  const rows = data?.items ?? []                       // this page only
  const selectable = rows.filter((b) => !b.dismissed)
  const ignoredRows = rows.filter((b) => b.dismissed)
  const pickedNos = Object.keys(picked)
  const pickedTotal = Object.values(picked).reduce((t, v) => t + Number(v), 0)
  // Live = matching and not already ignored, over the WHOLE filter (server totals).
  const live = (data?.total ?? 0) - (data?.dismissed_bills ?? 0)
  const liveTotal = Number(data?.money_bal ?? 0) - Number(data?.dismissed_bal ?? 0)
  const narrowed = !!(from || to)
  // The server refuses a filter-wide judgement over MAX_PER_ACTION bills, and
  // /dismiss takes at most 500 at a time — say so here rather than 409/422.
  const capped = live > MAX_PER_ACTION
  const tooManyPicked = pickedNos.length > 500
  const err = dismissSome.error || dismissAll.error || restore.error
  const togglePick = (b: FlatBill, on: boolean) => setPicked((p) => {
    const n = { ...p }
    if (on) n[b.bill_no] = b.money_bal; else delete n[b.bill_no]
    return n
  })
  const pageAllPicked = selectable.length > 0 && selectable.every((b) => b.bill_no in picked)

  const pick = (d: ClusterRow) => { setFrom(d.bill_date); setTo(d.bill_date); setPicked({}) }

  return (
    <>
      {/* Discovery. Nobody types 2020-08-31 from memory. */}
      <div className="mb-3">
        <p className="mb-1.5 text-xs text-fg-muted">
          Dates carrying the most open balance. A single day held by dozens of suppliers is a
          migration load, not trading.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {(clusters?.items ?? []).map((c) => (
            <ChoiceCard key={c.bill_date} selected={from === c.bill_date && to === c.bill_date} onClick={() => pick(c)}
                    className="block rounded-lg px-2.5 py-1.5 text-xs">
              <span className="font-mono">{day(c.bill_date)}</span>
              <span className="ml-2 font-mono tabular-nums font-semibold">{money(c.amount)}</span>
              <span className="ml-2 text-fg-muted">
                {c.bills} bills · {c.suppliers} suppliers
                {c.dismissed_bills > 0 && <>, {c.dismissed_bills} ignored</>}
              </span>
            </ChoiceCard>
          ))}
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-end gap-3 rounded-lg border border-line bg-surface-subtle px-3 py-2.5">
        <label className="text-xs text-fg-secondary">
          <span className="mb-1 block font-medium">Bill date from</span>
          <Input inputSize="sm" type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPicked({}) }}
                 />
        </label>
        <label className="text-xs text-fg-secondary">
          <span className="mb-1 block font-medium">to</span>
          <Input inputSize="sm" type="date" value={to} onChange={(e) => { setTo(e.target.value); setPicked({}) }}
                 />
        </label>
        {narrowed && (
          <Button variant="subtle" size="xs" onClick={() => { setFrom(''); setTo(''); setPicked({}) }}
                 >clear dates</Button>
        )}
        <label className="flex items-center gap-1.5 pb-1 text-xs text-fg-secondary">
          <Checkbox checked={showIgnored}
                 onChange={(e) => { setShowIgnored(e.target.checked); setPicked({}) }} />
          Show bills already ignored
        </label>
        <div className="ml-auto pb-0.5 text-right text-xs">
          <div className="font-mono tabular-nums text-base font-semibold text-fg">
            {money(String(liveTotal))} {currency}
          </div>
          <div className="text-fg-muted">
            {(data?.total ?? 0) - (data?.dismissed_bills ?? 0)} bills match
            {(data?.dismissed_bills ?? 0) > 0 && (
              <span className="text-fg-muted"> · {data!.dismissed_bills} already ignored</span>
            )}
          </div>
        </div>
      </div>

      <div className="mb-2 flex flex-wrap items-center gap-3 text-xs">
        {pickedNos.length > 0 ? (
          <>
            <span className="font-medium text-fg-secondary">
              {pickedNos.length} selected · {money(String(pickedTotal))} {currency}
            </span>
            <Button variant="outline" size="xs" onClick={() => setConfirming('picked')} disabled={tooManyPicked}
                   >
              <EyeOff className="h-3 w-3" /> Ignore selected
            </Button>
            <Button variant="subtle" size="xs" onClick={() => setPicked({})}>clear</Button>
            {tooManyPicked && (
              <span className="text-warning-fg">At most 500 bills at a time — clear some, or ignore the whole filter.</span>
            )}
          </>
        ) : narrowed && live > 0 ? (
          <>
            <span className="text-fg-muted">
              Tick the bills that are not real debt, or judge the whole filter at once.
            </span>
            <Button variant="secondary" size="xs" onClick={() => setConfirming('all')} disabled={capped}
                   >
              <EyeOff className="h-3 w-3" /> Ignore all {live} matching
            </Button>
          </>
        ) : (
          <span className="text-fg-muted">
            Pick a date above, or set a range, to judge a group of bills at once.
          </span>
        )}
        {/* A bulk action with no bulk undo is a trap: 88 bills judged in one
            click would otherwise take 88 clicks to take back. Only offered
            while the ignored rows are actually on screen. */}
        {showIgnored && ignoredRows.length > 0 && pickedNos.length === 0 && (
          <Button variant="secondary" size="xs" onClick={() => restore.mutate(ignoredRows.map((b) => b.bill_no))}
                 >
            <Undo2 className="h-3 w-3" /> Restore the {ignoredRows.length} ignored on this page
          </Button>
        )}
        {capped && (
          <span className="text-warning-fg">
            {live} bills match — more than {MAX_PER_ACTION} cannot be ignored in one go; narrow the dates before
            judging the whole filter.
          </span>
        )}
        {err != null && (
          <span className="font-medium text-danger-fg">
            Could not save that: {String((err as Error).message ?? err)}
          </span>
        )}
      </div>

      <div className="overflow-x-auto rounded-lg border border-line">
        <Table density="compact" className="min-w-[980px]">
          <THead>
            <Tr className="bg-surface-subtle text-xs text-fg-muted">
              <Th className="w-8">
                <Checkbox aria-label="Select all shown"
                       checked={pageAllPicked}
                       onChange={(e) => selectable.forEach((b) => togglePick(b, e.target.checked))} />
              </Th>
              <SortTh label="Bill" sortKey="bill_no" sort={sort} onSort={onSort} className="px-3 py-2 font-medium text-left" />
              <SortTh label="Date" sortKey="bill_date" sort={sort} onSort={onSort} className="px-3 py-2 font-medium text-left" />
              <SortTh label="Supplier" sortKey="supplier" sort={sort} onSort={onSort} className="px-3 py-2 font-medium text-left" />
              <SortTh label="Invoice" sortKey="invoice_no" sort={sort} onSort={onSort} className="px-3 py-2 font-medium text-left" />
              <SortTh label="PO" sortKey="purchase_order" sort={sort} onSort={onSort} className="px-3 py-2 font-medium text-left" />
              <SortTh label="Billed" sortKey="money_cr" sort={sort} onSort={onSort} align="right" className="px-3 py-2 font-medium" />
              <SortTh label="Still open" sortKey="money_bal" sort={sort} onSort={onSort} align="right" className="px-3 py-2 font-medium" />
            </Tr>
          </THead>
          <TBody>
            {isFetching && !data ? (
              <Tr><Td align="center" colSpan={8}>
                <Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></Td></Tr>
            ) : error ? (
              <Tr><Td align="center" colSpan={8} className="text-danger-fg">
                Could not load these bills: {String((error as Error).message ?? error)}
              </Td></Tr>
            ) : rows.length === 0 ? (
              <Tr><Td align="center" colSpan={8} className="text-fg-muted">
                {/* An emptied list after a bulk judgement must say what
                    happened to the money, not just go blank. */}
                {(data?.dismissed_bills ?? 0) > 0
                  ? <>All {data!.dismissed_bills} bills matching this filter have been
                      ignored — {money(data!.dismissed_bal)} {currency}. Tick
                      &lsquo;Show bills already ignored&rsquo; to see or undo them.</>
                  : <>No open bills match.</>}
              </Td></Tr>
            ) : rows.map((b) => {
              const gone = !!b.dismissed
              return (
                <Tr key={b.bill_no}
                    className={cn('border-t border-line-subtle hover:bg-surface-subtle/60',
                                  gone && 'text-fg-muted')}>
                  <Td>
                    {gone ? (
                      <Button variant="subtle" size="icon-xs" onClick={() => restore.mutate([b.bill_no])}
                              title="Put this bill back on the list"
                              aria-label={`Restore ${b.bill_no}`}
                             >
                        <Undo2 className="h-3.5 w-3.5" />
                      </Button>
                    ) : (
                      <Checkbox aria-label={`Select ${b.bill_no}`}
                             checked={b.bill_no in picked}
                             onChange={(e) => togglePick(b, e.target.checked)} />
                    )}
                  </Td>
                  <Td className="font-mono text-xs">
                    {b.bill_no}
                    {b.dismissed && (
                      <span title={[b.dismissed.note, b.dismissed.by, b.dismissed.at && formatDate(b.dismissed.at)]
                              .filter(Boolean).join(' · ')}
                            className="ml-1.5 rounded bg-surface-strong px-1 py-0.5 text-2xs font-medium text-fg-muted">
                        ignored
                      </span>
                    )}
                    <BillEvidence jv={b.jv_matches} other={b.other_currency} />
                  </Td>
                  <Td className="whitespace-nowrap text-xs text-fg-secondary">{day(b.bill_date)}</Td>
                  <Td>
                    <div className={cn(gone ? 'text-fg-muted' : 'text-fg')}>
                      {b.supplier_name ?? '—'}
                    </div>
                    <div className="font-mono text-2xs text-fg-muted">{b.supplier_code ?? '—'}</div>
                  </Td>
                  <Td className="font-mono text-xs text-fg-secondary"><BillInvoices invoices={b.invoices} /></Td>
                  <Td className="font-mono text-xs text-fg-muted">{billPurchaseOrders(b.invoices).join(', ') || '—'}</Td>
                  <Td align="right" className="font-mono tabular-nums text-fg-secondary">{money(b.money_cr)}</Td>
                  <Td className={cn('px-3 py-2 text-right font-mono tabular-nums font-semibold',
                                    gone && 'font-normal line-through')}>
                    {money(b.money_bal)}
                  </Td>
                </Tr>
              )
            })}
          </TBody>
        </Table>
        {/* `total` counts ignored bills too; the rows leave them out unless shown. */}
        <ListPager page={page} pageSize={PAGE_SIZE} total={showIgnored ? (data?.total ?? 0) : live} noun="bills"
                   onPage={setPage}
                   className="flex items-center justify-between border-t border-line-subtle px-3 py-2 text-sm text-fg-muted" />
      </div>

      {confirming && (
        <DismissDialog
          billNos={confirming === 'picked' ? pickedNos : null}
          count={confirming === 'picked' ? pickedNos.length : live}
          total={confirming === 'picked' ? pickedTotal : liveTotal}
          currency={currency}
          onCancel={() => setConfirming(null)}
          onDone={(reason, note) => confirming === 'picked'
            ? dismissSome.mutate({ bill_nos: pickedNos, reason, note })
            : dismissAll.mutate({ reason, note })} />
      )}
    </>
  )
}

export default function ApLedgerHealthPage() {
  const { user } = useAuthStore()
  const [currency, setCurrency] = useState('CAD')
  // The approved payable is the page's subject. The abandoned documents are
  // not work — they are an exclusion that has to be declared, not worked.
  const [view, setView] = useState<'suppliers' | 'bills' | 'abandoned'>('suppliers')
  const [open, setOpen] = useState<{ code: string; name: string | null; gap: string | null } | null>(null)
  const [vendorQ, setVendorQ] = useState('')

  const { data: summary, isFetching: loadingSummary } = useQuery({
    queryKey: ['ap-ledger-health-summary'],
    queryFn: () => financeApi.get<SummaryResp>('/ap-ledger-health/summary'),
  })
  // Vendor search runs on the server now that both lists are paged; debounced
  // so a fast typist does not fire a query per keystroke.
  const [debouncedQ, setDebouncedQ] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(vendorQ.trim()), 300)
    return () => clearTimeout(t)
  }, [vendorQ])
  const qParam = debouncedQ ? `&q=${encodeURIComponent(debouncedQ)}` : ''
  // A page number belongs to the currency + search it was turned on; a new
  // currency, a new search or a new sort starts again at page 1.
  const listKey = `${currency}|${debouncedQ}`
  const [supPageAt, setSupPageAt] = useState({ key: listKey, page: 1 })
  const [abPageAt, setAbPageAt] = useState({ key: listKey, page: 1 })
  const supPage = supPageAt.key === listKey ? supPageAt.page : 1
  const abPage = abPageAt.key === listKey ? abPageAt.page : 1
  const setSupPage = (page: number) => setSupPageAt({ key: listKey, page })
  const setAbPage = (page: number) => setAbPageAt({ key: listKey, page })
  const [supSort, onSupSort] = useSort<SupplierSortKey>(null, () => setSupPage(1))
  const [abSort, onAbSort] = useSort<AbandonedSortKey>(null, () => setAbPage(1))

  const { data: items, isFetching: loadingItems } = useQuery({
    queryKey: ['ap-ledger-health-items', currency, debouncedQ, supPage, supSort],
    queryFn: () => financeApi.get<ItemsResp>(
      `/ap-ledger-health/items?currency=${currency}&limit=${PAGE_SIZE}` +
      `&offset=${(supPage - 1) * PAGE_SIZE}${qParam}${sortQs(supSort)}`),
    enabled: view === 'suppliers',
  })
  const { data: abandoned, isFetching: loadingAbandoned } = useQuery({
    queryKey: ['ap-ledger-abandoned', currency, debouncedQ, abPage, abSort],
    queryFn: () => financeApi.get<AbandonedResp>(
      `/ap-ledger-health/abandoned?currency=${currency}&limit=${PAGE_SIZE}` +
      `&offset=${(abPage - 1) * PAGE_SIZE}${qParam}${sortQs(abSort)}`),
    enabled: view === 'abandoned',
  })

  const ccyRows = summary?.currencies ?? []
  // Not offered on 'Bills by date' — there "Ignore all N matching" acts on the
  // date filter, and a vendor search alongside it would make N ambiguous.
  const searching = debouncedQ !== ''
  const supplierRows = items?.items ?? []
  const abandonedRows = abandoned?.items ?? []

  // Every matching row (not just the page), in the on-screen sort and search.
  const fetchAll = async <T,>(path: string): Promise<T[]> => {
    const all: T[] = []
    for (let off = 0; ; off += 1000) {
      const page = await financeApi.get<{ total: number; items: T[] }>(
        `${path}&limit=1000&offset=${off}`)
      all.push(...page.items)
      if (all.length >= page.total || page.items.length === 0) return all
    }
  }

  const exportCsv = async () => {
    const allAbandoned = view === 'abandoned'
      ? await fetchAll<AbandonedRow>(
          `/ap-ledger-health/abandoned?currency=${currency}${qParam}${sortQs(abSort)}`)
      : []
    const allSuppliers = view === 'abandoned' ? [] : await fetchAll<SupplierRow>(
      `/ap-ledger-health/items?currency=${currency}${qParam}${sortQs(supSort)}`)
    const head = view === 'abandoned'
      ? ['Bill', 'Date', 'Supplier code', 'Supplier', 'Invoice no', 'Currency',
         'Billed', 'Balance carried', 'Bill status', 'Approve status', 'Duplicate of an approved bill']
      : ['Supplier code', 'Supplier', 'Currency', 'Health', 'Billed', 'Paid',
         'Subledger open', 'Billed minus paid', 'Gap',
         'Ignored bills', 'Ignored balance', 'Open after ignored']
    const body = view === 'abandoned'
      ? allAbandoned.map((r) => [
          r.bill_no, r.bill_date, r.supplier_code, r.supplier_name, invoicesCsv(r.invoices),
          r.currency, r.money_cr, r.money_bal, r.bill_status, r.approve_status,
          r.superseded ? 'yes' : 'no'])
      : allSuppliers.map((r) => [
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
        <div className="mb-5 rounded-lg border border-line bg-surface-subtle px-4 py-3 text-sm leading-relaxed text-fg-secondary">
          <p className="mb-1.5">
            Counting only the documents NC <strong>approved</strong>, the subledger is healthy — the open
            balance and billed-minus-paid agree to the cent for almost every supplier. That is the
            figure the cash-flow forecast is built on, and this page exists to keep it honest: any
            supplier where the two start to disagree shows up here.
          </p>
          <p className="text-fg-muted">
            Documents NC never approved are <strong>excluded</strong> and are not work: none of them has
            an approved payment against it, so no money ever moved. They are listed only so the
            exclusion is on the record — if a figure here ever differs from an NC report, this is
            the difference. Where the approved side does disagree,
            <strong> the judgement is finance&rsquo;s, per supplier.</strong>
          </p>
        </div>

        {loadingSummary && !summary ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
        ) : (
          <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {ccyRows.map((c) => {
              const bad = Number(c.inconsistent.gap) !== 0
              return (
                <ChoiceCard key={c.currency} selected={currency === c.currency} onClick={() => setCurrency(c.currency)}
                        className="items-stretch gap-0 rounded-lg p-3">
                  <div className="flex items-center gap-1.5">
                    {/* The health signal is the approved side disagreeing —
                        never the size of the excluded pile, which is inert. */}
                    {bad ? <AlertTriangle className="h-3.5 w-3.5 text-warning-fg" />
                         : <CheckCircle2 className="h-3.5 w-3.5 text-primary-700" />}
                    <span className="font-mono text-sm font-semibold">{c.currency}</span>
                  </div>
                  <dl className="mt-2 space-y-1 text-xs">
                    <div className="flex justify-between gap-2">
                      <dt className="text-fg-muted">Real payable</dt>
                      <dd className="font-mono tabular-nums">{money(c.consistent.subledger_open)}</dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt className="text-fg-muted">Approved, disagrees</dt>
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
                          <dt className="text-fg-muted">Ignored by finance</dt>
                          <dd className="font-mono tabular-nums text-fg-muted">
                            &minus;{money(c.consistent.ignored_bal)}
                          </dd>
                        </div>
                        <div className="flex justify-between gap-2 border-t border-line pt-1">
                          <dt className="font-medium text-fg-secondary">Net payable</dt>
                          <dd className="font-mono tabular-nums font-semibold text-primary-700">
                            {money(c.consistent.open_after_ignored)}
                          </dd>
                        </div>
                      </>
                    )}
                    <div className="flex justify-between gap-2 border-t border-line pt-1">
                      <dt className="text-fg-muted">Excluded (unapproved)</dt>
                      <dd className="font-mono tabular-nums text-fg-muted">{money(c.abandoned.money_bal)}</dd>
                    </div>
                  </dl>
                  <p className="mt-1.5 text-2xs text-fg-muted">
                    {c.consistent.suppliers} suppliers agree · {c.abandoned.bills} abandoned docs
                    {c.abandoned.superseded_bills > 0 && <>, {c.abandoned.superseded_bills} duplicate</>}
                    {c.ignored.bills > 0 && <> · {c.ignored.bills} bills ignored</>}
                  </p>
                </ChoiceCard>
              )
            })}
          </div>
        )}

        <div className="mb-3 flex flex-wrap items-center gap-2">
          {/* 'Bills by date': the same population as the first view, sliced by
              document instead of by supplier — because what is not real debt
              turned out to cluster by DATE, across dozens of suppliers at once. */}
          <Segmented<typeof view>
            size="md"
            items={[
              { key: 'suppliers', label: 'Approved suppliers' },
              { key: 'bills', label: 'Bills by date' },
              { key: 'abandoned', label: 'Excluded documents' },
            ]}
            value={view}
            onChange={setView}
          />
          <span className="text-sm text-fg-muted">
            {currency}
            {view === 'abandoned'
              ? ` · ${abandoned?.total ?? 0} documents${searching ? ' matching' : ''}`
              : view === 'suppliers'
                ? ` · ${items?.total ?? 0} suppliers${searching ? ' matching' : ''}` : ''}
          </span>
          {view !== 'bills' && (
            <div className="ml-auto flex items-center gap-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
                <Input type="search" value={vendorQ} onChange={(e) => setVendorQ(e.target.value)}
                       placeholder="Search vendor or invoice no." aria-label="Search vendor or invoice number"
                       className="w-64 pl-8 pr-2.5" />
              </div>
              <Button variant="secondary" size="sm" onClick={exportCsv}>
                <Download className="h-4 w-4" /> Export CSV
              </Button>
            </div>
          )}
        </div>

        {view === 'bills' ? (
          <BillsByDate currency={currency} />
        ) : view === 'abandoned' ? (
          <div className="overflow-x-auto rounded-lg border border-line">
            <Table density="compact" className="min-w-[880px]">
              <THead>
                <Tr className="bg-surface-subtle text-xs text-fg-muted">
                  <SortTh label="Bill" sortKey="bill_no" sort={abSort} onSort={onAbSort} className="px-3 py-2 font-medium text-left" />
                  <SortTh label="Date" sortKey="bill_date" sort={abSort} onSort={onAbSort} className="px-3 py-2 font-medium text-left" />
                  <SortTh label="Supplier" sortKey="supplier" sort={abSort} onSort={onAbSort} className="px-3 py-2 font-medium text-left" />
                  <SortTh label="Invoice no." sortKey="invoice_no" sort={abSort} onSort={onAbSort} className="px-3 py-2 font-medium text-left" />
                  <SortTh label="Billed" sortKey="money_cr" sort={abSort} onSort={onAbSort} align="right" className="px-3 py-2 font-medium" />
                  <SortTh label="Balance carried" sortKey="money_bal" sort={abSort} onSort={onAbSort} align="right" className="px-3 py-2 font-medium" />
                  <SortTh label="Status" sortKey="status" sort={abSort} onSort={onAbSort} className="px-3 py-2 font-medium text-left" />
                </Tr>
              </THead>
              <TBody>
                {loadingAbandoned && !abandoned ? (
                  <Tr><Td align="center" colSpan={7}>
                    <Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></Td></Tr>
                ) : abandonedRows.length === 0 ? (
                  <Tr><Td align="center" colSpan={7} className="text-fg-muted">
                    {searching
                      ? <>No vendor or invoice matching &ldquo;{vendorQ.trim()}&rdquo; in {currency}.</>
                      : <>No abandoned documents carrying a balance in {currency}.</>}</Td></Tr>
                ) : abandonedRows.map((r) => (
                  <Tr key={r.bill_no} className="hover:bg-surface-subtle/60">
                    <Td className="font-mono text-xs">{r.bill_no}</Td>
                    <Td className="text-xs text-fg-secondary">{day(r.bill_date)}</Td>
                    <Td>
                      <div className="text-fg">{r.supplier_name ?? '—'}</div>
                      <div className="font-mono text-2xs text-fg-muted">{r.supplier_code ?? '—'}</div>
                    </Td>
                    <Td className="font-mono text-xs text-fg-secondary"><BillInvoices invoices={r.invoices} /></Td>
                    <Td align="right" className="font-mono tabular-nums text-fg-secondary">{money(r.money_cr)}</Td>
                    <Td align="right" className="font-mono tabular-nums font-semibold text-warning-fg">{money(r.money_bal)}</Td>
                    <Td className="text-xs">
                      <span className="font-mono text-fg-muted">{r.bill_status}/{r.approve_status}</span>
                      {/* The cheapest ones to clear: an approved bill already
                          carries this supplier, amount and invoice number. */}
                      {r.superseded && (
                        <span className="ml-1.5 rounded bg-warning-soft px-1.5 py-0.5 text-2xs font-semibold text-warning-fg">
                          duplicate
                        </span>
                      )}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
            <ListPager page={abPage} pageSize={PAGE_SIZE} total={abandoned?.total ?? 0}
                       noun="documents" onPage={setAbPage}
                       className="flex items-center justify-between border-t border-line-subtle px-3 py-2 text-sm text-fg-muted" />
          </div>
        ) : (
        <div className="overflow-x-auto rounded-lg border border-line">
          <Table density="compact" className="min-w-[820px]">
            <THead>
              <Tr className="bg-surface-subtle text-xs text-fg-muted">
                <SortTh label="Supplier" sortKey="supplier" sort={supSort} onSort={onSupSort} className="px-3 py-2 font-medium text-left" />
                <SortTh label="Billed" sortKey="billed" sort={supSort} onSort={onSupSort} align="right" className="px-3 py-2 font-medium" />
                <SortTh label="Paid" sortKey="paid" sort={supSort} onSort={onSupSort} align="right" className="px-3 py-2 font-medium" />
                <SortTh label="Subledger open" sortKey="subledger_open" sort={supSort} onSort={onSupSort} align="right" className="px-3 py-2 font-medium" />
                <SortTh label={'Billed \u2212 paid'} sortKey="billed_minus_paid" sort={supSort} onSort={onSupSort} align="right" className="px-3 py-2 font-medium" />
                <SortTh label="Gap" sortKey="gap" sort={supSort} onSort={onSupSort} align="right" className="px-3 py-2 font-medium" />
                <Th></Th>
              </Tr>
            </THead>
            <TBody>
              {loadingItems && !items ? (
                <Tr><Td align="center" colSpan={7}>
                  <Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></Td></Tr>
              ) : supplierRows.length === 0 ? (
                <Tr><Td align="center" colSpan={7} className="text-fg-muted">
                  {searching
                    ? <>No vendor or invoice matching &ldquo;{vendorQ.trim()}&rdquo; in {currency}.</>
                    : <>No suppliers in this state for {currency}.</>}</Td></Tr>
              ) : supplierRows.map((r) => {
                // A supplier whose whole balance has been judged away is not
                // work any more. It stays listed and states what was ignored —
                // dropping the row would make the page disagree with NC with
                // nothing on screen to explain why.
                const cleared = Number(r.open_after_ignored) === 0 && r.ignored_bills > 0
                return (
                <Tr key={(r.supplier_code ?? '') + r.currency}
                    className={cn('border-t border-line-subtle hover:bg-surface-subtle/60',
                                  cleared && 'text-fg-muted')}>
                  <Td>
                    <div className={cn(cleared ? 'text-fg-muted' : 'text-fg')}>
                      {r.supplier_name ?? '—'}
                      {cleared && (
                        <span className="ml-1.5 rounded bg-surface-strong px-1.5 py-0.5 text-2xs font-medium text-fg-muted">
                          all ignored
                        </span>
                      )}
                    </div>
                    <div className="font-mono text-2xs text-fg-muted">{r.supplier_code ?? '—'}</div>
                    {r.invoice_hits.map((h) => (
                      <div key={h.bill_no} className="mt-0.5 text-2xs text-primary-700">
                        <span className="font-mono">{h.invoice_no}</span> on <span className="font-mono">{h.bill_no}</span>
                        {' · '}{day(h.bill_date)} · billed {money(h.money_cr)} ·{' '}
                        {Number(h.money_bal) === 0 ? 'settled' : `open ${money(h.money_bal)}`}
                        {h.dismissed && ' · ignored'}
                      </div>
                    ))}
                  </Td>
                  <Td align="right" className="font-mono tabular-nums text-fg-secondary">{money(r.billed)}</Td>
                  <Td align="right" className="font-mono tabular-nums text-fg-secondary">{money(r.paid)}</Td>
                  <Td align="right" className="font-mono tabular-nums">
                    {money(r.subledger_open)}
                    {r.ignored_bills > 0 && (
                      <div className="text-2xs text-fg-muted">
                        &minus;{money(r.ignored_bal)} ignored → {money(r.open_after_ignored)}
                      </div>
                    )}
                  </Td>
                  <Td align="right" className="font-mono tabular-nums">{money(r.billed_minus_paid)}</Td>
                  <Td className={cn('px-3 py-2 text-right font-mono tabular-nums font-semibold',
                                    Number(r.gap) === 0 ? 'text-fg-muted' : 'text-warning-fg')}>
                    {money(r.gap)}
                  </Td>
                  <Td>
                    {r.supplier_code && (
                      <Button variant="link" onClick={() => setOpen({ code: r.supplier_code!, name: r.supplier_name, gap: r.gap })}
                              className="text-xs">
                        Show documents
                      </Button>
                    )}
                  </Td>
                </Tr>
                )
              })}
            </TBody>
          </Table>
          <ListPager page={supPage} pageSize={PAGE_SIZE} total={items?.total ?? 0}
                     noun="suppliers" onPage={setSupPage}
                     className="flex items-center justify-between border-t border-line-subtle px-3 py-2 text-sm text-fg-muted" />
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
