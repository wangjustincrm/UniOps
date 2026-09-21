/**
 * AP Reconciliation — where each of our payables stands on NC's books.
 *
 * NC is the book of record for payable balances, so "still owed" here is NC's
 * open amount, not our paid_amount: payments are made in NC and never flow back
 * to us, which is why invoices we still show as unpaid can be long settled
 * there. The categories separate the three things that look identical in our
 * own AP list and are completely different problems:
 *
 *   missing in NC        — a real liability NC's books do not carry yet
 *   invoice no mismatch  — NC has the money under a different number (a typo)
 *   settled in NC        — we are overstating what we owe
 *
 * Read-only. Every fix is in NC or in our invoice record; nothing on this page
 * writes to either.
 *
 * The freshness strip at the top is part of the answer, not decoration. The
 * mirror syncs on a schedule, so these numbers are as of the last sync — and if
 * that sync did not tie out against NC, the page says so instead of showing
 * confident figures over an incomplete mirror.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CalendarX2, CheckCircle2, Download, Loader2 } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const linkBtn = 'text-xs font-medium text-[#085E5E] hover:underline'
const PAGE = 200

interface MirrorState {
  synced_at: string | null
  tie_out_ok: boolean | null
  tie_out?: { nc?: Record<string, string>; local?: Record<string, string>; diffs?: Record<string, unknown> }
  stale: boolean
}
interface CategoryRow {
  category: string
  invoices: number
  our_total: string
  our_outstanding: string
  nc_open: string
}
interface SummaryResp { categories: CategoryRow[]; mirror: MirrorState }

interface NcSide { bill_no: string | null; money_cr: string | null; money_bal: string | null; lines: number | null; supplier_agrees: boolean | null }
interface AltSide { bill_no: string | null; invoice_no: string | null; money_cr: string | null; money_bal: string | null; bill_date: string | null; day_gap: number | null }
interface ItemRow {
  id: string
  ap_invoice_number: string | null
  vendor_invoice_number: string | null
  vendor_name: string | null
  vendor_erp_id: string | null
  total_amount: string | null
  paid_amount: string | null
  invoice_date: string | null
  due_date: string | null
  status: string | null
  po_number: string | null
  source: string | null
  category: string
  nc: NcSide | null
  nc_amount_match: AltSide | null
}
interface ItemsResp { total: number; items: ItemRow[] }

interface AnomalyRow {
  id: string
  ap_invoice_number: string | null
  vendor_name: string | null
  vendor_invoice_number: string | null
  total_amount: string | null
  invoice_date: string | null
  due_date: string | null
  status: string | null
  reason: string
}
interface AnomaliesResp { total: number; items: AnomalyRow[] }

/** Display order is worst-first: the categories that cost money come before the
 *  ones that are merely untidy, and drafts come last because they are not a
 *  finding at all. */
const CATEGORIES: { key: string; label: string; help: string; tone: 'bad' | 'warn' | 'plain' }[] = [
  {
    key: 'missing_in_nc',
    label: 'Missing in NC',
    tone: 'bad',
    help: 'We hold the invoice and NC has no bill for it under any number we can find. These are real liabilities NC’s books do not carry yet — chase finance to key them.',
  },
  {
    key: 'invoice_no_mismatch',
    label: 'Invoice number mismatch',
    tone: 'warn',
    help: 'NC has a bill for the same supplier and exactly the same amount, under a different invoice number. Almost always a keying error in NC. Check the date gap before acting — a recurring charge bills the same figure every period.',
  },
  {
    key: 'in_nc_open',
    label: 'Open in NC',
    tone: 'plain',
    help: 'On NC’s books and still unpaid there. This is what we actually owe, and it is what the cash-flow forecast is built from.',
  },
  {
    key: 'in_nc_settled',
    label: 'Settled in NC',
    tone: 'warn',
    help: 'NC has paid these. Anything we still show as outstanding against them is an overstatement on our side — payments happen in NC and never flow back to us.',
  },
  {
    key: 'not_submitted',
    label: 'Draft (not submitted)',
    tone: 'plain',
    help: 'Our own drafts. Absent from NC because they have not been submitted — listed for completeness, not as a finding.',
  },
]

function money(v: string | null | undefined) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return n === 0 ? '0.00' : '—'
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function csvEscape(v: unknown) {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}
/** Date-only strings must not go through new Date() — in a UTC-behind zone that
 *  renders yesterday. These are already YYYY-MM-DD from the API. */
function day(v: string | null) { return v ? v.slice(0, 10) : '—' }

export default function ApReconciliationPage() {
  const { user } = useAuthStore()
  const [category, setCategory] = useState('missing_in_nc')
  const [offset, setOffset] = useState(0)
  const [includePaid, setIncludePaid] = useState(true)
  const [showAnomalies, setShowAnomalies] = useState(false)

  const qs = `include_paid=${includePaid}`

  const { data: summary, isFetching: loadingSummary } = useQuery({
    queryKey: ['ap-recon-summary', includePaid],
    queryFn: () => financeApi.get<SummaryResp>(`/ap-recon/summary?${qs}`),
  })
  const { data: items, isFetching: loadingItems } = useQuery({
    queryKey: ['ap-recon-items', category, offset, includePaid],
    queryFn: () => financeApi.get<ItemsResp>(
      `/ap-recon/items?category=${category}&limit=${PAGE}&offset=${offset}&${qs}`),
  })
  const { data: anomalies } = useQuery({
    queryKey: ['ap-recon-anomalies', includePaid],
    queryFn: () => financeApi.get<AnomaliesResp>(`/ap-recon/date-anomalies?${qs}`),
  })

  const byKey = useMemo(() => {
    const m: Record<string, CategoryRow> = {}
    for (const c of summary?.categories ?? []) m[c.category] = c
    return m
  }, [summary])

  const mirror = summary?.mirror
  const active = CATEGORIES.find((c) => c.key === category)

  const pick = (key: string) => { setCategory(key); setOffset(0) }

  const exportCsv = async () => {
    const all: ItemRow[] = []
    for (let off = 0; ; off += 1000) {
      const page = await financeApi.get<ItemsResp>(
        `/ap-recon/items?category=${category}&limit=1000&offset=${off}&${qs}`)
      all.push(...page.items)
      if (all.length >= page.total || page.items.length === 0) break
    }
    const head = ['AP No', 'Vendor', 'Vendor code', 'Our invoice no', 'Amount',
                  'Invoice date', 'Due date', 'Status', 'PO', 'Category',
                  'NC bill', 'NC billed', 'NC open',
                  'NC invoice no (amount match)', 'Day gap']
    const body = all.map((r) => [
      r.ap_invoice_number, r.vendor_name, r.vendor_erp_id, r.vendor_invoice_number,
      r.total_amount, r.invoice_date, r.due_date, r.status, r.po_number, r.category,
      r.nc?.bill_no ?? '', r.nc?.money_cr ?? '', r.nc?.money_bal ?? '',
      r.nc_amount_match?.invoice_no ?? '', r.nc_amount_match?.day_gap ?? '',
    ])
    const csv = [head, ...body].map((row) => row.map(csvEscape).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `ap-reconciliation-${category}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/ap-recon"
      title="AP Reconciliation"
      subtitle="Our payables against NC’s books — balances are NC’s, not ours (CAD)"
    >
      <div className="mx-auto max-w-7xl">
        {/* Freshness first. A daily sync means a stale page is normal, and a
            mirror that does not tie out must not present confident totals. */}
        {mirror && (
          <div className={cn(
            'mb-4 flex flex-wrap items-center gap-2 rounded-lg border px-4 py-2.5 text-sm',
            mirror.tie_out_ok === false
              ? 'border-danger-200 bg-danger-50 text-danger-900'
              : 'border-neutral-200 bg-neutral-50 text-neutral-600')}>
            {mirror.tie_out_ok === false
              ? <AlertTriangle className="h-4 w-4 shrink-0" />
              : <CheckCircle2 className="h-4 w-4 shrink-0 text-[#085E5E]" />}
            {mirror.synced_at ? (
              <span>
                NC mirror as of <strong>{mirror.synced_at.replace('T', ' ').slice(0, 16)}</strong>
                {mirror.tie_out_ok === false
                  ? ' — it does NOT tie out against NC, so these figures may be incomplete.'
                  : ' — tied out against NC.'}
              </span>
            ) : (
              <span>The NC payables mirror has never completed a sync — nothing below can be trusted yet.</span>
            )}
          </div>
        )}

        <div className="mb-4 flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-sm text-neutral-600">
            <input type="checkbox" checked={includePaid}
                   onChange={(e) => { setIncludePaid(e.target.checked); setOffset(0) }} />
            Include invoices we have marked paid
          </label>
          <button onClick={exportCsv}
                  className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50">
            <Download className="h-4 w-4" /> Export CSV
          </button>
        </div>

        {loadingSummary && !summary ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="mb-5 overflow-x-auto rounded-lg border border-neutral-200">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                  <th className="px-3 py-2 text-left font-medium">Category</th>
                  <th className="px-3 py-2 text-right font-medium">Invoices</th>
                  <th className="px-3 py-2 text-right font-medium">Our total</th>
                  <th className="px-3 py-2 text-right font-medium">Open in NC</th>
                </tr>
              </thead>
              <tbody>
                {CATEGORIES.map((c) => {
                  const r = byKey[c.key]
                  const n = r?.invoices ?? 0
                  return (
                    <tr key={c.key}
                        className={cn('border-t border-neutral-100',
                                      category === c.key && 'bg-primary-50/60',
                                      n === 0 && 'text-neutral-400')}>
                      <td className="px-3 py-2">
                        <button className={cn(linkBtn, n === 0 && 'text-neutral-400 hover:no-underline')}
                                disabled={n === 0} onClick={() => pick(c.key)}>
                          {c.label}
                        </button>
                        {c.tone === 'bad' && n > 0 && (
                          <span className="ml-2 rounded bg-danger-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-danger-700">action</span>
                        )}
                        {c.tone === 'warn' && n > 0 && (
                          <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-800">check</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right font-mono tabular-nums">{n.toLocaleString()}</td>
                      <td className="px-3 py-2 text-right font-mono tabular-nums">{money(r?.our_total)}</td>
                      <td className="px-3 py-2 text-right font-mono tabular-nums">{money(r?.nc_open)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {active && (
          <p className="mb-3 max-w-3xl text-xs leading-relaxed text-neutral-500">{active.help}</p>
        )}

        <div className="overflow-x-auto rounded-lg border border-neutral-200">
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                <th className="px-3 py-2 text-left font-medium">AP No.</th>
                <th className="px-3 py-2 text-left font-medium">Vendor</th>
                <th className="px-3 py-2 text-left font-medium">Our invoice no.</th>
                <th className="px-3 py-2 text-right font-medium">Amount</th>
                <th className="px-3 py-2 text-left font-medium">Invoice</th>
                <th className="px-3 py-2 text-left font-medium">Due</th>
                <th className="px-3 py-2 text-left font-medium">PO</th>
                {category === 'invoice_no_mismatch' ? (
                  <>
                    <th className="px-3 py-2 text-left font-medium">NC invoice no.</th>
                    <th className="px-3 py-2 text-left font-medium">NC bill</th>
                    <th className="px-3 py-2 text-right font-medium">Gap</th>
                  </>
                ) : (
                  <>
                    <th className="px-3 py-2 text-left font-medium">NC bill</th>
                    <th className="px-3 py-2 text-right font-medium">NC open</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {loadingItems && !items ? (
                <tr><td colSpan={10} className="px-3 py-8 text-center">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" />
                </td></tr>
              ) : (items?.items.length ?? 0) === 0 ? (
                <tr><td colSpan={10} className="px-3 py-8 text-center text-neutral-400">
                  Nothing in this category.
                </td></tr>
              ) : items!.items.map((r) => (
                <tr key={r.id} className="border-t border-neutral-100 hover:bg-neutral-50/60">
                  <td className="px-3 py-2 font-mono text-xs">{r.ap_invoice_number ?? '—'}</td>
                  <td className="px-3 py-2">{r.vendor_name ?? '—'}</td>
                  <td className="px-3 py-2 font-mono text-xs">{r.vendor_invoice_number ?? '—'}</td>
                  <td className="px-3 py-2 text-right font-mono tabular-nums">{money(r.total_amount)}</td>
                  <td className="px-3 py-2 text-xs text-neutral-600">{day(r.invoice_date)}</td>
                  <td className="px-3 py-2 text-xs text-neutral-600">{day(r.due_date)}</td>
                  <td className="px-3 py-2 font-mono text-xs text-neutral-500">{r.po_number ?? '—'}</td>
                  {category === 'invoice_no_mismatch' ? (
                    <>
                      <td className="px-3 py-2 font-mono text-xs text-amber-800">
                        {r.nc_amount_match?.invoice_no ?? '—'}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-500">
                        {r.nc_amount_match?.bill_no ?? '—'}
                      </td>
                      {/* A small gap means the same invoice keyed wrong; a large
                          one may just be next period's identical charge. */}
                      <td className={cn('px-3 py-2 text-right font-mono tabular-nums text-xs',
                                        (r.nc_amount_match?.day_gap ?? 0) > 60
                                          ? 'text-neutral-400' : 'text-neutral-700')}>
                        {r.nc_amount_match?.day_gap ?? '—'}d
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-500">{r.nc?.bill_no ?? '—'}</td>
                      <td className="px-3 py-2 text-right font-mono tabular-nums">{money(r.nc?.money_bal)}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-3 flex items-center gap-3 text-sm text-neutral-500">
          <span>{(items?.total ?? 0).toLocaleString()} invoices</span>
          <div className="ml-auto flex gap-2">
            <button disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - PAGE))}
                    className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-40">Prev</button>
            <button disabled={offset + PAGE >= (items?.total ?? 0)}
                    onClick={() => setOffset(offset + PAGE)}
                    className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-40">Next</button>
          </div>
        </div>

        {/* A different question from the NC comparison: not "is it on NC's
            books" but "can this date be right at all". Kept apart so it never
            competes with the reconciliation counts. */}
        <div className="mt-8">
          <button onClick={() => setShowAnomalies((v) => !v)}
                  className="inline-flex items-center gap-2 text-sm font-medium text-neutral-700 hover:text-[#085E5E]">
            <CalendarX2 className="h-4 w-4" />
            Impossible invoice dates
            <span className={cn('rounded px-1.5 py-0.5 text-[11px] font-semibold',
                                (anomalies?.total ?? 0) > 0
                                  ? 'bg-amber-100 text-amber-800' : 'bg-neutral-100 text-neutral-500')}>
              {anomalies?.total ?? 0}
            </span>
          </button>
          {showAnomalies && (
            <>
              <p className="mt-2 max-w-3xl text-xs leading-relaxed text-neutral-500">
                A due date before the invoice date, or an invoice dated in the future — almost
                always a day/month swap out of OCR. These feed the cash-flow buckets directly,
                so a September invoice read as December moves real money into a month it will
                never be paid in.
              </p>
              <div className="mt-3 overflow-x-auto rounded-lg border border-neutral-200">
                <table className="w-full min-w-[720px] text-sm">
                  <thead>
                    <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                      <th className="px-3 py-2 text-left font-medium">AP No.</th>
                      <th className="px-3 py-2 text-left font-medium">Vendor</th>
                      <th className="px-3 py-2 text-left font-medium">Invoice no.</th>
                      <th className="px-3 py-2 text-right font-medium">Amount</th>
                      <th className="px-3 py-2 text-left font-medium">Invoice date</th>
                      <th className="px-3 py-2 text-left font-medium">Due date</th>
                      <th className="px-3 py-2 text-left font-medium">Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(anomalies?.items.length ?? 0) === 0 ? (
                      <tr><td colSpan={7} className="px-3 py-6 text-center text-neutral-400">
                        No impossible dates.
                      </td></tr>
                    ) : anomalies!.items.map((r) => (
                      <tr key={r.id} className="border-t border-neutral-100">
                        <td className="px-3 py-2 font-mono text-xs">{r.ap_invoice_number ?? '—'}</td>
                        <td className="px-3 py-2">{r.vendor_name ?? '—'}</td>
                        <td className="px-3 py-2 font-mono text-xs">{r.vendor_invoice_number ?? '—'}</td>
                        <td className="px-3 py-2 text-right font-mono tabular-nums">{money(r.total_amount)}</td>
                        <td className="px-3 py-2 text-xs text-amber-800">{day(r.invoice_date)}</td>
                        <td className="px-3 py-2 text-xs text-neutral-600">{day(r.due_date)}</td>
                        <td className="px-3 py-2 text-xs text-neutral-500">
                          {r.reason === 'due_before_invoice'
                            ? 'Due date is before the invoice date'
                            : 'Invoice is dated in the future'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </PortalChromeLayout>
  )
}
