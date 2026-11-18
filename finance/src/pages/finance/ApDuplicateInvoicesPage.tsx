/**
 * AP Duplicate Invoices — vendor + invoice numbers NC has on more than one payable.
 *
 * Finance's rule: vendor name + invoice number is unique. NC65 does not check
 * it, and the same invoice has been entered and paid twice. The same number
 * from two different vendors is NOT a duplicate. NC cannot be changed from here, so this page
 * is the check NC does not have: it is recomputed from the NC mirror on every
 * load, and the hourly AP sync raises one standing task for AP when something
 * new appears (finance-api services/ap_duplicate_invoice_tasks.py).
 *
 * The status column is what AP has to do, in the order it gets more expensive:
 *   Stop approval  — a draft repeats an invoice already entered; do not approve it
 *   Stop payment   — an approved extra copy still has a balance; hold it back
 *   Recover        — every copy is settled; the supplier was paid twice
 *   Review         — same vendor and number, different amounts; still a breach,
 *                    cleared by reviewing it (e.g. as a deliberate split)
 *
 * A group the reviewer confirms is fine is marked reviewed with a reason. The
 * verdict covers the bills that were on screen: if another copy lands later,
 * the group comes back.
 */
import { Fragment, useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Download, Loader2, Undo2 } from 'lucide-react'
import { Button, ChoiceCard, Input, Modal as ShellModal, Select, SortTh, TBody, THead, Table, Td, Textarea, Th, Tr, sortParams, useSort, Badge, type BadgeTone } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn, formatDate } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { ListPager } from '@/components/ListPager'

const PAGE_SIZE = 50
/** GET /ap-duplicate-invoices sort keys (finance-api ap_duplicate_invoices.FINDING_SORT). */
type FindingSortKey = 'action' | 'invoice' | 'supplier' | 'currency' | 'amount' | 'bills'
  | 'extra' | 'open' | 'review'

type Kind = 'exact' | 'amount_differs' | 'pending'
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
  key: string; kind: Kind; kind_label: string; vendor: string; status: Status
  invoice_no: string | null; invoice_no_norm: string; currency: string | null
  supplier_code: string | null; supplier_name: string | null; amount: string | null
  extra_copies: number; extra_amount: string; open_exposure: string
  bill_nos: string[]; reviewed: boolean; review: Review | null; reopened: boolean
  bills: Bill[]
}
interface Resp { summary: Record<Kind, KindSummary>; total: number; findings: Finding[] }
interface Reason { code: string; label: string }

const KIND_ORDER: Kind[] = ['exact', 'amount_differs', 'pending']
const KIND_TAB: Record<Kind, string> = {
  exact: 'Same vendor + invoice, same amount',
  amount_differs: 'Same vendor + invoice, different amounts',
  pending: 'Unapproved drafts',
}
const STATUS: Record<Status, { label: string; tone: BadgeTone; hint: string }> = {
  stop_approval: { label: 'Stop approval', tone: 'danger',
                   hint: 'A draft repeats an invoice already entered — do not approve it' },
  stop_payment: { label: 'Stop payment', tone: 'danger',
                  hint: 'An extra copy still has a balance in NC — hold it back from payment' },
  recover: { label: 'Recover', tone: 'warning',
             hint: 'Every copy is settled — the supplier was paid more than once' },
  review: { label: 'Review', tone: 'outline',
            hint: 'Same vendor and invoice number, different amounts — check it; mark a deliberate split as reviewed' },
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
    <ShellModal open onClose={onCancel} title={<>Mark invoice {finding.invoice_no ?? finding.invoice_no_norm} as reviewed</>} description="The group leaves the working list and stops counting toward the AP task. NC is not changed. If another bill with this invoice number is entered later, the group comes back." closeOnEscape={false} stacked size="full" className="max-w-lg"
      footer={<>
        <Button variant="secondary" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={() => onDone(reason, note.trim())} disabled={needsNote || pending}
               >
          {pending ? 'Saving…' : 'Mark reviewed'}
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
                    placeholder="e.g. Supplier issued credit note CN-1042 on 2026-10-02"
                    className="w-full" />
        </div>
        <div className="rounded border border-line bg-surface-subtle px-2.5 py-1.5 font-mono text-2xs text-fg-muted">
          {finding.bill_nos.join(', ')}
        </div>
        {error && <p className="text-xs text-danger-fg">{error}</p>}
      </div>
    </ShellModal>
  )
}

function BillsTable({ bills }: { bills: Bill[] }) {
  return (
    <Table density="compact">
      <THead>
        <Tr className="text-left text-2xs text-fg-muted">
          <Th>NC bill</Th>
          <Th>Status</Th>
          <Th>Bill date</Th>
          <Th>Supplier</Th>
          <Th align="right">Invoice amount</Th>
          <Th align="right">Still open</Th>
          <Th align="right">Paid on bill</Th>
          <Th>NC payments</Th>
        </Tr>
      </THead>
      <TBody>
        {bills.map((b) => (
          <Tr key={`${b.bill_no}-${b.supplier_code}`}>
            <Td className="font-mono">{b.bill_no}</Td>
            <Td>
              <span className={cn(!b.effective && 'font-medium text-danger-fg')}>{b.status_label}</span>
              {b.dismissed && <span className="ml-1 text-fg-muted">(ignored)</span>}
            </Td>
            <Td className="font-mono">{day(b.bill_date)}</Td>
            <Td>
              <span className="font-mono text-fg-muted">{b.supplier_code}</span> {b.supplier_name}
            </Td>
            <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums',
                              Number(b.amount) < 0 && 'text-success-fg')}>{money(b.amount)}</Td>
            <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums',
                              Number(b.open) !== 0 && 'font-semibold text-danger-fg')}>{money(b.open)}</Td>
            <Td align="right" className="font-mono tabular-nums">{money(b.paid_on_bill)}</Td>
            <Td className="font-mono text-2xs text-fg-muted">
              {b.payment_nos ?? '—'}{b.last_paid && <span className="text-fg-muted"> · {day(b.last_paid)}</span>}
            </Td>
          </Tr>
        ))}
      </TBody>
    </Table>
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

  // Groups are computed, sorted and paged by the API (a group's bills always
  // travel with it). A page belongs to the filter it was turned on; a new
  // filter or a new sort starts at page 1.
  const filterQs = new URLSearchParams({ kind, reviewed })
  if (search.trim()) filterQs.set('q', search.trim())
  const [pageAt, setPageAt] = useState({ key: filterQs.toString(), page: 1 })
  const page = pageAt.key === filterQs.toString() ? pageAt.page : 1
  const setPage = (p: number) => setPageAt({ key: filterQs.toString(), page: p })
  const [sort, onSort] = useSort<FindingSortKey>(null, () => setPage(1))
  const sortQs = (() => {
    const { sort: key, dir } = sortParams(sort)
    return key && dir ? `&sort=${key}&dir=${dir}` : ''
  })()
  const { data, isFetching, error } = useQuery({
    queryKey: ['ap-dup-invoices', kind, reviewed, search.trim(), page, sort],
    queryFn: () => financeApi.get<Resp>(
      `/ap-duplicate-invoices?${filterQs}&limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}${sortQs}`),
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
  // Expansion is keyed by the group key, so it survives paging and sorting.
  const rows = data?.findings ?? []

  const toggle = (key: string) => setExpanded((prev) => {
    const next = new Set(prev)
    if (next.has(key)) next.delete(key); else next.add(key)
    return next
  })

  // Every matching group (not just this page), in the on-screen sort.
  const exportCsv = async () => {
    const all: Finding[] = []
    for (let off = 0; ; off += 2000) {
      const r = await financeApi.get<Resp>(
        `/ap-duplicate-invoices?${filterQs}&limit=2000&offset=${off}${sortQs}`)
      all.push(...r.findings)
      if (all.length >= r.total || r.findings.length === 0) break
    }
    const head = ['Action', 'Kind', 'Invoice no', 'Currency', 'Supplier code', 'Supplier',
                  'Invoice amount', 'Extra copies', 'Billed twice', 'Still payable on copies',
                  'NC bill', 'Bill status', 'Bill date', 'Bill supplier', 'Amount on bill',
                  'Open on bill', 'Paid on bill', 'NC payments', 'Reviewed', 'Reason', 'Note']
    const body = all.flatMap((f) => f.bills.map((b) => [
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
        <div className="mb-5 rounded-lg border border-line bg-surface-subtle px-4 py-3 text-sm leading-relaxed text-fg-secondary">
          <p className="mb-1.5">
            A vendor&rsquo;s invoice number must be unique. NC does not check it, so the same invoice
            can be entered — and paid — more than once. This page is that check: every group is one
            vendor and one invoice number on more than one payable, whatever the amounts. The same
            number from two different vendors is not a duplicate. It reads NC&rsquo;s payables after
            every AP sync (hourly), and AP gets a task when something new turns up.
          </p>
          <p className="text-fg-muted">
            A bill that was reversed by an equal credit is not counted as a copy. The earlier a
            duplicate is caught the cheaper it is: <strong>stop</strong> a draft before approval,
            <strong> hold</strong> an approved copy back from payment, and only a copy already paid
            has to be <strong>recovered</strong> from the supplier. Nothing here changes NC — the fix
            (void, credit note) is made in NC and this page follows on the next sync.
          </p>
        </div>

        <div className="mb-4 grid gap-3 sm:grid-cols-3">
          {KIND_ORDER.map((k) => {
            const s = summary?.[k]
            const byCcy = Object.entries(s?.by_currency ?? {})
            return (
              <ChoiceCard key={k} selected={kind === k} onClick={() => { setKind(k); setExpanded(new Set()) }}
                      className="items-stretch gap-0 rounded-lg p-3">
                <div className="flex items-center gap-1.5">
                  {s && s.actionable && s.unreviewed > 0
                    ? <AlertTriangle className="h-3.5 w-3.5 text-warning-fg" />
                    : <CheckCircle2 className="h-3.5 w-3.5 text-primary-700" />}
                  <span className="text-sm font-semibold text-fg">{KIND_TAB[k]}</span>
                </div>
                <p className="mt-1.5 text-xs text-fg-muted">
                  <span className="font-mono text-base font-semibold text-fg">{s?.unreviewed ?? '—'}</span>
                  {' '}to review{s && s.reviewed > 0 && <> · {s.reviewed} reviewed</>}
                </p>
                {k === 'exact' && byCcy.map(([ccy, t]) => (
                  <p key={ccy} className="mt-0.5 text-2xs text-fg-muted">
                    <span className="font-mono">{ccy}</span> billed twice {money(t.extra_amount)}
                    {Number(t.open_exposure) > 0 && (
                      <span className="font-medium text-danger-fg"> · {money(t.open_exposure)} still payable</span>
                    )}
                  </p>
                ))}
              </ChoiceCard>
            )
          })}
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-lg border border-line-strong p-0.5 text-xs">
            {(['no', 'yes', 'all'] as const).map((r) => (
              <Button key={r} onClick={() => setReviewed(r)}
                      variant={reviewed === r ? 'primary' : 'subtle'} size="xs" aria-pressed={reviewed === r}>
                {r === 'no' ? 'To review' : r === 'yes' ? 'Reviewed' : 'All'}
              </Button>
            ))}
          </div>
          <Input value={search} onChange={(e) => setSearch(e.target.value)}
                 placeholder="Invoice no, supplier, or NC bill no"
                 className="w-72" />
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-fg-muted" />}
          <span className="ml-auto text-xs text-fg-muted">{data ? `${data.total} groups` : ''}</span>
          <Button variant="secondary" size="xs" onClick={exportCsv} disabled={!rows.length}>
            <Download className="h-3.5 w-3.5" /> CSV
          </Button>
        </div>

        {error ? (
          <div className="rounded-lg border border-danger-line bg-danger-soft px-4 py-3 text-sm text-danger-fg">
            {(error as Error).message}
          </div>
        ) : !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
        ) : rows.length === 0 ? (
          <div className="rounded-lg border border-line px-4 py-10 text-center text-sm text-fg-muted">
            {reviewed === 'no' ? 'Nothing left to review here.' : 'No groups.'}
          </div>
        ) : (
          <>
          <div className="overflow-x-auto rounded-lg border border-line">
            <Table density="compact">
              <THead>
                <Tr className="text-left text-xs text-fg-muted">
                  <Th className="w-6" />
                  <SortTh label="Action" sortKey="action" sort={sort} onSort={onSort} className="px-2.5 py-2 font-medium" />
                  <SortTh label="Invoice no" sortKey="invoice" sort={sort} onSort={onSort} className="px-2.5 py-2 font-medium" />
                  <SortTh label="Supplier" sortKey="supplier" sort={sort} onSort={onSort} className="px-2.5 py-2 font-medium" />
                  <SortTh label="Ccy" sortKey="currency" sort={sort} onSort={onSort} className="px-2.5 py-2 font-medium" />
                  <SortTh label="Invoice amount" sortKey="amount" sort={sort} onSort={onSort} align="right" className="px-2.5 py-2 font-medium" />
                  <SortTh label="Bills" sortKey="bills" sort={sort} onSort={onSort} align="right" className="px-2.5 py-2 font-medium" />
                  {showMoney && <SortTh label="Billed twice" sortKey="extra" sort={sort} onSort={onSort} align="right" className="px-2.5 py-2 font-medium" />}
                  {showMoney && <SortTh label="Still payable" sortKey="open" sort={sort} onSort={onSort} align="right" className="px-2.5 py-2 font-medium" />}
                  <SortTh label="Review" sortKey="review" sort={sort} onSort={onSort} className="px-2.5 py-2 font-medium" />
                </Tr>
              </THead>
              <TBody>
                {rows.map((f) => {
                  const open = expanded.has(f.key)
                  const st = STATUS[f.status]
                  const suppliers = f.supplier_name
                    ?? Array.from(new Set(f.bills.map((b) => b.supplier_name))).join(' / ')
                  return (
                    <Fragment key={f.key}>
                      <Tr className="cursor-pointer hover:bg-surface-subtle"
                          onClick={() => toggle(f.key)}>
                        <Td className="text-fg-muted">
                          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </Td>
                        <Td>
                          <Badge size="sm" variant={st.tone} title={st.hint}>
                            {st.label}
                          </Badge>
                          {f.reopened && (
                            <span className="ml-1 text-2xs text-warning-fg" title="Reviewed before, but a bill has been added since">
                              new copy
                            </span>
                          )}
                        </Td>
                        <Td className="font-mono">{f.invoice_no ?? f.invoice_no_norm}</Td>
                        <Td>
                          {f.supplier_code && <span className="font-mono text-xs text-fg-muted">{f.supplier_code} </span>}
                          {suppliers}
                        </Td>
                        <Td className="font-mono text-xs">{f.currency}</Td>
                        <Td align="right" className="font-mono tabular-nums">{money(f.amount)}</Td>
                        <Td align="right" className="font-mono tabular-nums">{f.bill_nos.length}</Td>
                        {showMoney && (
                          <Td align="right" className="font-mono tabular-nums">{money(f.extra_amount)}</Td>
                        )}
                        {showMoney && (
                          <Td className={cn('px-2.5 py-2 text-right font-mono tabular-nums',
                                            Number(f.open_exposure) > 0 && 'font-semibold text-danger-fg')}>
                            {money(f.open_exposure)}
                          </Td>
                        )}
                        <Td onClick={(e) => e.stopPropagation()}>
                          {f.review ? (
                            <div className="flex items-center gap-2 text-xs">
                              <span className="text-fg-secondary" title={f.review.note ?? ''}>
                                {reasonLabel[f.review.reason] ?? f.review.reason} · {f.review.reviewed_by_name ?? '—'} · {day(f.review.reviewed_at)}
                              </span>
                              <Button aria-label="Put back on the list" variant="subtle" size="icon-xs" onClick={() => unreview.mutate(f.key)} disabled={unreview.isPending}
                                      title="Put back on the list"
                                     >
                                <Undo2 className="h-3.5 w-3.5" />
                              </Button>
                            </div>
                          ) : (
                            <Button variant="secondary" size="xs" onClick={() => { setReviewError(null); setReviewing(f) }}
                                   >
                              Mark reviewed
                            </Button>
                          )}
                        </Td>
                      </Tr>
                      {open && (
                        <Tr className="bg-surface-subtle/60">
                          <Td />
                          <Td colSpan={showMoney ? 9 : 7}>
                            <BillsTable bills={f.bills} />
                            {f.review?.note && (
                              <p className="mt-2 text-xs text-fg-muted">Note: {f.review.note}</p>
                            )}
                          </Td>
                        </Tr>
                      )}
                    </Fragment>
                  )
                })}
              </TBody>
            </Table>
          </div>
          <ListPager page={page} pageSize={PAGE_SIZE} total={data.total} noun="groups" onPage={setPage} />
          </>
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
        <div className="fixed bottom-4 right-4 z-[60] max-w-sm rounded-lg border border-warning-line bg-warning-soft px-4 py-3 text-sm text-warning-fg shadow">
          {reviewError}
          <Button variant="link" onClick={() => setReviewError(null)} className="ml-2 text-xs">Dismiss</Button>
        </div>
      )}
    </PortalChromeLayout>
  )
}
