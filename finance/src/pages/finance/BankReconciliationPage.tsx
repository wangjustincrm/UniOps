/**
 * Bank Reconciliation workbench (v2).
 *
 * The screen reproduces what finance does by hand, side by side:
 *
 *   left   the bank statement, in the order the bank printed it
 *   right  NC's ledger for that bank account — 100201 expanded by the
 *          bank-account auxiliary, which is the only thing separating RBC from
 *          Bank of China
 *
 * A statement line is usually a MERGED payment ("Direct Deposits (PDS) service
 * total 48,336.03" is 18 vendor payments), so matches are groups: N statement
 * lines against M ledger lines, explained where possible by the payment file the
 * bank hands back. Clicking a cleared line highlights the whole group on both
 * sides, which is the question the page exists to answer — what is this one line
 * actually made of.
 *
 * The number that matters is Difference. It is the statement's closing balance
 * minus the ledger's, NOT a total of what has been matched, so it cannot be
 * driven to zero by matching things wrongly.
 *
 * Styling follows the Portal convention (CoaConfigPage): neutral palette, zebra
 * rows, code chips, status pills, #085E5E primary.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, Download, FileText, History, Landmark, Link2, Loader2,
  Lock, LockOpen, Sparkles, Undo2, Upload,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import {
  financeApi, financeDownload, financeUpload, financeUploadMany,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

interface Account {
  id: string; name: string; bank_name: string; account_masked: string | null
  currency: string; nc_bank_account_code: string | null
}
interface Summary {
  statement_opening: string | null; statement_closing: string | null
  statement_verified: boolean
  book_opening: string; book_closing: string
  cleared_debit_total: string; cleared_credit_total: string
  cleared_debit_count: number; cleared_credit_count: number
  outstanding_bank_lines: number; outstanding_book_lines: number
  difference: string; opening_difference: string; movement_difference: string
  bank_account: string; status: string
}
interface BankLine {
  id: string; txn_date: string; description: string; amount: string
  running_balance: string | null; sort_seq: number | null; cleared: boolean
}
interface BookLine {
  jv_line_id: string; jv_number: string; voucher_date: string; summary: string | null
  amount: string; contra_kind: string; contra_label: string; contra_codes: string[]
  cleared: boolean
}
interface MatchGroup {
  id: string; method: string; amount: string; note: string | null
  advice_id: string | null; bank_ids: string[]
  book_lines: { jv_line_id: string | null; nc_voucher_pk: string | null; amount: string }[]
}
/** A row of `GET /bank-recon/{account}/reconciliations` — the history the backend
 *  has always served and nothing ever asked for. */
interface PeriodRow {
  id: string; period_start: string; period_end: string; status: string
  difference: string; statement_closing: string | null; book_closing: string | null
  finalized_at: string | null; has_report: boolean
}
interface Period {
  id: string; status: string; period_start: string; period_end: string; currency: string
  summary: Summary; bank_lines: BankLine[]; book_lines: BookLine[]; matches: MatchGroup[]
}
interface Finding {
  kind: string; message: string; bank_ids: string[]; advice_ids: string[]; book_ids: string[]
}
interface AutoResult {
  matched_groups: number; bank_lines_cleared: number; book_lines_cleared: number
  by_method: Record<string, number>; findings: Finding[]; summary: Summary
}
/** A ranked ledger line for one statement line — `GET .../candidates/{txn_id}`,
 *  written for exactly the case where the ladder gives up and a person has to
 *  clear the line by hand, and never called until now. */
interface Candidate {
  jv_line_id: string; jv_number: string; voucher_date: string
  summary: string | null; amount: string; contra_kind: string; exact: boolean
}
interface AdviceLine {
  id: string; seq: number; payee_code: string | null; payee_name: string | null
  amount: string; matched_jv_line_id: string | null
}

/** How a group was cleared, in words a reviewer can act on. */
// How much a match is worth, not just what it is called. `confirmation_no` is the
// bank's own reference on both sides; `advice_*` is a payment file whose total and
// per-payee lines both reconcile; `book_subset` is nothing but "these amounts add
// up", which is why it is the one the findings call out. Rendering all seven the
// same weight let the weakest read like the strongest.
const METHOD_TRUST: Record<string, 'evidence' | 'amounts' | 'manual'> = {
  confirmation_no: 'evidence',
  advice_total: 'evidence',
  advice_combo: 'evidence',
  direct: 'amounts',
  subset_sum: 'amounts',
  book_subset: 'amounts',
  manual: 'manual',
}
const TRUST_CHIP: Record<string, string> = {
  evidence: 'border-green-300 bg-green-50 text-green-800',
  amounts: 'border-amber-300 bg-amber-50 text-amber-800',
  manual: 'border-sky-300 bg-sky-50 text-sky-800',
}
const TRUST_WORD: Record<string, string> = {
  evidence: 'backed by a document',
  amounts: 'amounts only — no document',
  manual: 'matched by a person',
}

const METHOD_LABEL: Record<string, string> = {
  advice_total: 'Payment file',
  advice_combo: 'Several payment files',
  confirmation_no: 'Confirmation number',
  direct: 'One to one',
  subset_sum: 'Split across ledger lines',
  book_subset: 'Inferred — no payment file',
  manual: 'Matched by hand',
}

const KIND_TONE: Record<string, string> = {
  ap: 'bg-neutral-100 text-neutral-600',
  bank_transfer: 'bg-blue-50 text-blue-700',
  payroll: 'bg-purple-50 text-purple-700',
  credit_card: 'bg-amber-50 text-amber-700',
  other_payable: 'bg-neutral-100 text-neutral-600',
  bank_fee: 'bg-neutral-100 text-neutral-500',
  mixed: 'bg-amber-50 text-amber-700',
}

// The ledger rows get `contra_label` from the server; the candidates endpoint
// returns only the kind, so the same words live here rather than being invented
// per call site. Mirrors bank_book.KIND_LABELS.
const KIND_LABEL_UI: Record<string, string> = {
  ap: 'Accounts payable',
  bank_transfer: 'Bank transfer',
  cash: 'Cash',
  payroll: 'Payroll',
  credit_card: 'Credit card',
  other_payable: 'Other payable',
  bank_fee: 'Bank charges / interest',
  mixed: 'Several accounts',
  other: 'Other',
  unknown: '—',
}

function money(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === '') return '—'
  return Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** Dates arrive as plain YYYY-MM-DD. Never through `new Date()` — that reads
 *  them as UTC midnight and shows the previous day west of Greenwich. */
function shortDate(iso: string): string {
  const [, m, d] = iso.split('-')
  return `${d}/${m}`
}

function Pill({ children, tone }: { children: React.ReactNode; tone?: string }) {
  return (
    <span className={cn('whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium',
      tone ?? 'bg-neutral-100 text-neutral-600')}>{children}</span>
  )
}

function Stat({ label, value, tone, hint }: {
  label: string; value: string; tone?: 'pos' | 'neg' | 'warn'; hint?: string
}) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-white px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-neutral-500">{label}</div>
      <div className={cn('font-mono text-sm font-semibold tabular-nums',
        tone === 'neg' && 'text-red-600', tone === 'pos' && 'text-green-700',
        tone === 'warn' && 'text-amber-600')}>{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-neutral-400">{hint}</div>}
    </div>
  )
}

export default function BankReconciliationPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [accountId, setAccountId] = useState('')
  const [periodStart, setPeriodStart] = useState('')
  const [periodEnd, setPeriodEnd] = useState('')
  const [reconId, setReconId] = useState('')
  const [pickedBank, setPickedBank] = useState<Set<string>>(new Set())
  const [pickedBook, setPickedBook] = useState<Set<string>>(new Set())
  const [focusGroup, setFocusGroup] = useState<string | null>(null)
  const [openAdvice, setOpenAdvice] = useState<string | null>(null)
  const [findings, setFindings] = useState<Finding[]>([])
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const stmtInput = useRef<HTMLInputElement>(null)
  const adviceInput = useRef<HTMLInputElement>(null)

  const flash = (kind: 'ok' | 'err', text: string) => {
    setBanner({ kind, text })
    if (kind === 'ok') setTimeout(() => setBanner(null), 6000)
  }

  const { data: perms } = useQuery({
    queryKey: ['coa-permissions'],
    queryFn: () => financeApi.get<{ can_manage: boolean }>('/coa/permissions'),
  })
  const canManage = perms?.can_manage ?? false

  const { data: accounts = [] } = useQuery({
    queryKey: ['bank-accounts'],
    queryFn: () => financeApi.get<Account[]>('/bank/accounts'),
  })
  const account = accounts.find((a) => a.id === accountId) ?? null

  const { data: period, isFetching: periodLoading } = useQuery({
    queryKey: ['bank-recon-period', reconId],
    queryFn: () => financeApi.get<Period>(`/bank-recon/reconciliations/${reconId}`),
    enabled: !!reconId,
  })

  const { data: adviceLines = [] } = useQuery({
    queryKey: ['bank-advice-lines', openAdvice],
    queryFn: () => financeApi.get<AdviceLine[]>(`/bank-recon/advices/${openAdvice}/lines`),
    enabled: !!openAdvice,
  })

  // Every period this account has ever had. Signed-off ones keep their snapshot
  // and their PDF, so the history is the audit trail — it just had no way in.
  const { data: history = [] } = useQuery({
    queryKey: ['bank-recon-history', accountId],
    enabled: !!accountId,
    queryFn: () => financeApi.get<PeriodRow[]>(`/bank-recon/${accountId}/reconciliations`),
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['bank-recon-period', reconId] })
    qc.invalidateQueries({ queryKey: ['bank-recon-history', accountId] })
  }
  const clearPicks = () => { setPickedBank(new Set()); setPickedBook(new Set()) }

  const openPeriod = useMutation({
    mutationFn: () => financeApi.post<{ id: string; summary: Summary }>(
      `/bank-recon/${accountId}/reconciliations`,
      { period_start: periodStart, period_end: periodEnd }),
    onSuccess: (r) => { setReconId(r.id); setFindings([]); clearPicks() },
    onError: (e: Error) => flash('err', e.message),
  })

  const importStatement = useMutation({
    mutationFn: (file: File) =>
      financeUpload<{ verified: boolean; verify_errors: string[]; lines: number; imported: number; retention_warning: string | null }>(
        `/bank-recon/${accountId}/statements`, file),
    onSuccess: (r) => {
      const head = `Statement read: ${r.lines} lines (${r.imported} new).`
      if (!r.verified) {
        flash('err', `${head} It does NOT tie to its own printed totals, so it cannot be reconciled yet — ${r.verify_errors.join(' ')}`)
      } else {
        flash('ok', `${head} Verified against the statement's own totals.${r.retention_warning ? ` ${r.retention_warning}` : ''}`)
      }
      refresh()
    },
    onError: (e: Error) => flash('err', e.message),
  })

  const importAdvices = useMutation({
    mutationFn: (files: File[]) =>
      financeUploadMany<{ imported: number; duplicates: number; failed: number; results: { filename: string; error?: string; tie_ok?: boolean; tie_error?: string }[] }>(
        `/bank-recon/${accountId}/advices`, files),
    onSuccess: (r) => {
      const bad = r.results.filter((x) => x.error || x.tie_ok === false)
      const head = `${r.imported} payment file(s) imported, ${r.duplicates} already there.`
      if (bad.length) {
        flash('err', `${head} ${bad.length} could not be used: ${bad.map((b) => `${b.filename} — ${b.error ?? b.tie_error}`).join(' · ')}`)
      } else {
        flash('ok', head)
      }
      refresh()
    },
    onError: (e: Error) => flash('err', e.message),
  })

  const autoMatch = useMutation({
    mutationFn: () => financeApi.post<AutoResult>(
      `/bank-recon/reconciliations/${reconId}/auto-match`, {}),
    onSuccess: (r) => {
      setFindings(r.findings)
      const rungs = Object.entries(r.by_method).map(([m, n]) => `${METHOD_LABEL[m] ?? m} ${n}`).join(' · ')
      flash('ok', `Cleared ${r.bank_lines_cleared} statement line(s) against ${r.book_lines_cleared} ledger line(s)${rungs ? ` — ${rungs}` : ''}. ${r.summary.outstanding_bank_lines} statement and ${r.summary.outstanding_book_lines} ledger line(s) still open.`)
      clearPicks()
      refresh()
    },
    onError: (e: Error) => flash('err', e.message),
  })

  const matchByHand = useMutation({
    mutationFn: () => financeApi.post(`/bank-recon/reconciliations/${reconId}/matches`, {
      bank_transaction_ids: [...pickedBank], jv_line_ids: [...pickedBook],
    }),
    onSuccess: () => { flash('ok', 'Matched.'); clearPicks(); refresh() },
    onError: (e: Error) => flash('err', e.message),
  })

  const unmatch = useMutation({
    mutationFn: (id: string) =>
      financeApi.delete(`/bank-recon/reconciliations/${reconId}/matches/${id}`),
    onSuccess: () => { setFocusGroup(null); refresh() },
    onError: (e: Error) => flash('err', e.message),
  })

  const finalize = useMutation({
    mutationFn: () => financeApi.post<{ status: string; retention_warning: string | null }>(
      `/bank-recon/reconciliations/${reconId}/finalize`, {}),
    onSuccess: (r) => {
      flash('ok', `Signed off.${r.retention_warning ? ` ${r.retention_warning}` : ''}`)
      refresh()
    },
    onError: (e: Error) => flash('err', e.message),
  })

  const reopen = useMutation({
    mutationFn: () => financeApi.post(`/bank-recon/reconciliations/${reconId}/reopen`, {}),
    onSuccess: () => { flash('ok', 'Reopened.'); refresh() },
    onError: (e: Error) => flash('err', e.message),
  })

  const download = useMutation({
    mutationFn: (fmt: 'pdf' | 'xlsx') => financeDownload(
      `/bank-recon/reconciliations/${reconId}/report?fmt=${fmt}`,
      `bank-reconciliation-${period?.period_end ?? ''}.${fmt}`),
    onError: (e: Error) => flash('err', e.message),
  })

  // Which group a line belongs to — powers the "what is this line made of" highlight.
  const groupOfBank = useMemo(() => {
    const m: Record<string, string> = {}
    for (const g of period?.matches ?? []) for (const id of g.bank_ids) m[id] = g.id
    return m
  }, [period])
  const groupOfBook = useMemo(() => {
    const m: Record<string, string> = {}
    for (const g of period?.matches ?? []) {
      for (const b of g.book_lines) if (b.jv_line_id) m[b.jv_line_id] = g.id
    }
    return m
  }, [period])
  // Candidates are for the case a person is actually in: ONE statement line
  // selected, looking for the ledger lines that make it up. Asking for several at
  // once has no meaning — the ranking is per statement line.
  const soleBankPick = pickedBank.size === 1 ? [...pickedBank][0] : null
  const { data: candidates = [], isFetching: candidatesLoading } = useQuery({
    queryKey: ['bank-recon-candidates', reconId, soleBankPick],
    enabled: !!reconId && !!soleBankPick,
    queryFn: () => financeApi.get<Candidate[]>(
      `/bank-recon/reconciliations/${reconId}/candidates/${soleBankPick}`),
  })

  const focused = period?.matches.find((g) => g.id === focusGroup) ?? null

  // Bring the focused group into view on BOTH sides. A group can span 26 ledger
  // lines in a 260-row table, so "the rows are highlighted" is useless if the
  // reader has to go hunting for them. First row of each side is enough — the
  // rest of the group follows it.
  useEffect(() => {
    if (!focused) return
    const show = (id: string | undefined) => {
      if (!id) return
      document.getElementById(id)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
    show(focused.bank_ids[0] ? `bank-${focused.bank_ids[0]}` : undefined)
    // jv_line_id can be null between a full NC reload and the healer run, and
    // `book-null` matches nothing — skip to the first line that still has one.
    const firstBook = focused.book_lines.find((l) => l.jv_line_id)
    show(firstBook ? `book-${firstBook.jv_line_id}` : undefined)
  }, [focused])

  const pickedBankTotal = useMemo(
    () => (period?.bank_lines ?? []).filter((b) => pickedBank.has(b.id))
      .reduce((s, b) => s + Number(b.amount), 0), [period, pickedBank])
  const pickedBookTotal = useMemo(
    () => (period?.book_lines ?? []).filter((b) => pickedBook.has(b.jv_line_id))
      .reduce((s, b) => s + Number(b.amount), 0), [period, pickedBook])
  const picksBalance = pickedBank.size > 0 && pickedBook.size > 0
    && Math.abs(pickedBankTotal - pickedBookTotal) < 0.005

  if (!user) return <Navigate to="/login" replace />

  const s = period?.summary
  const frozen = period?.status === 'finalized'
  const busy = autoMatch.isPending || matchByHand.isPending || finalize.isPending
    || reopen.isPending || importStatement.isPending || importAdvices.isPending

  const toggle = (set: Set<string>, id: string, apply: (s: Set<string>) => void) => {
    const next = new Set(set)
    next.has(id) ? next.delete(id) : next.add(id)
    apply(next)
  }

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/bank"
      title="Bank Reconciliation"
      subtitle="The statement beside NC's ledger, joined by the bank's own payment files"
    >
      <div className="mx-auto max-w-[1600px]">
        {banner && (
          <div className={cn('mb-3 flex items-start gap-2 rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700')}>
            {banner.kind === 'err' ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />}
            <span>{banner.text}</span>
            <button onClick={() => setBanner(null)} className="ml-auto text-xs underline">dismiss</button>
          </div>
        )}

        {/* ── account + period ─────────────────────────────────────────── */}
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-[11px] uppercase tracking-wide text-neutral-500">Bank account</label>
            <div className="flex items-center gap-2">
              <Landmark className="h-4 w-4 text-neutral-400" />
              <select value={accountId} className={cn(inputCls, 'w-72')}
                      onChange={(e) => { setAccountId(e.target.value); setReconId(''); clearPicks() }}>
                <option value="">Select an account…</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.bank_name} · {a.name} · {a.currency}
                    {a.nc_bank_account_code ? '' : '  (not linked to NC)'}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-[11px] uppercase tracking-wide text-neutral-500">Period</label>
            <div className="flex items-center gap-2">
              <input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)}
                     className={cn(inputCls, 'w-40')} />
              <span className="text-neutral-400">to</span>
              <input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)}
                     className={cn(inputCls, 'w-40')} />
            </div>
          </div>
          <button onClick={() => openPeriod.mutate()}
                  disabled={!accountId || !periodStart || !periodEnd || openPeriod.isPending}
                  className={primaryBtn}>
            {openPeriod.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Open period
          </button>
          <Link to="/finance/bank/csv-import"
                className="ml-auto text-xs text-neutral-500 underline hover:text-neutral-700">
            Import a statement as CSV instead →
          </Link>
        </div>

        {/* ── every period this account has had ────────────────────────── */}
        {/* Without this the only way back to a signed-off July was to already know
            its id. The reconciliations a period produces ARE the audit record —
            each keeps its own frozen snapshot and PDF — so they need a way in. */}
        {accountId && history.length > 0 && (
          <div className="mb-4 rounded-lg border border-neutral-200 bg-white">
            <div className="flex items-center gap-2 border-b border-neutral-100 px-3 py-2">
              <History className="h-4 w-4 text-neutral-400" />
              <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                Periods for this account ({history.length})
              </span>
            </div>
            <div className="max-h-60 overflow-y-auto">
              <table className="w-full text-sm">
                <tbody>
                  {history.map((h, i) => (
                    <tr key={h.id}
                        onClick={() => { setReconId(h.id); setFindings([]); clearPicks() }}
                        className={cn('cursor-pointer border-t border-neutral-100 hover:bg-neutral-50',
                          i % 2 && 'bg-neutral-50/40',
                          h.id === reconId && 'bg-[#E4EFEC]')}>
                      <td className="w-56 px-3 py-2 font-mono text-sm">
                        {h.period_start} → {h.period_end}
                      </td>
                      <td className="w-28 px-3 py-2">
                        {h.status === 'finalized'
                          ? <Pill tone="bg-green-50 text-green-700">signed off</Pill>
                          : <Pill tone="bg-amber-50 text-amber-700">open</Pill>}
                      </td>
                      <td className="w-36 px-3 py-2 text-right font-mono text-sm tabular-nums">
                        {/* The number that decides whether it could be signed off */}
                        <span className={Number(h.difference) === 0 ? 'text-neutral-400' : 'text-red-600'}>
                          {money(h.difference)}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-sm text-neutral-500">
                        {h.finalized_at
                          ? `signed off ${h.finalized_at.slice(0, 10)}`
                          : 'not signed off'}
                      </td>
                      <td className="w-32 px-3 py-2 text-right">
                        {h.has_report && (
                          <span className="text-sm text-neutral-400">report kept</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}


        {account && !account.nc_bank_account_code && (
          <div className="mb-4 flex items-start gap-2 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              This account is not linked to an NC bank account, so it has no ledger
              side to reconcile against. Set its NC bank account code in{' '}
              <Link to="/finance/bank-settings" className="underline">Bank Settings</Link> —
              NC shows it on account 100201, expanded by bank account (e.g. 1033760).
            </span>
          </div>
        )}

        {!reconId ? (
          <div className="rounded-lg border border-dashed border-neutral-300 p-10 text-center text-sm text-neutral-500">
            Pick an account and a period, then Open period.
          </div>
        ) : periodLoading && !period ? (
          <div className="p-10 text-center"><Loader2 className="mx-auto h-6 w-6 animate-spin text-neutral-400" /></div>
        ) : !period ? null : (
          <>
            {/* ── the numbers ───────────────────────────────────────────── */}
            <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-6">
              <Stat label="Statement opening" value={money(s?.statement_opening)} />
              <Stat label="Statement closing" value={money(s?.statement_closing)}
                    hint={s?.statement_verified ? 'verified' : 'not verified'}
                    tone={s?.statement_verified ? undefined : 'warn'} />
              <Stat label="Ledger opening" value={money(s?.book_opening)} />
              <Stat label="Ledger closing" value={money(s?.book_closing)} />
              <Stat label="Cleared"
                    value={`${(s?.cleared_debit_count ?? 0) + (s?.cleared_credit_count ?? 0)} lines`}
                    hint={`${money(s?.cleared_debit_total)} out · ${money(s?.cleared_credit_total)} in`} />
              <Stat label="Difference" value={money(s?.difference)}
                    tone={Number(s?.difference ?? 0) === 0 ? 'pos' : 'neg'}
                    hint={Number(s?.difference ?? 0) === 0 ? 'reconciled' : 'must be 0.00 to sign off'} />
            </div>

            {/* Where the difference came from. The two halves sum to Difference and
                they call for opposite actions: a carry-in gap cannot be closed by
                matching anything inside this period, and saying only "the ledger
                closes at X" left a reader whose lines were all ticked to conclude
                something was unmatched. */}
            {Number(s?.difference ?? 0) !== 0 && (
              <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-1 rounded-lg border
                              border-neutral-200 bg-neutral-50/70 px-3 py-2 text-xs">
                <span className="font-medium text-neutral-500">Where the difference is</span>
                <span className="flex items-center gap-1.5">
                  <span className="text-neutral-500">Carried in (before this period)</span>
                  <span className={cn('font-mono tabular-nums',
                    Number(s?.opening_difference ?? 0) === 0 ? 'text-neutral-400' : 'text-red-600')}>
                    {money(s?.opening_difference)}
                  </span>
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="text-neutral-500">This period&rsquo;s movements</span>
                  <span className={cn('font-mono tabular-nums',
                    Number(s?.movement_difference ?? 0) === 0 ? 'text-neutral-400' : 'text-red-600')}>
                    {money(s?.movement_difference)}
                  </span>
                </span>
                <span className="text-neutral-500">
                  {Number(s?.movement_difference ?? 0) === 0
                    ? 'Matching inside this period cannot change a carried-in gap — check the opening balance or an earlier period.'
                    : Number(s?.opening_difference ?? 0) === 0
                      ? 'The opening balances agree; the gap is in this period, which is what matching is for.'
                      : 'Both halves are off — fix the carry-in first, then match what remains.'}
                </span>
              </div>
            )}

            {/* ── actions ───────────────────────────────────────────────── */}
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {canManage && !frozen && (
                <>
                  <input ref={stmtInput} type="file" accept="application/pdf" className="hidden"
                         onChange={(e) => {
                           const f = e.target.files?.[0]
                           if (f) importStatement.mutate(f)
                           e.target.value = ''
                         }} />
                  <button onClick={() => stmtInput.current?.click()} disabled={busy}
                          className={secondaryBtn}>
                    {importStatement.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Upload className="h-4 w-4" />}
                    Import statement (PDF)
                  </button>

                  <input ref={adviceInput} type="file" accept="application/pdf" multiple className="hidden"
                         onChange={(e) => {
                           const fs = Array.from(e.target.files ?? [])
                           if (fs.length) importAdvices.mutate(fs)
                           e.target.value = ''
                         }} />
                  <button onClick={() => adviceInput.current?.click()} disabled={busy}
                          className={secondaryBtn}>
                    {importAdvices.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <FileText className="h-4 w-4" />}
                    Import payment files
                  </button>

                  <button onClick={() => autoMatch.mutate()} disabled={busy} className={primaryBtn}>
                    {autoMatch.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Sparkles className="h-4 w-4" />}
                    Match
                  </button>
                </>
              )}
              <div className="ml-auto flex items-center gap-2">
                <button onClick={() => download.mutate('pdf')} disabled={download.isPending}
                        className={secondaryBtn}>
                  <Download className="h-4 w-4" /> Report (PDF)
                </button>
                <button onClick={() => download.mutate('xlsx')} disabled={download.isPending}
                        className={secondaryBtn}>
                  <Download className="h-4 w-4" /> Excel
                </button>
                {canManage && (frozen ? (
                  <button onClick={() => reopen.mutate()} disabled={busy} className={secondaryBtn}>
                    {reopen.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <LockOpen className="h-4 w-4" />}
                    Reopen
                  </button>
                ) : (
                  <button onClick={() => finalize.mutate()} disabled={busy} className={primaryBtn}>
                    {finalize.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Lock className="h-4 w-4" />}
                    Sign off
                  </button>
                ))}
              </div>
            </div>

            {frozen && (
              <div className="mb-3 flex items-center gap-2 rounded-md bg-neutral-100 px-3 py-2 text-sm text-neutral-600">
                <Lock className="h-4 w-4" />
                This period is signed off. The report comes from the snapshot taken at
                sign-off, so it keeps saying what it said. Reopen to change anything.
              </div>
            )}

            {findings.length > 0 && (
              <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
                <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-800">
                  <AlertTriangle className="h-3.5 w-3.5" /> Needs a look ({findings.length})
                </div>
                {/* Clickable: a finding names a statement line, and reading it used
                    to leave you to find that line yourself in a 37-row table. If the
                    line is already in a cleared group, focus the group so both sides
                    highlight and the breakdown opens; otherwise just scroll to it. */}
                <ul className="space-y-1 text-sm text-amber-900">
                  {findings.map((f, i) => {
                    const bankId = f.bank_ids[0]
                    const gid = bankId ? groupOfBank[bankId] : undefined
                    const reachable = !!bankId
                    return (
                      <li key={i}>
                        {reachable ? (
                          <button type="button"
                                  onClick={() => {
                                    if (gid) setFocusGroup(gid)
                                    else document.getElementById(`bank-${bankId}`)
                                      ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
                                  }}
                                  className="text-left underline decoration-amber-400 underline-offset-2 hover:decoration-amber-700">
                            · {f.message}
                          </button>
                        ) : <>· {f.message}</>}
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}

            {/* ── the manual-match bar, only while a selection is live ──── */}
            {(pickedBank.size > 0 || pickedBook.size > 0) && !frozen && (
              <div className="sticky top-2 z-10 mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-[#085E5E]/30 bg-[#F2F8F7] px-3 py-2 text-sm">
                <span className="font-medium text-neutral-700">
                  {pickedBank.size} statement · {pickedBook.size} ledger
                </span>
                <span className="font-mono tabular-nums text-neutral-600">
                  {money(pickedBankTotal)} vs {money(pickedBookTotal)}
                </span>
                <span className={cn('font-mono text-xs tabular-nums',
                  picksBalance ? 'text-green-700' : 'text-red-600')}>
                  {picksBalance ? 'balances'
                    : `off by ${money(pickedBankTotal - pickedBookTotal)}`}
                </span>
                <div className="ml-auto flex gap-2">
                  <button onClick={clearPicks} className={secondaryBtn}>Clear</button>
                  <button onClick={() => matchByHand.mutate()}
                          disabled={!picksBalance || matchByHand.isPending || busy}
                          className={primaryBtn}>
                    {matchByHand.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Link2 className="h-4 w-4" />}
                    Match these
                  </button>
                </div>
              </div>
            )}

            {/* ── candidates for the one statement line being cleared ─────── */}
            {/* The ladder deliberately stops before guessing: a PDS batch can be one
                statement line against a dozen ledger lines, and searching that many
                combinations by amount alone finds A set that adds up rather than THE
                set. So it hands the line to a person — and until now handed it over
                with 74 rows and no help. This is the ranking it already computes. */}
            {soleBankPick && !frozen && (
              <div className="mb-3 rounded-lg border border-[#085E5E]/30 bg-white">
                <div className="flex items-center gap-2 border-b border-neutral-100 px-3 py-2 text-xs">
                  <Link2 className="h-4 w-4 text-neutral-400" />
                  <span className="font-semibold uppercase tracking-wide text-neutral-500">
                    Ledger lines that could make up this statement line
                  </span>
                  {candidatesLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-neutral-400" />}
                  <span className="ml-auto text-neutral-400">
                    tick what belongs — the totals above say when it balances
                  </span>
                </div>
                {candidates.length === 0 && !candidatesLoading ? (
                  <div className="px-3 py-4 text-sm text-neutral-400">
                    No unclaimed ledger line within the search window. Widen the period,
                    or import the payment file for this batch and run Match again.
                  </div>
                ) : (
                  <div className="max-h-56 overflow-y-auto">
                    <table className="w-full text-sm">
                      <tbody>
                        {candidates.map((c, i) => (
                          <tr key={c.jv_line_id}
                              onClick={() => toggle(pickedBook, c.jv_line_id, setPickedBook)}
                              className={cn('cursor-pointer border-t border-neutral-100 hover:bg-neutral-50',
                                i % 2 && 'bg-neutral-50/40',
                                pickedBook.has(c.jv_line_id)
                                  && 'bg-[#F2F8F7] ring-1 ring-inset ring-[#085E5E]/30')}>
                            <td className="w-10 px-2 py-1.5">
                              <input type="checkbox" readOnly checked={pickedBook.has(c.jv_line_id)}
                                     className="h-3.5 w-3.5 accent-[#085E5E]" />
                            </td>
                            <td className="w-14 px-2 py-1.5 font-mono text-xs text-neutral-500">
                              {shortDate(c.voucher_date)}
                            </td>
                            <td className={cn('w-28 px-2 py-1.5 text-right font-mono tabular-nums',
                              Number(c.amount) < 0 ? 'text-red-600' : 'text-green-700')}>
                              {money(c.amount)}
                            </td>
                            <td className="px-2 py-1.5">
                              <div className="truncate" title={c.summary ?? ''}>{c.summary || '—'}</div>
                              <div className="font-mono text-[11px] text-neutral-400">{c.jv_number}</div>
                            </td>
                            <td className="w-36 px-2 py-1.5">
                              <Pill tone={KIND_TONE[c.contra_kind]}>
                                {KIND_LABEL_UI[c.contra_kind] ?? c.contra_kind}
                              </Pill>
                            </td>
                            <td className="w-24 px-2 py-1.5 text-right">
                              {/* Named, not just ranked first: "same amount to the cent"
                                  is a different kind of claim from "nearby and plausible". */}
                              {c.exact && (
                                <span className="rounded bg-green-50 px-1.5 py-0.5 text-[11px] text-green-700">
                                  exact amount
                                </span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* ── how to read the two sides ─────────────────────────────── */}
            {/* The same cell is a checkbox before a line is matched and a tick
                after, and nothing on the page said so. Everything here is a fact
                about the UI a first-time reader cannot deduce from the data. */}
            <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg
                            border border-neutral-200 bg-neutral-50/70 px-3 py-2 text-xs text-neutral-600">
              <span className="font-medium text-neutral-500">How to read this</span>
              <span className="flex items-center gap-1">
                <CheckCircle2 className="h-4 w-4 text-green-600" /> matched — click to see its group
              </span>
              <span className="flex items-center gap-1">
                <input type="checkbox" readOnly className="h-3.5 w-3.5 accent-[#085E5E]" />
                not matched — tick both sides, then Match
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-3 w-3 rounded-sm bg-[#BFE3DA] ring-1 ring-[#085E5E]" />
                the group you are looking at
              </span>
              <span className="ml-auto flex items-center gap-2">
                <span className={cn('rounded border px-1.5 py-0.5', TRUST_CHIP.evidence)}>
                  backed by a document
                </span>
                <span className={cn('rounded border px-1.5 py-0.5', TRUST_CHIP.amounts)}>
                  amounts only
                </span>
                <span className={cn('rounded border px-1.5 py-0.5', TRUST_CHIP.manual)}>
                  by a person
                </span>
              </span>
            </div>

            {/* ── the two sides ─────────────────────────────────────────── */}
            <div className="grid gap-3 lg:grid-cols-2">
              <Side title={`Bank statement · ${period.bank_lines.length} lines`}
                    subtitle={s?.bank_account}>
                <table className="w-full text-sm">
                  <thead className="bg-neutral-50 text-left text-[11px] uppercase tracking-wide text-neutral-500">
                    <tr>
                      <th className="w-10 px-2 py-2" />
                      <th className="w-14 px-2 py-2">Date</th>
                      {/* Amount in the same column position as the ledger side: the
                          two tables are read against each other, and an amount that
                          sits in a different place on each is work the reader has to
                          do on every line. */}
                      <th className="w-28 px-2 py-2 text-right">Amount</th>
                      <th className="px-2 py-2">Description</th>
                      <th className="w-28 px-2 py-2 text-right">Balance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {period.bank_lines.length === 0 && (
                      <tr><td colSpan={5} className="px-3 py-8 text-center text-neutral-400">
                        No statement lines yet — import the statement PDF.</td></tr>
                    )}
                    {period.bank_lines.map((b, i) => {
                      const gid = groupOfBank[b.id]
                      const inFocus = !!focusGroup && gid === focusGroup
                      return (
                        <tr key={b.id}
                            onClick={() => (b.cleared ? setFocusGroup(gid === focusGroup ? null : gid)
                              : !frozen && toggle(pickedBank, b.id, setPickedBank))}
                            id={`bank-${b.id}`}
                            className={cn('cursor-pointer border-t border-neutral-100',
                              i % 2 && 'bg-neutral-50/40',
                              // The focused group has to read at a glance across a
                              // 37-row table; the old bg-[#E4EFEC] was too close to
                              // the zebra stripe to find by eye.
                              inFocus && 'bg-[#BFE3DA] ring-1 ring-inset ring-[#085E5E]',
                              pickedBank.has(b.id) && 'bg-[#F2F8F7] ring-1 ring-inset ring-[#085E5E]/30')}>
                          <td className="px-2 py-1.5">
                            {b.cleared
                              ? <CheckCircle2 className="h-4 w-4 text-green-600" />
                              : <input type="checkbox" readOnly checked={pickedBank.has(b.id)}
                                       className="h-3.5 w-3.5 accent-[#085E5E]" />}
                          </td>
                          <td className="px-2 py-1.5 font-mono text-xs text-neutral-500">{shortDate(b.txn_date)}</td>
                          <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums',
                            Number(b.amount) < 0 ? 'text-red-600' : 'text-green-700')}>
                            {money(b.amount)}
                          </td>
                          <td className="px-2 py-1.5">{b.description}</td>
                          <td className="px-2 py-1.5 text-right font-mono text-xs tabular-nums text-neutral-400">
                            {b.running_balance ? money(b.running_balance) : ''}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </Side>

              <Side title={`NC ledger · ${period.book_lines.length} lines`}
                    subtitle="Account 100201, expanded by bank account">
                <table className="w-full text-sm">
                  <thead className="bg-neutral-50 text-left text-[11px] uppercase tracking-wide text-neutral-500">
                    <tr>
                      <th className="w-10 px-2 py-2" />
                      <th className="w-14 px-2 py-2">Date</th>
                      {/* Amount before Summary: Summary is the only elastic column, so
                          it took every spare pixel and pushed Amount past the right
                          edge — the one number you scan a ledger for was behind a
                          horizontal scroll. */}
                      <th className="w-28 px-2 py-2 text-right">Amount</th>
                      <th className="px-2 py-2">Summary</th>
                      <th className="w-36 px-2 py-2">Contra</th>
                    </tr>
                  </thead>
                  <tbody>
                    {period.book_lines.length === 0 && (
                      <tr><td colSpan={5} className="px-3 py-8 text-center text-neutral-400">
                        No ledger lines for this account and period. If NC has them,
                        the bank-account dimension has not been synced yet.</td></tr>
                    )}
                    {period.book_lines.map((b, i) => {
                      const gid = groupOfBook[b.jv_line_id]
                      const inFocus = !!focusGroup && gid === focusGroup
                      return (
                        <tr key={b.jv_line_id}
                            onClick={() => (b.cleared ? setFocusGroup(gid === focusGroup ? null : gid)
                              : !frozen && toggle(pickedBook, b.jv_line_id, setPickedBook))}
                            id={`book-${b.jv_line_id}`}
                            className={cn('cursor-pointer border-t border-neutral-100',
                              i % 2 && 'bg-neutral-50/40',
                              inFocus && 'bg-[#BFE3DA] ring-1 ring-inset ring-[#085E5E]',
                              pickedBook.has(b.jv_line_id) && 'bg-[#F2F8F7] ring-1 ring-inset ring-[#085E5E]/30')}>
                          <td className="px-2 py-1.5">
                            {b.cleared
                              ? <CheckCircle2 className="h-4 w-4 text-green-600" />
                              : <input type="checkbox" readOnly checked={pickedBook.has(b.jv_line_id)}
                                       className="h-3.5 w-3.5 accent-[#085E5E]" />}
                          </td>
                          <td className="px-2 py-1.5 font-mono text-xs text-neutral-500">{shortDate(b.voucher_date)}</td>
                          <td className={cn('px-2 py-1.5 text-right font-mono tabular-nums',
                            Number(b.amount) < 0 ? 'text-red-600' : 'text-green-700')}>
                            {money(b.amount)}
                          </td>
                          <td className="px-2 py-1.5">
                            <div className="truncate" title={b.summary ?? ''}>{b.summary || '—'}</div>
                            <div className="font-mono text-[11px] text-neutral-400">{b.jv_number}</div>
                          </td>
                          <td className="px-2 py-1.5">
                            <Pill tone={KIND_TONE[b.contra_kind]}>{b.contra_label}</Pill>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </Side>
            </div>

            {/* ── what a cleared line is made of ────────────────────────── */}
            {focused && (
              <div className="mt-4 rounded-lg border border-[#085E5E]/30 bg-white p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Pill tone={TRUST_CHIP[METHOD_TRUST[focused.method] ?? 'manual']}>
                    {METHOD_LABEL[focused.method] ?? focused.method}
                  </Pill>
                  <span className="text-xs text-neutral-500">
                    {TRUST_WORD[METHOD_TRUST[focused.method] ?? 'manual']}
                  </span>
                  <span className="font-mono text-sm tabular-nums">{money(focused.amount)}</span>
                  <span className="text-xs text-neutral-500">
                    {focused.bank_ids.length} statement line(s) ↔ {focused.book_lines.length} ledger line(s)
                  </span>
                  {focused.note && <span className="text-xs text-neutral-500">· {focused.note}</span>}
                  <div className="ml-auto flex gap-2">
                    {focused.advice_id && (
                      <button className={secondaryBtn}
                              onClick={() => setOpenAdvice(openAdvice === focused.advice_id ? null : focused.advice_id)}>
                        <FileText className="h-4 w-4" />
                        {openAdvice === focused.advice_id ? 'Hide' : 'Show'} the payment file
                      </button>
                    )}
                    {canManage && !frozen && (
                      <button onClick={() => unmatch.mutate(focused.id)} disabled={unmatch.isPending || busy}
                              className={secondaryBtn}>
                        {unmatch.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                          : <Undo2 className="h-4 w-4" />}
                        Undo this match
                      </button>
                    )}
                  </div>
                </div>
                {openAdvice === focused.advice_id && (
                  <table className="w-full text-sm">
                    <thead className="bg-neutral-50 text-left text-[11px] uppercase tracking-wide text-neutral-500">
                      <tr>
                        <th className="w-10 px-2 py-1.5">#</th>
                        <th className="px-2 py-1.5">Payee</th>
                        <th className="w-32 px-2 py-1.5 text-right">Amount</th>
                        <th className="w-32 px-2 py-1.5">Ledger line</th>
                      </tr>
                    </thead>
                    <tbody>
                      {adviceLines.map((l) => (
                        <tr key={l.id} className="border-t border-neutral-100">
                          <td className="px-2 py-1 font-mono text-xs text-neutral-400">{l.seq}</td>
                          <td className="px-2 py-1">
                            {l.payee_name}
                            {l.payee_code && <span className="ml-2 rounded bg-neutral-100 px-1 font-mono text-[11px] text-neutral-500">{l.payee_code}</span>}
                          </td>
                          <td className="px-2 py-1 text-right font-mono tabular-nums">{money(l.amount)}</td>
                          <td className="px-2 py-1">
                            {l.matched_jv_line_id
                              ? <Pill tone="bg-green-50 text-green-700">matched</Pill>
                              : <Pill tone="bg-amber-50 text-amber-700">not found</Pill>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}

            {/* ── every group, for review ───────────────────────────────── */}
            {period.matches.length > 0 && (
              <div className="mt-4">
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">
                  Cleared groups ({period.matches.length})
                </div>
                {/* A fixed 6-column grid, not flex-wrap: wrapped chips of different
                    widths gave every row a different rhythm, and 37 of them read as
                    noise. Aligned columns make the list scannable. */}
                <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-6">
                  {period.matches.map((g) => (
                    <button key={g.id}
                            onClick={() => setFocusGroup(g.id === focusGroup ? null : g.id)}
                            title={`${g.bank_ids.length} statement line(s) matched against `
                              + `${g.book_lines.length} ledger line(s) — `
                              + `${TRUST_WORD[METHOD_TRUST[g.method] ?? 'manual']}`}
                            className={cn('rounded-lg border px-2 py-1 text-left text-xs',
                              g.id === focusGroup
                                ? 'border-[#085E5E] bg-[#BFE3DA] text-[#085E5E] ring-1 ring-[#085E5E]'
                                : TRUST_CHIP[METHOD_TRUST[g.method] ?? 'manual']
                                  ?? 'border-neutral-200 bg-white text-neutral-600')}>
                      {/* Stacked and left-aligned: in a fixed column the three parts
                          on one line push each other around and nothing lines up
                          between rows. */}
                      <span className="block font-mono tabular-nums">{money(g.amount)}</span>
                      <span className="block truncate">{METHOD_LABEL[g.method] ?? g.method}</span>
                      <span className="block text-[11px] text-neutral-400">
                        {g.bank_ids.length} ↔ {g.book_lines.length} lines
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </PortalChromeLayout>
  )
}

function Side({ title, subtitle, children }: {
  title: string; subtitle?: string; children: React.ReactNode
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
      <div className="border-b border-neutral-200 bg-neutral-50/60 px-3 py-2">
        <div className="text-sm font-semibold text-neutral-700">{title}</div>
        {subtitle && <div className="text-[11px] text-neutral-500">{subtitle}</div>}
      </div>
      <div className="max-h-[30rem] overflow-auto">{children}</div>
    </div>
  )
}
