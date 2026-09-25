/**
 * AP Duplicate Invoices — invoice numbers NC has on more than one payable.
 *
 * NC65 does not check that an invoice number is unique, and the same invoice
 * has been entered and paid twice. NC cannot be changed from here, so this page
 * is the check NC does not have: it is recomputed from the NC mirror on every
 * load, and the hourly AP sync raises one standing task for AP when something
 * new appears (finance-api services/ap_duplicate_invoice_tasks.py).
 *
 * The status column is what AP has to do, in the order it gets more expensive:
 *   Stop approval  — a draft repeats an invoice already entered; do not approve it
 *   Stop payment   — an approved extra copy still has a balance; hold it back
 *   Recover        — every copy is settled; the supplier was paid twice
 *   Review         — same number, different amounts; usually a deliberate split
 *
 * A group the reviewer confirms is fine is marked reviewed with a reason. The
 * verdict covers the bills that were on screen: if another copy lands later,
 * the group comes back.
 */
import { Fragment, useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Download, Loader2, Undo2 } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

type Kind = 'exact' | 'cross_supplier' | 'pending' | 'amount_differs'
type Status = 'stop_approval' | 'stop_payment' | 'recover' | 'review'

interface CcyTotals { findings: number; extra_amount: string; open_exposure: string }
interface KindSummary {
  label: string; actionable: boolean; unreviewed: number; reviewed: number
  by_currency: Record<string, CcyTotals>
}
interface Bill {
  bill_no: string; effective: boolean; status_label: string; trade_type: string | null
  supplier_code: string | null; supplier_name: string | null; currency: string | null
  invoice_no: string | null; bill_date: string | null; amount: string; open: string
  dismissed: boolean
  /** Approved NC payments against the whole bill (a bill may carry several invoices). */
  paid_on_bill: string | null; payment_nos: string | null; last_paid: string | null
}
interface Review {
  reason: string; note: string | null; reviewed_by_name: string | null; reviewed_at: string | null
}
interface Finding {
  key: string; kind: Kind; kind_label: string; status: Status
  invoice_no: string | null; invoice_no_norm: string; currency: string | null
  supplier_code: string | null; supplier_name: string | null; amount: string | null
  extra_copies: number; extra_amount: string; open_exposure: string
  bill_nos: string[]; reviewed: boolean; review: Review | null; reopened: boolean
  bills: Bill[]
}
interface Resp { summary: Record<Kind, KindSummary>; total: number; findings: Finding[] }
interface Reason { code: string; label: string }

const KIND_ORDER: Kind[] = ['exact', 'cross_supplier', 'pending', 'amount_differs']
const KIND_TAB: Record<Kind, string> = {
  exact: 'Same invoice, same amount',
  cross_supplier: 'Across supplier codes',
  pending: 'Unapproved drafts',
  amount_differs: 'Different amounts',
}
const STATUS: Record<Status, { label: string; cls: string; hint: string }> = {
  stop_approval: { label: 'Stop approval', cls: 'bg-red-50 text-red-700 border-red-200',
                   hint: 'A draft repeats an invoice already entered — do not approve it' },
  stop_payment: { label: 'Stop payment', cls: 'bg-red-50 text-red-700 border-red-200',
                  hint: 'An extra copy still has a balance in NC — hold it back from payment' },
  recover: { label: 'Recover', cls: 'bg-amber-50 text-amber-800 border-amber-200',
             hint: 'Every copy is settled — the supplier was paid more than once' },
  review: { label: 'Review', cls: 'bg-neutral-50 text-neutral-600 border-neutral-200',
            hint: 'Same number, different amounts — usually one invoice split on purpose' },
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

function ReviewDialog({ finding, pending, error, onCancel, onDone }: {
  finding: Finding; pending: boolean; error: string | null
  onCancel: () => void; onDone: (reason: string, note: string) => void
}) {
  const [reason, setReason] = useState(finding.kind === 'amount_differs' ? 'split' : 'not_duplicate')
  const [note, setNote] = useState('')
  const { data } = useQuery({
    queryKey: ['ap-dup-invoice-reasons'],
    queryFn: () => financeApi.get<{ reasons: Reason[] }>('/ap-duplicate-invoices/reasons'),
    staleTime: Infinity,
  })
  const needsNote = reason === 'other' && !note.trim()

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-6">
      <div className="w-full max-w-lg rounded-lg bg-white shadow-xl">
        <div className="border-b border-neutral-200 px-5 py-4">
          <h3 className="text-sm font-semibold text-neutral-900">
            Mark invoice {finding.invoice_no ?? finding.invoice_no_norm} as reviewed
          </h3>
          <p className="mt-0.5 text-xs text-neutral-500">
            The group leaves the working list and stops counting toward the AP task. NC is
            not changed. If another bill with this invoice number is entered later, the group
            comes back.
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
                      placeholder="e.g. Supplier issued credit note CN-1042 on 2026-10-02"
                      className="w-full rounded-lg border border-neutral-300 px-2.5 py-1.5 text-sm" />
          </div>
          <div className="rounded border border-neutral-200 bg-neutral-50 px-2.5 py-1.5 font-mono text-[11px] text-neutral-500">
            {finding.bill_nos.join(', ')}
          </div>
          {error && <p className="text-xs text-red-700">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-neutral-200 px-5 py-3">
          <button onClick={onCancel}
                  className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50">
            Cancel
          </button>
          <button onClick={() => onDone(reason, note.trim())} disabled={needsNote || pending}
                  className="rounded-lg bg-[#085E5E] px-3 py-1.5 text-sm text-white hover:bg-[#064a4a] disabled:opacity-40">
            {pending ? 'Saving…' : 'Mark reviewed'}
          </button>
        </div>
      </div>
    </div>
  )
}

function BillsTable({ bills }: { bills: Bill[] }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-[11px] uppercase tracking-wide text-neutral-400">
          <th className="px-2 py-1.5 font-medium">NC bill</th>
          <th className="px-2 py-1.5 font-medium">Status</th>
          <th className="px-2 py-1.5 font-medium">Bill date</th>
          <th className="px-2 py-1.5 font-medium">Supplier</th>
          <th className="px-2 py-1.5 text-right font-medium">Invoice amount</th>
          <th className="px-2 py-1.5 text-right font-medium">Still open</th>
          <th className="px-2 py-1.5 text-right font-medium">Paid on bill</th>
          <th className="px-2 py-1.5 font-medium">NC payments</th>
        </tr>
      </thead>
      <tbody>
        {bills.map((b) => (
          <tr key={`${b.bill_no}-${b.supplier_code}`} className="border-t border-neutral-100">
            <td className="px-2 py-1.5 font-mono">{b.bill_no}</td>
            <td className="px-2 py-1.5">
              <span className={cn(!b.effective && 'font-medium text-red-700')}>{b.status_label}</span>
              {b.dismissed && <span className="ml-1 text-neutral-400">(ignored)</span>}
            </td>
            <td className="px-2 py-1.5 font-mono">{day(b.bill_date)}</td>
            <td className="px-2 py-1.5">
              <span className="font-mono text-neutral-400">{b.supplier_code}</span> {b.supplier_name}
            </td>
            <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums',
                              Number(b.amount) < 0 && 'text-emerald-700')}>{money(b.amount)}</td>
            <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums',
                              Number(b.open) !== 0 && 'font-semibold text-red-700')}>{money(b.open)}</td>
            <td className="px-2 py-1.5 text-right font-mono tabular-nums">{money(b.paid_on_bill)}</td>
            <td className="px-2 py-1.5 font-mono text-[11px] text-neutral-500">
              {b.payment_nos ?? '—'}{b.last_paid && <span className="text-neutral-400"> · {day(b.last_paid)}</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function ApDuplicateInvoicesPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [kind, setKind] = useState<Kind>('exact')
  const [reviewed, setReviewed] = useState<'no' | 'yes' | 'all'>('no')
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [reviewing, setReviewing] = useState<Finding | null>(null)
  const [reviewError, setReviewError] = useState<string | null>(null)

  const qs = new URLSearchParams({ kind, reviewed, limit: '2000' })
  if (search.trim()) qs.set('q', search.trim())
  const { data, isFetching, error } = useQuery({
    queryKey: ['ap-dup-invoices', kind, reviewed, search.trim()],
    queryFn: () => financeApi.get<Resp>(`/ap-duplicate-invoices?${qs}`),
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['ap-dup-invoices'] })
  const review = useMutation({
    mutationFn: (v: { key: string; bill_nos: string[]; reason: string; note: string }) =>
      financeApi.post('/ap-duplicate-invoices/review', v),
    onSuccess: () => { setReviewing(null); setReviewError(null); invalidate() },
    onError: (e: Error) => {
      // 409: a sync moved the group since it was loaded (a copy added or
      // voided). The verdict would cover bills nobody looked at, so reload.
      setReviewError(e.message === 'HTTP 409'
        ? 'This group changed since the page loaded (NC was synced). It has been refreshed — check the bills again.'
        : e.message)
      if (e.message === 'HTTP 409') { setReviewing(null); invalidate() }
    },
  })
  const unreview = useMutation({
    mutationFn: (key: string) => financeApi.post('/ap-duplicate-invoices/unreview', { key }),
    onSuccess: invalidate,
  })

  const { data: reasonData } = useQuery({
    queryKey: ['ap-dup-invoice-reasons'],
    queryFn: () => financeApi.get<{ reasons: Reason[] }>('/ap-duplicate-invoices/reasons'),
    staleTime: Infinity,
  })
  const reasonLabel = useMemo(
    () => Object.fromEntries((reasonData?.reasons ?? []).map((r) => [r.code, r.label])),
    [reasonData])

  const summary = data?.summary
  const rows = data?.findings ?? []

  const toggle = (key: string) => setExpanded((prev) => {
    const next = new Set(prev)
    if (next.has(key)) next.delete(key); else next.add(key)
    return next
  })

  const exportCsv = () => {
    const head = ['Action', 'Kind', 'Invoice no', 'Currency', 'Supplier code', 'Supplier',
                  'Invoice amount', 'Extra copies', 'Billed twice', 'Still payable on copies',
                  'NC bill', 'Bill status', 'Bill date', 'Bill supplier', 'Amount on bill',
                  'Open on bill', 'Paid on bill', 'NC payments', 'Reviewed', 'Reason', 'Note']
    const body = rows.flatMap((f) => f.bills.map((b) => [
      STATUS[f.status].label, f.kind_label, f.invoice_no, f.currency, f.supplier_code,
      f.supplier_name, f.amount, f.extra_copies, f.extra_amount, f.open_exposure,
      b.bill_no, b.status_label, b.bill_date, b.supplier_name, b.amount, b.open,
      b.paid_on_bill, b.payment_nos, f.reviewed ? 'yes' : 'no', f.review?.reason, f.review?.note]))
    const csv = [head, ...body].map((row) => row.map(csvEscape).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `ap-duplicate-invoices-${kind}-${reviewed}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!user) return <Navigate to="/login" replace />

  const showMoney = kind !== 'pending' && kind !== 'amount_differs'

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/ap-duplicate-invoices"
      title="AP Duplicate Invoices"
      subtitle="Invoice numbers NC has on more than one payable"
    >
      <div className="mx-auto max-w-7xl">
        <div className="mb-5 rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm leading-relaxed text-neutral-700">
          <p className="mb-1.5">
            NC does not check that an invoice number is unique, so the same invoice can be entered —
            and paid — more than once. This page is that check. It reads NC&rsquo;s payables after
            every AP sync (hourly), and AP gets a task when something new turns up.
          </p>
          <p className="text-neutral-500">
            A bill that was reversed by an equal credit is not counted as a copy. The earlier a
            duplicate is caught the cheaper it is: <strong>stop</strong> a draft before approval,
            <strong> hold</strong> an approved copy back from payment, and only a copy already paid
            has to be <strong>recovered</strong> from the supplier. Nothing here changes NC — the fix
            (void, credit note) is made in NC and this page follows on the next sync.
          </p>
        </div>

        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {KIND_ORDER.map((k) => {
            const s = summary?.[k]
            const byCcy = Object.entries(s?.by_currency ?? {})
            return (
              <button key={k} onClick={() => { setKind(k); setExpanded(new Set()) }}
                      className={cn('rounded-lg border p-3 text-left transition-colors',
                                    kind === k ? 'border-[#085E5E] bg-primary-50/60'
                                               : 'border-neutral-200 hover:bg-neutral-50')}>
                <div className="flex items-center gap-1.5">
                  {s && s.actionable && s.unreviewed > 0
                    ? <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                    : <CheckCircle2 className="h-3.5 w-3.5 text-[#085E5E]" />}
                  <span className="text-sm font-semibold text-neutral-800">{KIND_TAB[k]}</span>
                </div>
                <p className="mt-1.5 text-xs text-neutral-500">
                  <span className="font-mono text-base font-semibold text-neutral-900">{s?.unreviewed ?? '—'}</span>
                  {' '}to review{s && s.reviewed > 0 && <> · {s.reviewed} reviewed</>}
                </p>
                {(k === 'exact' || k === 'cross_supplier') && byCcy.map(([ccy, t]) => (
                  <p key={ccy} className="mt-0.5 text-[11px] text-neutral-500">
                    <span className="font-mono">{ccy}</span> billed twice {money(t.extra_amount)}
                    {Number(t.open_exposure) > 0 && (
                      <span className="font-medium text-red-700"> · {money(t.open_exposure)} still payable</span>
                    )}
                  </p>
                ))}
                {!s?.actionable && s && (
                  <p className="mt-0.5 text-[11px] text-neutral-400">Not counted in the AP task</p>
                )}
              </button>
            )
          })}
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-lg border border-neutral-300 p-0.5 text-xs">
            {(['no', 'yes', 'all'] as const).map((r) => (
              <button key={r} onClick={() => setReviewed(r)}
                      className={cn('rounded-md px-2.5 py-1',
                                    reviewed === r ? 'bg-[#085E5E] text-white' : 'text-neutral-600 hover:bg-neutral-50')}>
                {r === 'no' ? 'To review' : r === 'yes' ? 'Reviewed' : 'All'}
              </button>
            ))}
          </div>
          <input value={search} onChange={(e) => setSearch(e.target.value)}
                 placeholder="Invoice no, supplier, or NC bill no"
                 className="w-72 rounded-lg border border-neutral-300 px-2.5 py-1.5 text-sm" />
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-neutral-400" />}
          <span className="ml-auto text-xs text-neutral-500">{data ? `${data.total} groups` : ''}</span>
          <button onClick={exportCsv} disabled={!rows.length}
                  className="inline-flex items-center gap-1 rounded-lg border border-neutral-300 px-2.5 py-1.5 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-40">
            <Download className="h-3.5 w-3.5" /> CSV
          </button>
        </div>

        {error ? (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {(error as Error).message}
          </div>
        ) : !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : rows.length === 0 ? (
          <div className="rounded-lg border border-neutral-200 px-4 py-10 text-center text-sm text-neutral-500">
            {reviewed === 'no' ? 'Nothing left to review here.' : 'No groups.'}
          </div>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50">
                <tr className="text-left text-xs text-neutral-500">
                  <th className="w-6 px-2 py-2" />
                  <th className="px-2.5 py-2 font-medium">Action</th>
                  <th className="px-2.5 py-2 font-medium">Invoice no</th>
                  <th className="px-2.5 py-2 font-medium">Supplier</th>
                  <th className="px-2.5 py-2 font-medium">Ccy</th>
                  <th className="px-2.5 py-2 text-right font-medium">Invoice amount</th>
                  <th className="px-2.5 py-2 text-right font-medium">Bills</th>
                  {showMoney && <th className="px-2.5 py-2 text-right font-medium">Billed twice</th>}
                  {showMoney && <th className="px-2.5 py-2 text-right font-medium">Still payable</th>}
                  <th className="px-2.5 py-2 font-medium">Review</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((f) => {
                  const open = expanded.has(f.key)
                  const st = STATUS[f.status]
                  const suppliers = f.supplier_name
                    ?? Array.from(new Set(f.bills.map((b) => b.supplier_name))).join(' / ')
                  return (
                    <Fragment key={f.key}>
                      <tr className="cursor-pointer border-t border-neutral-100 hover:bg-neutral-50"
                          onClick={() => toggle(f.key)}>
                        <td className="px-2 py-2 text-neutral-400">
                          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </td>
                        <td className="px-2.5 py-2">
                          <span title={st.hint}
                                className={cn('inline-block rounded border px-1.5 py-0.5 text-[11px] font-medium', st.cls)}>
                            {st.label}
                          </span>
                          {f.reopened && (
                            <span className="ml-1 text-[11px] text-amber-700" title="Reviewed before, but a bill has been added since">
                              new copy
                            </span>
                          )}
                        </td>
                        <td className="px-2.5 py-2 font-mono">{f.invoice_no ?? f.invoice_no_norm}</td>
                        <td className="px-2.5 py-2">
                          {f.supplier_code && <span className="font-mono text-xs text-neutral-400">{f.supplier_code} </span>}
                          {suppliers}
                        </td>
                        <td className="px-2.5 py-2 font-mono text-xs">{f.currency}</td>
                        <td className="px-2.5 py-2 text-right font-mono tabular-nums">{money(f.amount)}</td>
                        <td className="px-2.5 py-2 text-right font-mono tabular-nums">{f.bill_nos.length}</td>
                        {showMoney && (
                          <td className="px-2.5 py-2 text-right font-mono tabular-nums">{money(f.extra_amount)}</td>
                        )}
                        {showMoney && (
                          <td className={cn('px-2.5 py-2 text-right font-mono tabular-nums',
                                            Number(f.open_exposure) > 0 && 'font-semibold text-red-700')}>
                            {money(f.open_exposure)}
                          </td>
                        )}
                        <td className="px-2.5 py-2" onClick={(e) => e.stopPropagation()}>
                          {f.review ? (
                            <div className="flex items-center gap-2 text-xs">
                              <span className="text-neutral-600" title={f.review.note ?? ''}>
                                {reasonLabel[f.review.reason] ?? f.review.reason} · {f.review.reviewed_by_name ?? '—'} · {day(f.review.reviewed_at)}
                              </span>
                              <button onClick={() => unreview.mutate(f.key)} disabled={unreview.isPending}
                                      title="Put back on the list"
                                      className="text-neutral-400 hover:text-neutral-700 disabled:opacity-40">
                                <Undo2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          ) : (
                            <button onClick={() => { setReviewError(null); setReviewing(f) }}
                                    className="rounded-lg border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50">
                              Mark reviewed
                            </button>
                          )}
                        </td>
                      </tr>
                      {open && (
                        <tr className="bg-neutral-50/60">
                          <td />
                          <td colSpan={showMoney ? 9 : 7} className="px-2.5 py-2">
                            <BillsTable bills={f.bills} />
                            {f.review?.note && (
                              <p className="mt-2 text-xs text-neutral-500">Note: {f.review.note}</p>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {reviewing && (
        <ReviewDialog
          finding={reviewing}
          pending={review.isPending}
          error={reviewError}
          onCancel={() => { setReviewing(null); setReviewError(null) }}
          onDone={(reason, note) => review.mutate({
            key: reviewing.key, bill_nos: reviewing.bill_nos, reason, note })}
        />
      )}
      {reviewError && !reviewing && (
        <div className="fixed bottom-4 right-4 z-[60] max-w-sm rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 shadow">
          {reviewError}
          <button onClick={() => setReviewError(null)} className="ml-2 text-xs underline">Dismiss</button>
        </div>
      )}
    </PortalChromeLayout>
  )
}
