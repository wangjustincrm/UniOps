/**
 * General Ledger workbench (Phase f UI)
 *
 * Tabs over the posting spine: Trial Balance · Balance Sheet · Income Statement
 * · Journal. Trial-balance rows drill into an account ledger (running balance).
 * Actions: post opening balances (NC65 cut-over) and run the year-end close
 * (sweep P&L → Retained Earnings).
 *
 * Styling follows the Portal convention (CoaConfigPage): neutral palette,
 * zebra rows, #085E5E primary.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BookOpen, CalendarClock, Check, Loader2, Lock, Plus, Scale, Trash2, X,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

interface TBRow {
  account_code: string; account_name: string; account_type: string | null
  opening: string; period_debit: string; period_credit: string; closing: string
}
interface TrialBalance {
  period: string; rows: TBRow[]
  totals: { opening: string; period_debit: string; period_credit: string; closing: string }
  balanced: boolean
}
interface StatementRow { account_code: string; account_name: string; amount: string }
interface BalanceSheet {
  assets: StatementRow[]; liabilities: StatementRow[]; equity: StatementRow[]
  assets_total: string; liabilities_total: string; equity_total: string
  liabilities_and_equity_total: string; balanced: boolean
}
interface IncomeStatement {
  revenue: StatementRow[]; expense: StatementRow[]
  revenue_total: string; expense_total: string; net_income: string; basis: string
}
interface Account { code: string; name: string; is_postable: boolean }

type Tab = 'tb' | 'bs' | 'is' | 'journal'

function num(v: string) { return Number(v) }
function money(v: string) {
  return num(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
/** debit-positive signed value → debit column (or blank) */
function drCol(signed: string) { return num(signed) > 0 ? money(signed) : '' }
function crCol(signed: string) { return num(signed) < 0 ? money(String(-num(signed))) : '' }

function thisMonth() { return new Date().toISOString().slice(0, 7) }

export default function GeneralLedgerPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('tb')
  const [period, setPeriod] = useState(thisMonth())
  const [ytd, setYtd] = useState(true)
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [ledgerCode, setLedgerCode] = useState<string | null>(null)
  const [showOpening, setShowOpening] = useState(false)
  const [showClose, setShowClose] = useState(false)

  const { data: perms } = useQuery({
    queryKey: ['coa-permissions'],
    queryFn: () => financeApi.get<{ can_manage: boolean }>('/coa/permissions'),
  })
  const canManage = perms?.can_manage ?? false

  const tb = useQuery({
    queryKey: ['gl-tb', period],
    queryFn: () => financeApi.get<TrialBalance>(`/gl/trial-balance?period=${period}`),
    enabled: tab === 'tb',
  })
  const bs = useQuery({
    queryKey: ['gl-bs', period],
    queryFn: () => financeApi.get<BalanceSheet>(`/gl/balance-sheet?period=${period}`),
    enabled: tab === 'bs',
  })
  const is = useQuery({
    queryKey: ['gl-is', period, ytd],
    queryFn: () => financeApi.get<IncomeStatement>(`/gl/income-statement?period=${period}&ytd=${ytd}`),
    enabled: tab === 'is',
  })
  const journal = useQuery({
    queryKey: ['gl-journal', period],
    queryFn: () => financeApi.get<any[]>(`/gl/journal?period=${period}`),
    enabled: tab === 'journal',
  })

  const flash = (kind: 'ok' | 'err', text: string) => { setBanner({ kind, text }); setTimeout(() => setBanner(null), 6000) }
  const refreshAll = () => qc.invalidateQueries({ queryKey: ['gl-tb'] })

  if (!user) return <Navigate to="/login" replace />

  const TABS: [Tab, string][] = [
    ['tb', 'Trial Balance'], ['bs', 'Balance Sheet'], ['is', 'Income Statement'], ['journal', 'Journal'],
  ]

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/gl"
      title="General Ledger"
      subtitle="Trial balance, financial statements, and period close over the posting spine"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {TABS.map(([t, label]) => (
            <button key={t} onClick={() => setTab(t)}
                    className={cn('rounded-lg px-4 py-2 text-sm font-medium',
                      tab === t ? 'bg-[#085E5E] text-white'
                                : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50')}>
              {label}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2">
            <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)}
                   className={cn(inputCls, 'w-40')} />
            {canManage && (
              <>
                <button onClick={() => setShowOpening(true)} className={secondaryBtn}>
                  <Plus className="h-4 w-4" /> Opening
                </button>
                <button onClick={() => setShowClose(true)} className={secondaryBtn}>
                  <Lock className="h-4 w-4" /> Year-End Close
                </button>
              </>
            )}
          </div>
        </div>

        {banner && (
          <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700')}>
            {banner.text}
          </div>
        )}

        {tab === 'tb' && <TrialBalanceTab q={tb} onDrill={setLedgerCode} />}
        {tab === 'bs' && <BalanceSheetTab q={bs} />}
        {tab === 'is' && <IncomeStatementTab q={is} ytd={ytd} setYtd={setYtd} />}
        {tab === 'journal' && <JournalTab q={journal} />}
      </div>

      {ledgerCode && (
        <AccountLedgerModal code={ledgerCode} period={period} onClose={() => setLedgerCode(null)} />
      )}
      {showOpening && (
        <OpeningModal onClose={() => setShowOpening(false)}
          onSaved={() => { setShowOpening(false); refreshAll(); flash('ok', 'Opening balance posted') }}
          onError={(m) => flash('err', m)} />
      )}
      {showClose && (
        <CloseYearModal onClose={() => setShowClose(false)}
          onDone={(net) => { setShowClose(false); refreshAll(); flash('ok', `Year closed — net income ${money(net)} swept to Retained Earnings`) }}
          onError={(m) => flash('err', m)} />
      )}
    </PortalChromeLayout>
  )
}

function Balanced({ ok }: { ok: boolean }) {
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
      ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700')}>
      <Scale className="h-3 w-3" /> {ok ? 'Balanced' : 'Out of balance'}
    </span>
  )
}

function Loading() {
  return <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
}

function TrialBalanceTab({ q, onDrill }: { q: any; onDrill: (c: string) => void }) {
  if (q.isFetching && !q.data) return <Loading />
  const tb: TrialBalance | undefined = q.data
  if (!tb) return null
  return (
    <>
      <div className="mb-2 flex justify-end"><Balanced ok={tb.balanced} /></div>
      <div className="overflow-hidden rounded-lg border border-neutral-200">
        <table className="w-full text-sm">
          <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
            <tr>
              <th className="px-3 py-2 w-20">Code</th>
              <th className="px-3 py-2">Account</th>
              <th className="px-3 py-2 w-28 text-right">Opening</th>
              <th className="px-3 py-2 w-28 text-right">Period Dr</th>
              <th className="px-3 py-2 w-28 text-right">Period Cr</th>
              <th className="px-3 py-2 w-28 text-right">Closing Dr</th>
              <th className="px-3 py-2 w-28 text-right">Closing Cr</th>
            </tr>
          </thead>
          <tbody>
            {tb.rows.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-neutral-400">No activity in {tb.period}.</td></tr>
            )}
            {tb.rows.map((r, i) => (
              <tr key={r.account_code} onClick={() => onDrill(r.account_code)}
                  className={cn('cursor-pointer border-t border-neutral-100 hover:bg-primary-50/40', i % 2 && 'bg-neutral-50/40')}>
                <td className="px-3 py-2 font-mono text-xs">{r.account_code}</td>
                <td className="px-3 py-2">{r.account_name}</td>
                <td className="px-3 py-2 text-right font-mono text-neutral-500">{r.opening !== '0.00' ? money(r.opening) : '—'}</td>
                <td className="px-3 py-2 text-right font-mono">{r.period_debit !== '0.00' ? money(r.period_debit) : ''}</td>
                <td className="px-3 py-2 text-right font-mono">{r.period_credit !== '0.00' ? money(r.period_credit) : ''}</td>
                <td className="px-3 py-2 text-right font-mono">{drCol(r.closing)}</td>
                <td className="px-3 py-2 text-right font-mono">{crCol(r.closing)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-neutral-200 bg-neutral-50 font-semibold">
              <td className="px-3 py-2" colSpan={3}>Totals</td>
              <td className="px-3 py-2 text-right font-mono">{money(tb.totals.period_debit)}</td>
              <td className="px-3 py-2 text-right font-mono">{money(tb.totals.period_credit)}</td>
              <td className="px-3 py-2 text-right font-mono" colSpan={2}>
                {tb.balanced ? 'debits = credits' : 'imbalance!'}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="mt-2 text-xs text-neutral-400">Click a row to open its account ledger.</p>
    </>
  )
}

function Section({ title, rows, total }: { title: string; rows: StatementRow[]; total: string }) {
  return (
    <div className="mb-4">
      <h3 className="mb-1 text-sm font-semibold text-neutral-700">{title}</h3>
      <div className="overflow-hidden rounded-lg border border-neutral-200">
        <table className="w-full text-sm">
          <tbody>
            {rows.length === 0 && <tr><td className="px-3 py-2 text-neutral-400">—</td></tr>}
            {rows.map((r) => (
              <tr key={r.account_code + r.account_name} className="border-t border-neutral-100 first:border-t-0">
                <td className="px-3 py-2 w-20 font-mono text-xs text-neutral-500">{r.account_code}</td>
                <td className="px-3 py-2">{r.account_name}</td>
                <td className="px-3 py-2 text-right font-mono">{money(r.amount)}</td>
              </tr>
            ))}
            <tr className="border-t-2 border-neutral-200 bg-neutral-50 font-semibold">
              <td className="px-3 py-2" colSpan={2}>Total {title}</td>
              <td className="px-3 py-2 text-right font-mono">{money(total)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}

function BalanceSheetTab({ q }: { q: any }) {
  if (q.isFetching && !q.data) return <Loading />
  const bs: BalanceSheet | undefined = q.data
  if (!bs) return null
  return (
    <>
      <div className="mb-2 flex justify-end"><Balanced ok={bs.balanced} /></div>
      <Section title="Assets" rows={bs.assets} total={bs.assets_total} />
      <Section title="Liabilities" rows={bs.liabilities} total={bs.liabilities_total} />
      <Section title="Equity" rows={bs.equity} total={bs.equity_total} />
      <div className="flex justify-end gap-8 rounded-lg bg-neutral-50 px-4 py-3 text-sm font-semibold">
        <span>Assets: <span className="font-mono">{money(bs.assets_total)}</span></span>
        <span>Liabilities + Equity: <span className="font-mono">{money(bs.liabilities_and_equity_total)}</span></span>
      </div>
    </>
  )
}

function IncomeStatementTab({ q, ytd, setYtd }: { q: any; ytd: boolean; setYtd: (v: boolean) => void }) {
  const s: IncomeStatement | undefined = q.data
  return (
    <>
      <div className="mb-2 flex items-center justify-end gap-2 text-sm">
        <label className="flex items-center gap-1.5 text-neutral-600">
          <input type="checkbox" checked={ytd} onChange={(e) => setYtd(e.target.checked)} /> Year-to-date
        </label>
      </div>
      {q.isFetching && !s ? <Loading /> : s && (
        <>
          <Section title="Revenue" rows={s.revenue} total={s.revenue_total} />
          <Section title="Expenses" rows={s.expense} total={s.expense_total} />
          <div className="flex justify-end rounded-lg bg-neutral-50 px-4 py-3 text-base font-semibold">
            Net income: <span className={cn('ml-2 font-mono', num(s.net_income) >= 0 ? 'text-green-700' : 'text-red-600')}>
              {money(s.net_income)}</span>
          </div>
        </>
      )}
    </>
  )
}

function JournalTab({ q }: { q: any }) {
  if (q.isFetching && !q.data) return <Loading />
  const entries: any[] = q.data ?? []
  if (entries.length === 0) return <div className="rounded-lg border border-dashed border-neutral-300 p-10 text-center text-sm text-neutral-500">No journal entries this period.</div>
  return (
    <div className="space-y-3">
      {entries.map((e) => (
        <div key={e.event_id} className="overflow-hidden rounded-lg border border-neutral-200">
          <div className="flex items-center justify-between bg-neutral-50 px-3 py-2 text-sm">
            <span className="font-mono text-xs">{e.date} · {e.source}</span>
            <span className="rounded-full bg-white px-2 py-0.5 text-xs text-neutral-600">{e.event_type}</span>
          </div>
          <table className="w-full text-sm">
            <tbody>
              {e.lines.map((l: any, i: number) => (
                <tr key={i} className="border-t border-neutral-100">
                  <td className="px-3 py-1.5 w-20 font-mono text-xs text-neutral-500">{l.account_code ?? '—'}</td>
                  <td className="px-3 py-1.5">{l.partner_name || l.line_role}</td>
                  <td className="px-3 py-1.5 w-28 text-right font-mono">{num(l.debit) ? money(l.debit) : ''}</td>
                  <td className="px-3 py-1.5 w-28 text-right font-mono">{num(l.credit) ? money(l.credit) : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

// ── account ledger drilldown ────────────────────────────────────────────────────
function AccountLedgerModal({ code, period, onClose }: { code: string; period: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['gl-ledger', code, period],
    queryFn: () => financeApi.get<any>(`/gl/account/${code}?period=${period}`),
  })
  return (
    <Modal title={`Ledger — ${code} ${data?.account_name ?? ''} · ${period}`} onClose={onClose} wide>
      {isLoading ? <Loading /> : data && (
        <>
          <div className="mb-2 flex justify-between text-sm text-neutral-600">
            <span>Opening: <span className="font-mono">{money(data.opening)}</span></span>
            <span>Closing: <span className="font-mono font-semibold">{money(data.closing)}</span></span>
          </div>
          <div className="max-h-96 overflow-y-auto rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr>
                  <th className="px-3 py-2 w-24">Date</th>
                  <th className="px-3 py-2">Source</th>
                  <th className="px-3 py-2 w-24 text-right">Debit</th>
                  <th className="px-3 py-2 w-24 text-right">Credit</th>
                  <th className="px-3 py-2 w-28 text-right">Balance</th>
                </tr>
              </thead>
              <tbody>
                {data.entries.length === 0 && (
                  <tr><td colSpan={5} className="px-3 py-6 text-center text-neutral-400">No movement this period.</td></tr>
                )}
                {data.entries.map((en: any, i: number) => (
                  <tr key={i} className="border-t border-neutral-100">
                    <td className="px-3 py-1.5 font-mono text-xs text-neutral-600">{en.date}</td>
                    <td className="px-3 py-1.5">
                      <span className="font-mono text-xs">{en.source}</span>
                      {en.partner_name && <span className="ml-1 text-neutral-500">· {en.partner_name}</span>}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">{num(en.debit) ? money(en.debit) : ''}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{num(en.credit) ? money(en.credit) : ''}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{money(en.balance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  )
}

// ── opening balance modal ───────────────────────────────────────────────────────
interface OpenLine { account_code: string; side: 'debit' | 'credit'; amount: string }

function OpeningModal({ onClose, onSaved, onError }: {
  onClose: () => void; onSaved: () => void; onError: (m: string) => void
}) {
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10))
  const [lines, setLines] = useState<OpenLine[]>([{ account_code: '', side: 'debit', amount: '' }])
  const [busy, setBusy] = useState(false)

  const { data: accounts = [] } = useQuery({
    queryKey: ['coa-postable'],
    queryFn: () => financeApi.get<Account[]>('/coa?postable_only=true'),
  })

  const totals = useMemo(() => {
    let dr = 0, cr = 0
    for (const l of lines) {
      const a = Number(l.amount) || 0
      if (l.side === 'debit') dr += a; else cr += a
    }
    return { dr, cr, balanced: dr === cr && dr > 0 }
  }, [lines])

  const setLine = (i: number, patch: Partial<OpenLine>) =>
    setLines((p) => p.map((l, idx) => idx === i ? { ...l, ...patch } : l))

  const save = async () => {
    const clean = lines.filter((l) => l.account_code && Number(l.amount) > 0)
    if (clean.length === 0) return onError('Add at least one line')
    if (!totals.balanced) return onError('Debits must equal credits')
    setBusy(true)
    try {
      await financeApi.post('/gl/opening-balance', {
        as_of: asOf,
        lines: clean.map((l) => ({
          account_code: l.account_code,
          debit: l.side === 'debit' ? Number(l.amount).toFixed(2) : '0',
          credit: l.side === 'credit' ? Number(l.amount).toFixed(2) : '0',
        })),
      })
      onSaved()
    } catch (e) { onError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal title="Post opening balance" onClose={onClose} wide>
      <p className="mb-3 text-xs text-neutral-500">
        Enter the migrated (NC65) trial balance as one balanced opening journal at cut-over.
      </p>
      <label className="mb-3 flex w-48 flex-col gap-1 text-sm">
        <span className="text-neutral-600">As-of date</span>
        <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} className={inputCls} />
      </label>
      <div className="space-y-2">
        {lines.map((l, i) => (
          <div key={i} className="grid grid-cols-[1fr_7rem_8rem_auto] items-center gap-2">
            <select value={l.account_code} onChange={(e) => setLine(i, { account_code: e.target.value })} className={inputCls}>
              <option value="">Select account…</option>
              {accounts.map((a) => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}
            </select>
            <select value={l.side} onChange={(e) => setLine(i, { side: e.target.value as 'debit' | 'credit' })} className={inputCls}>
              <option value="debit">Debit</option>
              <option value="credit">Credit</option>
            </select>
            <input type="number" step="0.01" value={l.amount} placeholder="0.00"
                   onChange={(e) => setLine(i, { amount: e.target.value })}
                   className={cn(inputCls, 'text-right font-mono')} />
            <button onClick={() => setLines((p) => p.filter((_, idx) => idx !== i))}
                    className="rounded p-1.5 text-neutral-400 hover:text-red-600"><Trash2 className="h-4 w-4" /></button>
          </div>
        ))}
      </div>
      <button onClick={() => setLines((p) => [...p, { account_code: '', side: 'debit', amount: '' }])}
              className={cn(secondaryBtn, 'mt-2')}>
        <Plus className="h-4 w-4" /> Add line
      </button>

      <div className="mt-4 flex items-center justify-end gap-6 border-t border-neutral-100 pt-3 text-sm">
        <span>Debits: <span className="font-mono">{money(String(totals.dr))}</span></span>
        <span>Credits: <span className="font-mono">{money(String(totals.cr))}</span></span>
        <Balanced ok={totals.balanced} />
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className={secondaryBtn}>Cancel</button>
        <button onClick={save} disabled={busy || !totals.balanced} className={primaryBtn}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Post
        </button>
      </div>
    </Modal>
  )
}

// ── year-end close modal ─────────────────────────────────────────────────────────
function CloseYearModal({ onClose, onDone, onError }: {
  onClose: () => void; onDone: (net: string) => void; onError: (m: string) => void
}) {
  const [year, setYear] = useState(new Date().getFullYear())
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    try {
      const r = await financeApi.post<{ net_income: string; already_closed: boolean }>(
        '/gl/close-year', { fiscal_year: year })
      if (r.already_closed) return onError(`Fiscal year ${year} is already closed`)
      onDone(r.net_income)
    } catch (e) { onError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal title="Year-end close" onClose={onClose}>
      <p className="mb-3 text-sm text-neutral-600">
        Sweep all revenue and expense balances for the fiscal year into Retained Earnings (3100),
        zeroing the P&L for the new year. This posts one balanced closing journal dated Dec 31.
      </p>
      <label className="flex w-40 flex-col gap-1 text-sm">
        <span className="text-neutral-600">Fiscal year</span>
        <input type="number" value={year} onChange={(e) => setYear(Number(e.target.value))}
               className={cn(inputCls, 'font-mono')} />
      </label>
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose} className={secondaryBtn}>Cancel</button>
        <button onClick={run} disabled={busy} className={primaryBtn}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CalendarClock className="h-4 w-4" />} Run Close
        </button>
      </div>
    </Modal>
  )
}

// ── shared modal shell ───────────────────────────────────────────────────────────
function Modal({ title, children, onClose, wide }: {
  title: string; children: React.ReactNode; onClose: () => void; wide?: boolean
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className={cn('w-full rounded-xl bg-white p-5 shadow-xl', wide ? 'max-w-3xl' : 'max-w-md')}
           onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-800">
            <BookOpen className="h-4 w-4 text-neutral-400" /> {title}
          </h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
