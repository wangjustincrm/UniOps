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
  Paperclip,
  Lock, LockOpen, Sparkles, Undo2, Upload,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import {
  financeApi, financeDownload, financeUpload, financeUploadMany,
} from '@/lib/api'
import { cn, formatDate, formatDateTime } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

import { Button, Checkbox, ChoiceCard, FileInput, Input, Select, TBody, THead, Table, Td, Th, Tr } from '@uniops/shell'

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
  /** Absent on periods signed off before foreign-currency accounts reconciled in
   *  their own currency — their frozen snapshot never had these. */
  currency?: string; base_only_count?: number; base_only_local_total?: string
}
interface BankLine {
  id: string; txn_date: string; description: string; amount: string
  running_balance: string | null; sort_seq: number | null; cleared: boolean
}
interface BookLine {
  jv_line_id: string; jv_number: string; voucher_date: string; summary: string | null
  amount: string; contra_kind: string; contra_label: string; contra_codes: string[]
  cleared: boolean
  /** false = NC has not tallied the voucher yet; only present when the period
   *  includes unposted vouchers. */
  posted?: boolean
  /** The same line in CAD (NC 本币). Only differs from `amount` on a non-CAD account. */
  local_amount?: string
}
/** A ledger line with no amount in the account's currency — FX revaluation or a
 *  CAD-only adjustment. Shown for the record; it is not a bank movement. */
interface BaseOnlyLine {
  jv_line_id: string; jv_number: string; voucher_date: string; summary: string | null
  currency: string; local_amount: string
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
  base_only_lines?: BaseOnlyLine[]
  /** The period's book basis — stored on the period, so auto-match, matches, the
   *  difference and the sign-off report all read the same ledger. */
  include_unposted: boolean
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
/** Every file this reconciliation stands on — statements, payment files and the
 *  signed-off report. All three were already stored; none were listable. */
interface ReconDoc {
  kind: string; label: string; name: string; detail: string
  imported_at: string | null; storage_key: string | null; superseded: boolean
}
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
  bank_rollup: 'amounts',
  manual: 'manual',
}
const TRUST_CHIP: Record<string, string> = {
  evidence: 'border-success-line bg-success-soft text-success-fg',
  amounts: 'border-warning-line bg-warning-soft text-warning-fg',
  manual: 'border-info-line bg-info-soft text-info-fg',
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
  bank_rollup: 'Many statement lines, one ledger entry',
  manual: 'Matched by hand',
}

const KIND_TONE: Record<string, string> = {
  ap: 'bg-surface-sunken text-fg-secondary',
  bank_transfer: 'bg-info-soft text-info-fg',
  payroll: 'bg-cat4-soft text-cat4-fg',
  credit_card: 'bg-warning-soft text-warning-fg',
  other_payable: 'bg-surface-sunken text-fg-secondary',
  bank_fee: 'bg-surface-sunken text-fg-muted',
  mixed: 'bg-warning-soft text-warning-fg',
}

// The ledger rows get `contra_label` from the server; the candidates endpoint
// returns only the kind, so the same words live here rather than being invented
// per call site. Mirrors bank_book.KIND_LABELS.
const DOC_TONE: Record<string, string> = {
  statement: 'bg-info-soft text-info-fg',
  advice: 'bg-success-soft text-success-fg',
  report: 'bg-accent-soft text-primary-700',
}

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
  return formatDate(iso)
}

function Pill({ children, tone }: { children: React.ReactNode; tone?: string }) {
  return (
    <span className={cn('whitespace-nowrap rounded-full px-2 py-0.5 text-2xs font-medium',
      tone ?? 'bg-surface-sunken text-fg-secondary')}>{children}</span>
  )
}

function Stat({ label, value, tone, hint }: {
  label: string; value: string; tone?: 'pos' | 'neg' | 'warn'; hint?: string
}) {
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2">
      <div className="text-2xs text-fg-muted">{label}</div>
      <div className={cn('font-mono text-sm font-semibold tabular-nums',
        tone === 'neg' && 'text-danger-fg', tone === 'pos' && 'text-success-fg',
        tone === 'warn' && 'text-warning-fg')}>{value}</div>
      {hint && <div className="mt-0.5 text-2xs text-fg-muted">{hint}</div>}
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

  // finance.bank.reconcile in Access Control — not the chart-of-accounts key it used to borrow.
  const { data: perms } = useQuery({
    queryKey: ['bank-permissions'],
    queryFn: () => financeApi.get<{ can_reconcile: boolean; can_manage_settings: boolean }>('/bank/permissions'),
  })
  const canManage = perms?.can_reconcile ?? false

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
      financeUpload<{ verified: boolean; verify_errors: string[]; lines: number; imported: number
                      removed?: number
                      stale_matched?: { date: string; amount: string; description: string }[]
                      retention_warning: string | null }>(
        `/bank-recon/${accountId}/statements`, file),
    onSuccess: (r) => {
      const head = `Statement read: ${r.lines} lines (${r.imported} new${r.removed ? `, ${r.removed} from the previous read removed` : ''}).`
      // A matched line the new read no longer has: kept rather than deleted,
      // because a group hangs off it — so the person has to hear about it.
      const stale = r.stale_matched ?? []
      if (stale.length) {
        flash('err', `${head} ${stale.length} matched line(s) from the previous read are not on this statement any more — unmatch and review: ${stale.map((t) => `${formatDate(t.date)} ${money(t.amount)} ${t.description}`).join('; ')}`)
      } else if (!r.verified) {
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

  const setBasis = useMutation({
    mutationFn: (include_unposted: boolean) => financeApi.put(
      `/bank-recon/reconciliations/${reconId}/basis`, { include_unposted }),
    onSuccess: () => { clearPicks(); refresh() },
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

  // Same helper the report uses: the file server authenticates the caller, so a
  // plain href would 401 for anyone but whoever minted it.
  const downloadDoc = (d: ReconDoc) => {
    if (!d.storage_key) return
    financeDownload(`/bank-recon/documents/${d.storage_key}`, d.name)
      .catch((e: Error) => flash('err', e.message))
  }

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

  const [showDocs, setShowDocs] = useState(false)
  const { data: docs = [] } = useQuery({
    queryKey: ['bank-recon-docs', reconId],
    enabled: !!reconId && showDocs,
    queryFn: () => financeApi.get<ReconDoc[]>(
      `/bank-recon/reconciliations/${reconId}/documents`),
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
  // A non-CAD account reconciles in its own currency (NC 原币); the CAD figures
  // are secondary. null on a CAD account, where the two are the same number.
  const periodCcy = (s?.currency ?? period?.currency ?? 'CAD').toUpperCase()
  const foreignCcy = periodCcy !== 'CAD' ? periodCcy : null
  const baseOnly = period?.base_only_lines ?? []
  const busy = autoMatch.isPending || matchByHand.isPending || finalize.isPending
    || reopen.isPending || importStatement.isPending || importAdvices.isPending
    || setBasis.isPending

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
            banner.kind === 'err' ? 'bg-danger-soft text-danger-fg' : 'bg-success-soft text-success-fg')}>
            {banner.kind === 'err' ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />}
            <span>{banner.text}</span>
            <Button variant="link" onClick={() => setBanner(null)} className="ml-auto text-xs">dismiss</Button>
          </div>
        )}

        {/* ── account + period ─────────────────────────────────────────── */}
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-2xs text-fg-muted">Bank account</label>
            <div className="flex items-center gap-2">
              <Landmark className="h-4 w-4 text-fg-muted" />
              <Select value={accountId} className="w-72"
                      onChange={(e) => { setAccountId(e.target.value); setReconId(''); clearPicks() }}>
                <option value="">Select an account…</option>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.bank_name} · {a.name} · {a.currency}
                    {a.nc_bank_account_code ? '' : '  (not linked to NC)'}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-2xs text-fg-muted">Period</label>
            <div className="flex items-center gap-2">
              <Input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)}
                     className="w-40" />
              <span className="text-fg-muted">to</span>
              <Input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)}
                     className="w-40" />
            </div>
          </div>
          <Button size="md" onClick={() => openPeriod.mutate()}
                  disabled={!accountId || !periodStart || !periodEnd || openPeriod.isPending}
                  >
            {openPeriod.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Open period
          </Button>
          <Link to="/finance/bank/csv-import"
                className="ml-auto text-xs text-fg-muted underline hover:text-fg-secondary">
            Import a statement as CSV instead →
          </Link>
        </div>

        {/* ── every period this account has had ────────────────────────── */}
        {/* Without this the only way back to a signed-off July was to already know
            its id. The reconciliations a period produces ARE the audit record —
            each keeps its own frozen snapshot and PDF — so they need a way in. */}
        {accountId && history.length > 0 && (
          <div className="mb-4 rounded-lg border border-line bg-surface">
            <div className="flex items-center gap-2 border-b border-line-subtle px-3 py-2">
              <History className="h-4 w-4 text-fg-muted" />
              <span className="text-xs font-semibold text-fg-muted">
                Periods for this account ({history.length})
              </span>
            </div>
            <div className="max-h-60 overflow-y-auto">
              <Table density="compact">
                <TBody>
                  {history.map((h, i) => (
                    <Tr key={h.id}
                        onClick={() => { setReconId(h.id); setFindings([]); clearPicks() }}
                        className={cn('cursor-pointer border-t border-line-subtle hover:bg-surface-subtle',
                          i % 2 && 'bg-surface-subtle/40',
                          h.id === reconId && 'bg-accent-soft')}>
                      <Td className="w-56 font-mono text-sm">
                        {formatDate(h.period_start)} → {formatDate(h.period_end)}
                      </Td>
                      <Td className="w-28">
                        {h.status === 'finalized'
                          ? <Pill tone="bg-success-soft text-success-fg">signed off</Pill>
                          : <Pill tone="bg-warning-soft text-warning-fg">open</Pill>}
                      </Td>
                      <Td align="right" className="w-36 font-mono text-sm tabular-nums">
                        {/* The number that decides whether it could be signed off */}
                        <span className={Number(h.difference) === 0 ? 'text-fg-muted' : 'text-danger-fg'}>
                          {money(h.difference)}
                        </span>
                      </Td>
                      <Td className="text-sm text-fg-muted">
                        {h.finalized_at
                          ? `signed off ${formatDate(h.finalized_at)}`
                          : 'not signed off'}
                      </Td>
                      <Td align="right" className="w-32 ">
                        {h.has_report && (
                          <span className="text-sm text-fg-muted">report kept</span>
                        )}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </div>
          </div>
        )}


        {account && !account.nc_bank_account_code && (
          <div className="mb-4 flex items-start gap-2 rounded-md bg-warning-soft px-3 py-2 text-sm text-warning-fg">
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
          <div className="rounded-lg border border-dashed border-line-strong p-10 text-center text-sm text-fg-muted">
            Pick an account and a period, then Open period.
          </div>
        ) : periodLoading && !period ? (
          <div className="p-10 text-center"><Loader2 className="mx-auto h-6 w-6 animate-spin text-fg-muted" /></div>
        ) : !period ? null : (
          <>
            {/* ── the numbers ───────────────────────────────────────────── */}
            <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-6">
              <Stat label="Statement opening" value={money(s?.statement_opening)} />
              <Stat label="Statement closing" value={money(s?.statement_closing)}
                    hint={s?.statement_verified ? 'verified' : 'not verified'}
                    tone={s?.statement_verified ? undefined : 'warn'} />
              <Stat label="Ledger opening" value={money(s?.book_opening)}
                    hint={foreignCcy ? `${foreignCcy} · NC original currency` : undefined} />
              <Stat label="Ledger closing" value={money(s?.book_closing)}
                    hint={foreignCcy ? `${foreignCcy} · NC original currency` : undefined} />
              <Stat label="Cleared"
                    value={`${(s?.cleared_debit_count ?? 0) + (s?.cleared_credit_count ?? 0)} lines`}
                    hint={`${money(s?.cleared_debit_total)} out · ${money(s?.cleared_credit_total)} in`} />
              {/* 0.00 against a statement that does not tie to its own totals is
                  not "reconciled" — ICBC July read the USD opening and the CAD
                  closing and still landed on 0.00 here. Sign off refuses it; the
                  card must not say the opposite. */}
              <Stat label="Difference" value={money(s?.difference)}
                    tone={!s?.statement_verified ? 'warn' : Number(s?.difference ?? 0) === 0 ? 'pos' : 'neg'}
                    hint={s?.statement_closing == null ? 'no statement imported yet'
                      : !s?.statement_verified ? 'statement not verified — re-import it'
                      : Number(s?.difference ?? 0) === 0 ? 'reconciled' : 'must be 0.00 to sign off'} />
            </div>

            {/* Where the difference came from. The two halves sum to Difference and
                they call for opposite actions: a carry-in gap cannot be closed by
                matching anything inside this period, and saying only "the ledger
                closes at X" left a reader whose lines were all ticked to conclude
                something was unmatched. */}
            {Number(s?.difference ?? 0) !== 0 && (
              <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-1 rounded-lg border
                              border-line bg-surface-subtle/70 px-3 py-2 text-xs">
                <span className="font-medium text-fg-muted">Where the difference is</span>
                <span className="flex items-center gap-1.5">
                  <span className="text-fg-muted">Carried in (before this period)</span>
                  <span className={cn('font-mono tabular-nums',
                    Number(s?.opening_difference ?? 0) === 0 ? 'text-fg-muted' : 'text-danger-fg')}>
                    {money(s?.opening_difference)}
                  </span>
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="text-fg-muted">This period&rsquo;s movements</span>
                  <span className={cn('font-mono tabular-nums',
                    Number(s?.movement_difference ?? 0) === 0 ? 'text-fg-muted' : 'text-danger-fg')}>
                    {money(s?.movement_difference)}
                  </span>
                </span>
                <span className="text-fg-muted">
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
              {/* Ticked by default, like the Budget and Account Balance pages. The
                  box is the period's own basis (saved on the period), not a view
                  filter: the difference and the signed report follow it too. */}
              <label className={cn('flex items-center gap-1.5 text-sm text-fg-secondary',
                                   (!canManage || frozen) && 'opacity-60')}
                     title="Count vouchers NC has not tallied yet on the ledger side. Saved on this period; the sign-off report states it.">
                <Checkbox checked={period.include_unposted}
                       disabled={!canManage || frozen || setBasis.isPending}
                       onChange={(e) => setBasis.mutate(e.target.checked)} />
                Include unposted vouchers
                {setBasis.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              </label>
              {canManage && !frozen && (
                <>
                  <FileInput ref={stmtInput} accept="application/pdf" className="hidden"
                         onChange={(e) => {
                           const f = e.target.files?.[0]
                           if (f) importStatement.mutate(f)
                           e.target.value = ''
                         }} />
                  <Button variant="secondary" size="md" onClick={() => stmtInput.current?.click()} disabled={busy}
                          >
                    {importStatement.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Upload className="h-4 w-4" />}
                    Import statement (PDF)
                  </Button>

                  <FileInput ref={adviceInput} accept="application/pdf" multiple className="hidden"
                         onChange={(e) => {
                           const fs = Array.from(e.target.files ?? [])
                           if (fs.length) importAdvices.mutate(fs)
                           e.target.value = ''
                         }} />
                  <Button variant="secondary" size="md" onClick={() => adviceInput.current?.click()} disabled={busy}
                          >
                    {importAdvices.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <FileText className="h-4 w-4" />}
                    Import payment files
                  </Button>

                  {/* "Auto-match", not "Match": the legend below tells people to tick
                      both sides and then match, and with this button also called
                      Match they ticked two lines and never ran the ladder — BOC July
                      sat at 1 manual group while 29 same-amount pairs waited. */}
                  <Button size="md" onClick={() => autoMatch.mutate()} disabled={busy} 
                          title="Match everything the rules can prove: payment files, confirmation numbers, and one statement line to one ledger line of the same amount within 5 days">
                    {autoMatch.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Sparkles className="h-4 w-4" />}
                    Auto-match
                  </Button>
                </>
              )}
              <div className="ml-auto flex items-center gap-2">
                <Button variant="secondary" size="md" onClick={() => setShowDocs((v) => !v)}
                        className={cn(showDocs && 'border-primary-700 text-primary-700')}>
                  <Paperclip className="h-4 w-4" />
                  {showDocs ? 'Hide' : 'Attachments'}
                </Button>
                <Button variant="secondary" size="md" onClick={() => download.mutate('pdf')} disabled={download.isPending}
                        >
                  <Download className="h-4 w-4" /> Report (PDF)
                </Button>
                <Button variant="secondary" size="md" onClick={() => download.mutate('xlsx')} disabled={download.isPending}
                        >
                  <Download className="h-4 w-4" /> Excel
                </Button>
                {canManage && (frozen ? (
                  <Button variant="secondary" size="md" onClick={() => reopen.mutate()} disabled={busy}>
                    {reopen.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <LockOpen className="h-4 w-4" />}
                    Reopen
                  </Button>
                ) : (
                  <Button size="md" onClick={() => finalize.mutate()} disabled={busy}>
                    {finalize.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Lock className="h-4 w-4" />}
                    Sign off
                  </Button>
                ))}
              </div>
            </div>

            {frozen && (
              <div className="mb-3 flex items-center gap-2 rounded-md bg-surface-sunken px-3 py-2 text-sm text-fg-secondary">
                <Lock className="h-4 w-4" />
                This period is signed off. The report comes from the snapshot taken at
                sign-off, so it keeps saying what it said. Reopen to change anything.
              </div>
            )}

            {findings.length > 0 && (
              <div className="mb-3 rounded-lg border border-warning-line bg-warning-soft px-3 py-2">
                <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-warning-fg">
                  <AlertTriangle className="h-3.5 w-3.5" /> Needs a look ({findings.length})
                </div>
                {/* Clickable: a finding names a statement line, and reading it used
                    to leave you to find that line yourself in a 37-row table. If the
                    line is already in a cleared group, focus the group so both sides
                    highlight and the breakdown opens; otherwise just scroll to it. */}
                <ul className="space-y-1 text-sm text-warning-fg">
                  {findings.map((f, i) => {
                    const bankId = f.bank_ids[0]
                    const gid = bankId ? groupOfBank[bankId] : undefined
                    const reachable = !!bankId
                    return (
                      <li key={i}>
                        {reachable ? (
                          <Button variant="link" type="button"
                                  onClick={() => {
                                    if (gid) setFocusGroup(gid)
                                    else document.getElementById(`bank-${bankId}`)
                                      ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
                                  }}
                                  className="text-left">
                            · {f.message}
                          </Button>
                        ) : <>· {f.message}</>}
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}

            {/* ── the manual-match bar, only while a selection is live ──── */}
            {(pickedBank.size > 0 || pickedBook.size > 0) && !frozen && (
              <div className="sticky top-2 z-10 mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-primary-700/30 bg-accent-soft/60 px-3 py-2 text-sm">
                <span className="font-medium text-fg-secondary">
                  {pickedBank.size} statement · {pickedBook.size} ledger
                </span>
                <span className="font-mono tabular-nums text-fg-secondary">
                  {money(pickedBankTotal)} vs {money(pickedBookTotal)}
                </span>
                <span className={cn('font-mono text-xs tabular-nums',
                  picksBalance ? 'text-success-fg' : 'text-danger-fg')}>
                  {picksBalance ? 'balances'
                    : `off by ${money(pickedBankTotal - pickedBookTotal)}`}
                </span>
                <div className="ml-auto flex gap-2">
                  <Button variant="secondary" size="md" onClick={clearPicks}>Clear</Button>
                  <Button size="md" onClick={() => matchByHand.mutate()}
                          disabled={!picksBalance || matchByHand.isPending || busy}
                          >
                    {matchByHand.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Link2 className="h-4 w-4" />}
                    Match these
                  </Button>
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
              <div className="mb-3 rounded-lg border border-primary-700/30 bg-surface">
                <div className="flex items-center gap-2 border-b border-line-subtle px-3 py-2 text-xs">
                  <Link2 className="h-4 w-4 text-fg-muted" />
                  <span className="font-semibold text-fg-muted">
                    Ledger lines that could make up this statement line
                  </span>
                  {candidatesLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-fg-muted" />}
                  <span className="ml-auto text-fg-muted">
                    tick what belongs — the totals above say when it balances
                  </span>
                </div>
                {candidates.length === 0 && !candidatesLoading ? (
                  <div className="px-3 py-4 text-sm text-fg-muted">
                    No unclaimed ledger line within the search window. Widen the period,
                    or import the payment file for this batch and run Match again.
                  </div>
                ) : (
                  <div className="max-h-56 overflow-y-auto">
                    <Table density="compact">
                      <TBody>
                        {candidates.map((c, i) => (
                          <Tr key={c.jv_line_id}
                              onClick={() => toggle(pickedBook, c.jv_line_id, setPickedBook)}
                              className={cn('cursor-pointer border-t border-line-subtle hover:bg-surface-subtle',
                                i % 2 && 'bg-surface-subtle/40',
                                pickedBook.has(c.jv_line_id)
                                  && 'bg-accent-soft/60 ring-1 ring-inset ring-primary-700/30')}>
                            <Td className="w-10">
                              <Checkbox readOnly checked={pickedBook.has(c.jv_line_id)}
                                     className="h-3.5 w-3.5 accent-[#085E5E]" />
                            </Td>
                            <Td className="w-24 whitespace-nowrap font-mono text-xs text-fg-muted">
                              {shortDate(c.voucher_date)}
                            </Td>
                            <Td className={cn('w-28 px-2 py-1.5 text-right font-mono tabular-nums',
                              Number(c.amount) < 0 ? 'text-danger-fg' : 'text-success-fg')}>
                              {money(c.amount)}
                            </Td>
                            <Td>
                              <div className="truncate" title={c.summary ?? ''}>{c.summary || '—'}</div>
                              <div className="font-mono text-2xs text-fg-muted">{c.jv_number}</div>
                            </Td>
                            <Td className="w-36">
                              <Pill tone={KIND_TONE[c.contra_kind]}>
                                {KIND_LABEL_UI[c.contra_kind] ?? c.contra_kind}
                              </Pill>
                            </Td>
                            <Td align="right" className="w-24 ">
                              {/* Named, not just ranked first: "same amount to the cent"
                                  is a different kind of claim from "nearby and plausible". */}
                              {c.exact && (
                                <span className="rounded bg-success-soft px-1.5 py-0.5 text-2xs text-success-fg">
                                  exact amount
                                </span>
                              )}
                            </Td>
                          </Tr>
                        ))}
                      </TBody>
                    </Table>
                  </div>
                )}
              </div>
            )}

            {/* ── every document behind this reconciliation ───────────────── */}
            {showDocs && (
              <div className="mb-3 rounded-lg border border-line bg-surface">
                <div className="flex items-center gap-2 border-b border-line-subtle px-3 py-2">
                  <Paperclip className="h-4 w-4 text-fg-muted" />
                  <span className="text-xs font-semibold text-fg-muted">
                    Documents for this period ({docs.length})
                  </span>
                </div>
                {docs.length === 0 ? (
                  <div className="px-3 py-4 text-sm text-fg-muted">
                    Nothing stored for this period yet — statements and payment files
                    appear here as they are imported, and the report once it is signed off.
                  </div>
                ) : (
                  <Table density="compact">
                    <TBody>
                      {docs.map((d, i) => (
                        <Tr key={`${d.kind}-${d.storage_key ?? i}`}
                            className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40',
                              d.superseded && 'opacity-50')}>
                          <Td className="w-40">
                            <Pill tone={DOC_TONE[d.kind] ?? 'bg-surface-sunken text-fg-secondary'}>
                              {d.label}
                            </Pill>
                          </Td>
                          <Td>
                            <div className="truncate" title={d.name}>{d.name}</div>
                            <div className="text-xs text-fg-muted">{d.detail}</div>
                          </Td>
                          <Td className="w-40 text-xs text-fg-muted">
                            {d.imported_at ? formatDateTime(d.imported_at) : ''}
                            {/* A superseded statement is kept on purpose — a re-import
                                replaces what is reconciled against, not the record of
                                what was once uploaded. */}
                            {d.superseded && <span className="ml-2">superseded</span>}
                          </Td>
                          <Td align="right" className="w-28 ">
                            {d.storage_key ? (
                              <Button variant="link" className="text-xs"
                                      onClick={() => downloadDoc(d)}>
                                Download
                              </Button>
                            ) : (
                              <span className="text-xs text-fg-muted" title="Imported before file retention was in place, or the file server rejected it">
                                not stored
                              </span>
                            )}
                          </Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                )}
              </div>
            )}

            {/* ── how to read the two sides ─────────────────────────────── */}
            {/* The same cell is a checkbox before a line is matched and a tick
                after, and nothing on the page said so. Everything here is a fact
                about the UI a first-time reader cannot deduce from the data. */}
            <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg
                            border border-line bg-surface-subtle/70 px-3 py-2 text-xs text-fg-secondary">
              <span className="font-medium text-fg-muted">How to read this</span>
              <span className="flex items-center gap-1">
                <CheckCircle2 className="h-4 w-4 text-success-fg" /> matched — click to see its group
              </span>
              <span className="flex items-center gap-1">
                <Checkbox readOnly className="h-3.5 w-3.5 accent-[#085E5E]" />
                not matched — tick both sides, then Match these
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-3 w-3 rounded-sm bg-primary-100 ring-1 ring-primary-700" />
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
                <Table density="compact">
                  <THead>
                    <Tr>
                      <Th className="w-10" />
                      <Th className="w-24">Date</Th>
                      {/* Amount in the same column position as the ledger side: the
                          two tables are read against each other, and an amount that
                          sits in a different place on each is work the reader has to
                          do on every line. */}
                      <Th align="right" className="w-28 ">Amount</Th>
                      <Th>Description</Th>
                      <Th align="right" className="w-28 ">Balance</Th>
                    </Tr>
                  </THead>
                  <TBody>
                    {period.bank_lines.length === 0 && (
                      <Tr><Td align="center" colSpan={5} className="text-fg-muted">
                        No statement lines yet — import the statement PDF.</Td></Tr>
                    )}
                    {period.bank_lines.map((b, i) => {
                      const gid = groupOfBank[b.id]
                      const inFocus = !!focusGroup && gid === focusGroup
                      return (
                        <Tr key={b.id}
                            onClick={() => (b.cleared ? setFocusGroup(gid === focusGroup ? null : gid)
                              : !frozen && toggle(pickedBank, b.id, setPickedBank))}
                            id={`bank-${b.id}`}
                            className={cn('cursor-pointer border-t border-line-subtle',
                              i % 2 && 'bg-surface-subtle/40',
                              // The focused group has to read at a glance across a
                              // 37-row table; the old bg-accent-soft was too close to
                              // the zebra stripe to find by eye.
                              inFocus && 'bg-primary-100 ring-1 ring-inset ring-primary-700',
                              pickedBank.has(b.id) && 'bg-accent-soft/60 ring-1 ring-inset ring-primary-700/30')}>
                          <Td>
                            {b.cleared
                              ? <CheckCircle2 className="h-4 w-4 text-success-fg" />
                              : <Checkbox readOnly checked={pickedBank.has(b.id)}
                                       className="h-3.5 w-3.5 accent-[#085E5E]" />}
                          </Td>
                          <Td className="whitespace-nowrap font-mono text-xs text-fg-muted">{shortDate(b.txn_date)}</Td>
                          <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums',
                            Number(b.amount) < 0 ? 'text-danger-fg' : 'text-success-fg')}>
                            {money(b.amount)}
                          </Td>
                          <Td>{b.description}</Td>
                          <Td align="right" className="font-mono text-xs tabular-nums text-fg-muted">
                            {b.running_balance ? money(b.running_balance) : ''}
                          </Td>
                        </Tr>
                      )
                    })}
                  </TBody>
                </Table>
              </Side>

              <Side title={`NC ledger · ${period.book_lines.length} lines`}
                    subtitle={foreignCcy
                      ? `Account 100201, expanded by bank account · amounts in ${foreignCcy} (NC original currency), CAD below`
                      : 'Account 100201, expanded by bank account'}>
                <Table density="compact">
                  <THead>
                    <Tr>
                      <Th className="w-10" />
                      <Th className="w-24">Date</Th>
                      {/* Amount before Summary: Summary is the only elastic column, so
                          it took every spare pixel and pushed Amount past the right
                          edge — the one number you scan a ledger for was behind a
                          horizontal scroll. */}
                      <Th align="right" className="w-28 ">Amount</Th>
                      <Th>Summary</Th>
                      <Th className="w-36">Contra</Th>
                    </Tr>
                  </THead>
                  <TBody>
                    {period.book_lines.length === 0 && (
                      <Tr><Td align="center" colSpan={5} className="text-fg-muted">
                        No ledger lines for this account and period. If NC has them,
                        the bank-account dimension has not been synced yet.</Td></Tr>
                    )}
                    {period.book_lines.map((b, i) => {
                      const gid = groupOfBook[b.jv_line_id]
                      const inFocus = !!focusGroup && gid === focusGroup
                      return (
                        <Tr key={b.jv_line_id}
                            onClick={() => (b.cleared ? setFocusGroup(gid === focusGroup ? null : gid)
                              : !frozen && toggle(pickedBook, b.jv_line_id, setPickedBook))}
                            id={`book-${b.jv_line_id}`}
                            className={cn('cursor-pointer border-t border-line-subtle',
                              i % 2 && 'bg-surface-subtle/40',
                              inFocus && 'bg-primary-100 ring-1 ring-inset ring-primary-700',
                              pickedBook.has(b.jv_line_id) && 'bg-accent-soft/60 ring-1 ring-inset ring-primary-700/30')}>
                          <Td>
                            {b.cleared
                              ? <CheckCircle2 className="h-4 w-4 text-success-fg" />
                              : <Checkbox readOnly checked={pickedBook.has(b.jv_line_id)}
                                       className="h-3.5 w-3.5 accent-[#085E5E]" />}
                          </Td>
                          <Td className="whitespace-nowrap font-mono text-xs text-fg-muted">{shortDate(b.voucher_date)}</Td>
                          <Td className={cn('px-2 py-1.5 text-right font-mono tabular-nums',
                            Number(b.amount) < 0 ? 'text-danger-fg' : 'text-success-fg')}>
                            {money(b.amount)}
                            {foreignCcy && b.local_amount != null && (
                              <div className="text-2xs text-fg-muted">CAD {money(b.local_amount)}</div>
                            )}
                          </Td>
                          <Td>
                            <div className="truncate" title={b.summary ?? ''}>{b.summary || '—'}</div>
                            <div className="font-mono text-2xs text-fg-muted">
                              {b.jv_number}
                              {b.posted === false && (
                                <span className="ml-1.5 rounded bg-warning-soft px-1.5 py-0.5 font-sans text-2xs font-medium text-warning-fg">
                                  Unposted
                                </span>
                              )}
                            </div>
                          </Td>
                          <Td>
                            <Pill tone={KIND_TONE[b.contra_kind]}>{b.contra_label}</Pill>
                          </Td>
                        </Tr>
                      )
                    })}
                    {/* Not selectable on purpose: they have no amount in the account's
                        currency, so there is nothing on the statement they could ever
                        match. Listed so the CAD 科目余额表 figure can still be traced. */}
                    {baseOnly.length > 0 && (
                      <Tr className="bg-surface-subtle">
                        <Td colSpan={5} className="text-2xs text-fg-muted">
                          Not on the statement · {baseOnly.length} line{baseOnly.length > 1 ? 's' : ''} with
                          no {foreignCcy ?? period.currency} amount (FX revaluation / CAD-only),
                          CAD {money(s?.base_only_local_total ??
                            String(baseOnly.reduce((t, b) => t + Number(b.local_amount), 0)))} — excluded
                          from the ledger balance
                        </Td>
                      </Tr>
                    )}
                    {baseOnly.map((b) => (
                      <Tr key={b.jv_line_id} className="text-fg-muted">
                        <Td />
                        <Td className="whitespace-nowrap font-mono text-xs">{shortDate(b.voucher_date)}</Td>
                        <Td align="right" className="font-mono text-xs tabular-nums">
                          <div>—</div>
                          <div className="text-2xs">CAD {money(b.local_amount)}</div>
                        </Td>
                        <Td>
                          <div className="truncate" title={b.summary ?? ''}>{b.summary || '—'}</div>
                          <div className="font-mono text-2xs">{b.jv_number}</div>
                        </Td>
                        <Td />
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </Side>
            </div>

            {/* ── what a cleared line is made of ────────────────────────── */}
            {focused && (
              <div className="mt-4 rounded-lg border border-primary-700/30 bg-surface p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Pill tone={TRUST_CHIP[METHOD_TRUST[focused.method] ?? 'manual']}>
                    {METHOD_LABEL[focused.method] ?? focused.method}
                  </Pill>
                  <span className="text-xs text-fg-muted">
                    {TRUST_WORD[METHOD_TRUST[focused.method] ?? 'manual']}
                  </span>
                  <span className="font-mono text-sm tabular-nums">{money(focused.amount)}</span>
                  <span className="text-xs text-fg-muted">
                    {focused.bank_ids.length} statement line(s) ↔ {focused.book_lines.length} ledger line(s)
                  </span>
                  {focused.note && <span className="text-xs text-fg-muted">· {focused.note}</span>}
                  <div className="ml-auto flex gap-2">
                    {focused.advice_id && (
                      <Button variant="secondary" size="md" 
                              onClick={() => setOpenAdvice(openAdvice === focused.advice_id ? null : focused.advice_id)}>
                        <FileText className="h-4 w-4" />
                        {openAdvice === focused.advice_id ? 'Hide' : 'Show'} the payment file
                      </Button>
                    )}
                    {canManage && !frozen && (
                      <Button variant="secondary" size="md" onClick={() => unmatch.mutate(focused.id)} disabled={unmatch.isPending || busy}
                              >
                        {unmatch.isPending ? <Loader2 className="h-4 w-4 animate-spin" />
                          : <Undo2 className="h-4 w-4" />}
                        Undo this match
                      </Button>
                    )}
                  </div>
                </div>
                {openAdvice === focused.advice_id && (
                  <Table density="compact">
                    <THead>
                      <Tr>
                        <Th className="w-10">#</Th>
                        <Th>Payee</Th>
                        <Th align="right" className="w-32 ">Amount</Th>
                        <Th className="w-32">Ledger line</Th>
                      </Tr>
                    </THead>
                    <TBody>
                      {adviceLines.map((l) => (
                        <Tr key={l.id}>
                          <Td className="font-mono text-xs text-fg-muted">{l.seq}</Td>
                          <Td>
                            {l.payee_name}
                            {l.payee_code && <span className="ml-2 rounded bg-surface-sunken px-1 font-mono text-2xs text-fg-muted">{l.payee_code}</span>}
                          </Td>
                          <Td align="right" className="font-mono tabular-nums">{money(l.amount)}</Td>
                          <Td>
                            {l.matched_jv_line_id
                              ? <Pill tone="bg-success-soft text-success-fg">matched</Pill>
                              : <Pill tone="bg-warning-soft text-warning-fg">not found</Pill>}
                          </Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                )}
              </div>
            )}

            {/* ── every group, for review ───────────────────────────────── */}
            {period.matches.length > 0 && (
              <div className="mt-4">
                <div className="mb-1 text-xs font-semibold text-fg-muted">
                  Cleared groups ({period.matches.length})
                </div>
                {/* A fixed 6-column grid, not flex-wrap: wrapped chips of different
                    widths gave every row a different rhythm, and 37 of them read as
                    noise. Aligned columns make the list scannable. */}
                <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-6">
                  {period.matches.map((g) => (
                    <ChoiceCard key={g.id} selected={g.id === focusGroup}
                            onClick={() => setFocusGroup(g.id === focusGroup ? null : g.id)}
                            title={`${g.bank_ids.length} statement line(s) matched against `
                              + `${g.book_lines.length} ledger line(s) — `
                              + `${TRUST_WORD[METHOD_TRUST[g.method] ?? 'manual']}`}
                            className={cn('block rounded-lg px-2 py-1 text-xs',
                              g.id === focusGroup
                                ? 'bg-primary-100 text-accent-fg'
                                : TRUST_CHIP[METHOD_TRUST[g.method] ?? 'manual']
                                  ?? 'border-line bg-surface text-fg-secondary')}>
                      {/* Stacked and left-aligned: in a fixed column the three parts
                          on one line push each other around and nothing lines up
                          between rows. */}
                      <span className="block font-mono tabular-nums">{money(g.amount)}</span>
                      <span className="block truncate">{METHOD_LABEL[g.method] ?? g.method}</span>
                      <span className="block text-2xs text-fg-muted">
                        {g.bank_ids.length} ↔ {g.book_lines.length} lines
                      </span>
                    </ChoiceCard>
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
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="border-b border-line bg-surface-subtle/60 px-3 py-2">
        <div className="text-sm font-semibold text-fg-secondary">{title}</div>
        {subtitle && <div className="text-2xs text-fg-muted">{subtitle}</div>}
      </div>
      <div className="max-h-[30rem] overflow-auto">{children}</div>
    </div>
  )
}
