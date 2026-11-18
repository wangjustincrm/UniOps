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
import { AlertTriangle, CalendarX2, CheckCircle2, Download, Loader2, Scale } from 'lucide-react'
import { Badge, Button, Checkbox, SortTh, TBody, THead, Table, Td, Th, Tr, sortParams, useSort, useSortableRows } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn, formatDate, formatDateTime } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

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

interface NcSide {
  bill_no: string | null; money_cr: string | null; money_bal: string | null
  lines: number | null; supplier_agrees: boolean | null
  /** NC billed minus what we recorded. Positive = NC is higher. */
  amount_gap: string | null
  amount_differs: boolean
  /** NC's line is negative under our number — a credit note, not a smaller bill. */
  is_credit: boolean
}
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
  unapproved_bill_no: string | null
  nc: NcSide | null
  nc_amount_match: AltSide | null
}
interface ItemsResp { total: number; items: ItemRow[] }
/** GET /ap-recon/items sort keys (finance-api ap_reconciliation.ITEM_SORT). */
type ItemSortKey = 'ap_invoice_number' | 'vendor_name' | 'vendor_invoice_number' | 'total_amount'
  | 'invoice_date' | 'due_date' | 'po_number' | 'nc_bill_no' | 'nc_money_cr' | 'nc_money_bal'
  | 'alt_invoice_no' | 'alt_bill_no' | 'alt_day_gap'

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

interface GapRow {
  id: string
  ap_invoice_number: string | null
  vendor_invoice_number: string | null
  vendor_name: string | null
  total_amount: string | null
  invoice_date: string | null
  status: string | null
  po_number: string | null
  category: string
  nc_bill_no: string | null
  nc_money_cr: string
  nc_money_bal: string
  nc_lines: number | null
  amount_gap: string
  is_credit: boolean
}
interface GapsResp {
  total: number
  drafts_excluded: number
  nc_billed_more: { invoices: number; amount: string }
  nc_billed_less: { invoices: number; amount: string }
  items: GapRow[]
}

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
    key: 'in_nc_unapproved',
    label: 'In NC but never approved',
    tone: 'bad',
    help: 'NC has a document for this invoice, but it was never approved — so it is not on the books and nothing will ever pay it. Usually a draft someone abandoned, sometimes a duplicate of a bill that was approved under a different number.',
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
function day(v: string | null) { return v ? formatDate(v.slice(0, 10)) : '—' }
function anomalyReason(reason: string) {
  return reason === 'due_before_invoice'
    ? 'Due date is before the invoice date'
    : 'Invoice is dated in the future'
}

export default function ApReconciliationPage() {
  const { user } = useAuthStore()
  const [category, setCategory] = useState('missing_in_nc')
  const [offset, setOffset] = useState(0)
  const [includePaid, setIncludePaid] = useState(true)
  const [showAnomalies, setShowAnomalies] = useState(false)
  const [showGaps, setShowGaps] = useState(false)

  const qs = `include_paid=${includePaid}`
  // The drill-down is paged by the API, so the API sorts it (default: by money).
  const [itemSort, onItemSort, setItemSort] = useSort<ItemSortKey>(null, () => setOffset(0))
  const { sort: sortKey, dir: sortDir } = sortParams(itemSort)
  const sortSuffix = sortKey ? `&sort=${sortKey}&dir=${sortDir}` : ''

  const { data: summary, isFetching: loadingSummary } = useQuery({
    queryKey: ['ap-recon-summary', includePaid],
    queryFn: () => financeApi.get<SummaryResp>(`/ap-recon/summary?${qs}`),
  })
  const { data: items, isFetching: loadingItems, error: itemsError } = useQuery({
    queryKey: ['ap-recon-items', category, offset, includePaid, itemSort],
    queryFn: () => financeApi.get<ItemsResp>(
      `/ap-recon/items?category=${category}&limit=${PAGE}&offset=${offset}&${qs}${sortSuffix}`),
  })
  const { data: anomalies } = useQuery({
    queryKey: ['ap-recon-anomalies', includePaid],
    queryFn: () => financeApi.get<AnomaliesResp>(`/ap-recon/date-anomalies?${qs}`),
  })
  // Cuts across in_nc_open and in_nc_settled rather than being a category of
  // its own — making it one would quietly move invoices out of the open list,
  // which is the figure the cash-flow forecast is built from.
  const { data: gaps } = useQuery({
    queryKey: ['ap-recon-gaps', includePaid],
    queryFn: () => financeApi.get<GapsResp>(`/ap-recon/amount-mismatches?${qs}`),
  })

  // Both side panels are loaded whole (no paging), so they sort on the page.
  const gapSort = useSortableRows(gaps?.items, {
    ap_invoice_number: (r) => r.ap_invoice_number, vendor: (r) => r.vendor_name,
    invoice: (r) => r.vendor_invoice_number, po: (r) => r.po_number,
    ours: (r) => Number(r.total_amount ?? NaN), nc_billed: (r) => Number(r.nc_money_cr),
    difference: (r) => Number(r.amount_gap), nc_bill: (r) => r.nc_bill_no,
    // The cell reads "credit note" / "open · <bal>" / "settled"
    state: (r) => (r.is_credit ? 'credit note' : r.category === 'in_nc_open' ? 'open' : 'settled'),
  })
  const anomalySort = useSortableRows(anomalies?.items, {
    ap_invoice_number: (r) => r.ap_invoice_number, vendor: (r) => r.vendor_name,
    invoice: (r) => r.vendor_invoice_number, amount: (r) => Number(r.total_amount ?? NaN),
    invoice_date: (r) => r.invoice_date, due_date: (r) => r.due_date,
    why: (r) => anomalyReason(r.reason),
  })

  const byKey = useMemo(() => {
    const m: Record<string, CategoryRow> = {}
    for (const c of summary?.categories ?? []) m[c.category] = c
    return m
  }, [summary])

  const mirror = summary?.mirror
  const active = CATEGORIES.find((c) => c.key === category)

  // The NC columns differ per category, so a sort on one of them must not
  // carry over to a category that does not show it.
  const pick = (key: string) => { setCategory(key); setOffset(0); setItemSort(null) }

  const exportCsv = async () => {
    const all: ItemRow[] = []
    for (let off = 0; ; off += 1000) {
      const page = await financeApi.get<ItemsResp>(
        `/ap-recon/items?category=${category}&limit=1000&offset=${off}&${qs}${sortSuffix}`)
      all.push(...page.items)
      if (all.length >= page.total || page.items.length === 0) break
    }
    const head = ['AP No', 'Vendor', 'Vendor code', 'Our invoice no', 'Amount',
                  'Invoice date', 'Due date', 'Status', 'PO', 'Category',
                  'NC bill', 'NC billed', 'NC open', 'NC billed minus ours',
                  'NC invoice no (amount match)', 'Day gap']
    const body = all.map((r) => [
      r.ap_invoice_number, r.vendor_name, r.vendor_erp_id, r.vendor_invoice_number,
      r.total_amount, r.invoice_date, r.due_date, r.status, r.po_number, r.category,
      r.nc?.bill_no ?? '', r.nc?.money_cr ?? '', r.nc?.money_bal ?? '',
      r.nc?.amount_gap ?? '',
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
              ? 'border-danger-line bg-danger-soft text-danger-fg'
              : 'border-line bg-surface-subtle text-fg-secondary')}>
            {mirror.tie_out_ok === false
              ? <AlertTriangle className="h-4 w-4 shrink-0" />
              : <CheckCircle2 className="h-4 w-4 shrink-0 text-primary-700" />}
            {mirror.synced_at ? (
              <span>
                NC mirror as of <strong>{formatDateTime(mirror.synced_at)}</strong>
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
          <label className="flex items-center gap-1.5 text-sm text-fg-secondary">
            <Checkbox checked={includePaid}
                   onChange={(e) => { setIncludePaid(e.target.checked); setOffset(0) }} />
            Include invoices we have marked paid
          </label>
          <Button variant="secondary" size="sm" onClick={exportCsv}
                  className="ml-auto">
            <Download className="h-4 w-4" /> Export CSV
          </Button>
        </div>

        {loadingSummary && !summary ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
        ) : (
          <div className="mb-5 overflow-x-auto rounded-lg border border-line">
            <Table density="compact" className="min-w-[640px]">
              <THead>
                <Tr className="bg-surface-subtle text-xs text-fg-muted">
                  <Th>Category</Th>
                  <Th align="right">Invoices</Th>
                  <Th align="right">Our total</Th>
                  <Th align="right">Open in NC</Th>
                </Tr>
              </THead>
              <TBody>
                {CATEGORIES.map((c) => {
                  const r = byKey[c.key]
                  const n = r?.invoices ?? 0
                  return (
                    <Tr key={c.key}
                        className={cn('border-t border-line-subtle',
                                      category === c.key && 'bg-primary-50/60',
                                      n === 0 && 'text-fg-muted')}>
                      <Td>
                        <Button variant="link" size="xs" className={cn(n === 0 && 'text-fg-muted hover:no-underline')}
                                disabled={n === 0} onClick={() => pick(c.key)}>
                          {c.label}
                        </Button>
                        {c.tone === 'bad' && n > 0 && (
                          <Badge variant="danger" className="ml-2">Action</Badge>
                        )}
                        {c.tone === 'warn' && n > 0 && (
                          <Badge variant="warning" className="ml-2">Check</Badge>
                        )}
                      </Td>
                      <Td align="right" className="font-mono tabular-nums">{n.toLocaleString()}</Td>
                      <Td align="right" className="font-mono tabular-nums">{money(r?.our_total)}</Td>
                      <Td align="right" className="font-mono tabular-nums">{money(r?.nc_open)}</Td>
                    </Tr>
                  )
                })}
              </TBody>
            </Table>
          </div>
        )}

        {active && (
          <p className="mb-3 max-w-3xl text-xs leading-relaxed text-fg-muted">{active.help}</p>
        )}

        <div className="overflow-x-auto rounded-lg border border-line">
          <Table density="compact" className="min-w-[900px]">
            <THead>
              <Tr className="bg-surface-subtle text-xs text-fg-muted">
                <SortTh label="AP No." sortKey="ap_invoice_number" sort={itemSort} onSort={onItemSort} className="px-3 py-2 font-medium text-left" />
                <SortTh label="Vendor" sortKey="vendor_name" sort={itemSort} onSort={onItemSort} className="px-3 py-2 font-medium text-left" />
                <SortTh label="Our invoice no." sortKey="vendor_invoice_number" sort={itemSort} onSort={onItemSort} className="px-3 py-2 font-medium text-left" />
                <SortTh label="Amount" sortKey="total_amount" sort={itemSort} onSort={onItemSort} align="right" className="px-3 py-2 font-medium" />
                <SortTh label="Invoice" sortKey="invoice_date" sort={itemSort} onSort={onItemSort} className="px-3 py-2 font-medium text-left" />
                <SortTh label="Due" sortKey="due_date" sort={itemSort} onSort={onItemSort} className="px-3 py-2 font-medium text-left" />
                <SortTh label="PO" sortKey="po_number" sort={itemSort} onSort={onItemSort} className="px-3 py-2 font-medium text-left" />
                {category === 'invoice_no_mismatch' ? (
                  <>
                    <SortTh label="NC invoice no." sortKey="alt_invoice_no" sort={itemSort} onSort={onItemSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="NC bill" sortKey="alt_bill_no" sort={itemSort} onSort={onItemSort} className="px-3 py-2 font-medium text-left" />
                    <SortTh label="Gap" sortKey="alt_day_gap" sort={itemSort} onSort={onItemSort} align="right" className="px-3 py-2 font-medium" />
                  </>
                ) : (
                  <>
                    <SortTh label="NC bill" sortKey="nc_bill_no" sort={itemSort} onSort={onItemSort} className="px-3 py-2 font-medium text-left" />
                    {/* NC's own billed figure, next to ours. Without it an
                        invoice that matched for the WRONG amount reads as
                        perfectly reconciled on this screen. */}
                    <SortTh label="NC billed" sortKey="nc_money_cr" sort={itemSort} onSort={onItemSort} align="right" className="px-3 py-2 font-medium" />
                    <SortTh label="NC open" sortKey="nc_money_bal" sort={itemSort} onSort={onItemSort} align="right" className="px-3 py-2 font-medium" />
                  </>
                )}
              </Tr>
            </THead>
            <TBody>
              {loadingItems && !items ? (
                <Tr><Td align="center" colSpan={11}>
                  <Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" />
                </Td></Tr>
              ) : itemsError ? (
                // A failed request must never read as "nothing here" — that is
                // how a summary saying 145 sits next to an empty table with no
                // hint that anything went wrong.
                <Tr><Td align="center" colSpan={11} className="text-danger-fg">
                  Could not load this category: {String((itemsError as Error).message ?? itemsError)}
                </Td></Tr>
              ) : (items?.items.length ?? 0) === 0 ? (
                <Tr><Td align="center" colSpan={11} className="text-fg-muted">
                  Nothing in this category.
                </Td></Tr>
              ) : items!.items.map((r) => (
                <Tr key={r.id} className="hover:bg-surface-subtle/60">
                  <Td className="font-mono text-xs">{r.ap_invoice_number ?? '—'}</Td>
                  <Td>{r.vendor_name ?? '—'}</Td>
                  <Td className="font-mono text-xs">{r.vendor_invoice_number ?? '—'}</Td>
                  <Td align="right" className="font-mono tabular-nums">{money(r.total_amount)}</Td>
                  <Td className="text-xs text-fg-secondary">{day(r.invoice_date)}</Td>
                  <Td className="text-xs text-fg-secondary">{day(r.due_date)}</Td>
                  <Td className="font-mono text-xs text-fg-muted">
                    {r.po_number ?? (r.unapproved_bill_no
                      ? <span className="text-warning-fg">{r.unapproved_bill_no}</span>
                      : '—')}
                  </Td>
                  {category === 'invoice_no_mismatch' ? (
                    <>
                      <Td className="font-mono text-xs text-warning-fg">
                        {r.nc_amount_match?.invoice_no ?? '—'}
                      </Td>
                      <Td className="font-mono text-xs text-fg-muted">
                        {r.nc_amount_match?.bill_no ?? '—'}
                      </Td>
                      {/* A small gap means the same invoice keyed wrong; a large
                          one may just be next period's identical charge. */}
                      <Td className={cn('px-3 py-2 text-right font-mono tabular-nums text-xs',
                                        (r.nc_amount_match?.day_gap ?? 0) > 60
                                          ? 'text-fg-muted' : 'text-fg-secondary')}>
                        {r.nc_amount_match?.day_gap ?? '—'}d
                      </Td>
                    </>
                  ) : (
                    <>
                      <Td className="font-mono text-xs text-fg-muted">{r.nc?.bill_no ?? '—'}</Td>
                      <Td align="right" className="font-mono tabular-nums">
                        {money(r.nc?.money_cr)}
                        {r.nc?.amount_differs && (
                          <div className={cn('text-2xs', r.nc.is_credit ? 'text-fg-muted' : 'text-warning-fg')}>
                            {r.nc.is_credit
                              ? 'credit note'
                              : <>{Number(r.nc.amount_gap) > 0 ? '+' : ''}{money(r.nc.amount_gap)} vs ours</>}
                          </div>
                        )}
                      </Td>
                      <Td align="right" className="font-mono tabular-nums">{money(r.nc?.money_bal)}</Td>
                    </>
                  )}
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>

        <div className="mt-3 flex items-center gap-3 text-sm text-fg-muted">
          <span>{(items?.total ?? 0).toLocaleString()} invoices</span>
          <div className="ml-auto flex gap-2">
            <Button variant="secondary" size="sm" disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - PAGE))}
                   >Prev</Button>
            <Button variant="secondary" size="sm" disabled={offset + PAGE >= (items?.total ?? 0)}
                    onClick={() => setOffset(offset + PAGE)}
                   >Next</Button>
          </div>
        </div>

        {/* The mirror image of the invoice-number check: there the money agreed
            and the number did not; here the number agrees and the money does
            not. Kept out of the category table on purpose — it cuts across two
            of those categories, and turning it into a seventh would move
            invoices out of the open list, which is the cash-flow figure. */}
        <div className="mt-8">
          <Button variant="subtle" size="xs" onClick={() => setShowGaps((v) => !v)}
                  className="text-sm">
            <Scale className="h-4 w-4" />
            Matched, but NC billed a different amount
            <Badge size="sm" variant={(gaps?.total ?? 0) > 0 ? 'danger' : 'neutral'}>
              {gaps?.total ?? 0}
            </Badge>
          </Button>
          {showGaps && (
            <>
              <p className="mt-2 max-w-3xl text-xs leading-relaxed text-fg-muted">
                Same invoice number, same supplier, different money. These pass every other
                check on this page — matched, on NC&rsquo;s books, shown as reconciled — which is
                what makes them worth looking at.{' '}
                {gaps && gaps.nc_billed_more.invoices > gaps.nc_billed_less.invoices * 3 && (
                  <span className="text-fg-secondary">
                    <strong>NC is higher in {gaps.nc_billed_more.invoices} of {gaps.total}</strong>,
                    by {money(gaps.nc_billed_more.amount)} in total. That is a direction, not keying
                    noise: freight, deposits and environmental fees printed on an invoice fall
                    outside our amount-plus-tax fields, while finance keys the paper total into NC.
                  </span>
                )}
                {gaps && gaps.drafts_excluded > 0 && (
                  <> {gaps.drafts_excluded} draft{gaps.drafts_excluded === 1 ? '' : 's'} also
                    differ and are excluded — a draft can still be edited.</>
                )}
              </p>
              <div className="mt-3 overflow-x-auto rounded-lg border border-line">
                <Table density="compact" className="min-w-[860px]">
                  <THead>
                    <Tr className="bg-surface-subtle text-xs text-fg-muted">
                      <SortTh label="AP No." sortKey="ap_invoice_number" sort={gapSort.sort} onSort={gapSort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="Vendor" sortKey="vendor" sort={gapSort.sort} onSort={gapSort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="Invoice no." sortKey="invoice" sort={gapSort.sort} onSort={gapSort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="PO" sortKey="po" sort={gapSort.sort} onSort={gapSort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="Ours" sortKey="ours" sort={gapSort.sort} onSort={gapSort.onSort} align="right" className="px-3 py-2 font-medium" />
                      <SortTh label="NC billed" sortKey="nc_billed" sort={gapSort.sort} onSort={gapSort.onSort} align="right" className="px-3 py-2 font-medium" />
                      <SortTh label="Difference" sortKey="difference" sort={gapSort.sort} onSort={gapSort.onSort} align="right" className="px-3 py-2 font-medium" />
                      <SortTh label="NC bill" sortKey="nc_bill" sort={gapSort.sort} onSort={gapSort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="State in NC" sortKey="state" sort={gapSort.sort} onSort={gapSort.onSort} className="px-3 py-2 font-medium text-left" />
                    </Tr>
                  </THead>
                  <TBody>
                    {(gaps?.items.length ?? 0) === 0 ? (
                      <Tr><Td align="center" colSpan={9} className="text-fg-muted">
                        Every matched invoice agrees with NC to the cent.
                      </Td></Tr>
                    ) : gapSort.rows.map((r) => (
                      <Tr key={r.id} className="hover:bg-surface-subtle/60">
                        <Td className="font-mono text-xs">{r.ap_invoice_number ?? '—'}</Td>
                        <Td>{r.vendor_name ?? '—'}</Td>
                        <Td className="font-mono text-xs">{r.vendor_invoice_number ?? '—'}</Td>
                        {/* Every one of these carries a PO — so the money that
                            went missing is money against a purchase order. */}
                        <Td className="font-mono text-xs text-fg-muted">{r.po_number ?? '—'}</Td>
                        <Td align="right" className="font-mono tabular-nums">{money(r.total_amount)}</Td>
                        <Td align="right" className="font-mono tabular-nums">{money(r.nc_money_cr)}</Td>
                        <Td className={cn('px-3 py-2 text-right font-mono tabular-nums font-semibold',
                                          r.is_credit ? 'text-fg-muted' : 'text-danger-fg')}>
                          {Number(r.amount_gap) > 0 ? '+' : ''}{money(r.amount_gap)}
                        </Td>
                        <Td className="font-mono text-xs text-fg-muted">
                          {r.nc_bill_no ?? '—'}
                          {(r.nc_lines ?? 1) > 1 && (
                            <span className="ml-1 text-2xs text-fg-muted">{r.nc_lines} lines</span>
                          )}
                        </Td>
                        <Td className="text-xs">
                          {r.is_credit ? (
                            <Badge size="sm">
                              credit note
                            </Badge>
                          ) : r.category === 'in_nc_open' ? (
                            <span className="text-fg-secondary">open · {money(r.nc_money_bal)}</span>
                          ) : (
                            // Already paid at NC's figure — nothing left to
                            // correct on the money, only on our record.
                            <span className="text-fg-muted">settled</span>
                          )}
                        </Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </div>
            </>
          )}
        </div>

        {/* A different question from the NC comparison: not "is it on NC's
            books" but "can this date be right at all". Kept apart so it never
            competes with the reconciliation counts. */}
        <div className="mt-8">
          <Button variant="subtle" size="xs" onClick={() => setShowAnomalies((v) => !v)}
                  className="text-sm">
            <CalendarX2 className="h-4 w-4" />
            Impossible invoice dates
            <Badge size="sm" variant={(anomalies?.total ?? 0) > 0 ? 'warning' : 'neutral'}>
              {anomalies?.total ?? 0}
            </Badge>
          </Button>
          {showAnomalies && (
            <>
              <p className="mt-2 max-w-3xl text-xs leading-relaxed text-fg-muted">
                A due date before the invoice date, or an invoice dated in the future — almost
                always a day/month swap out of OCR. These feed the cash-flow buckets directly,
                so a September invoice read as December moves real money into a month it will
                never be paid in.
              </p>
              <div className="mt-3 overflow-x-auto rounded-lg border border-line">
                <Table density="compact" className="min-w-[720px]">
                  <THead>
                    <Tr className="bg-surface-subtle text-xs text-fg-muted">
                      <SortTh label="AP No." sortKey="ap_invoice_number" sort={anomalySort.sort} onSort={anomalySort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="Vendor" sortKey="vendor" sort={anomalySort.sort} onSort={anomalySort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="Invoice no." sortKey="invoice" sort={anomalySort.sort} onSort={anomalySort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="Amount" sortKey="amount" sort={anomalySort.sort} onSort={anomalySort.onSort} align="right" className="px-3 py-2 font-medium" />
                      <SortTh label="Invoice date" sortKey="invoice_date" sort={anomalySort.sort} onSort={anomalySort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="Due date" sortKey="due_date" sort={anomalySort.sort} onSort={anomalySort.onSort} className="px-3 py-2 font-medium text-left" />
                      <SortTh label="Why" sortKey="why" sort={anomalySort.sort} onSort={anomalySort.onSort} className="px-3 py-2 font-medium text-left" />
                    </Tr>
                  </THead>
                  <TBody>
                    {(anomalies?.items.length ?? 0) === 0 ? (
                      <Tr><Td align="center" colSpan={7} className="text-fg-muted">
                        No impossible dates.
                      </Td></Tr>
                    ) : anomalySort.rows.map((r) => (
                      <Tr key={r.id}>
                        <Td className="font-mono text-xs">{r.ap_invoice_number ?? '—'}</Td>
                        <Td>{r.vendor_name ?? '—'}</Td>
                        <Td className="font-mono text-xs">{r.vendor_invoice_number ?? '—'}</Td>
                        <Td align="right" className="font-mono tabular-nums">{money(r.total_amount)}</Td>
                        <Td className="text-xs text-warning-fg">{day(r.invoice_date)}</Td>
                        <Td className="text-xs text-fg-secondary">{day(r.due_date)}</Td>
                        <Td className="text-xs text-fg-muted">{anomalyReason(r.reason)}</Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </div>
            </>
          )}
        </div>
      </div>
    </PortalChromeLayout>
  )
}
