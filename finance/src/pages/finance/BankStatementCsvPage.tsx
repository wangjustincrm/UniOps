/**
 * Statement import by CSV — the fallback path.
 *
 * Superseded as the main workbench by BankReconciliationPage (v2), which reads
 * the statement PDF, the bank's payment files and NC's ledger. This page stays
 * reachable from there, because a bank whose PDF we cannot read yet still has to
 * be importable: every bank's CSV export differs (debit/credit split, date
 * formats, RBC "01 May"), and the per-account column mapping lives here.
 *
 * - Account picker (the 11 real CRM accounts: Chase US+Toronto, Bank of China,
 *   ICBC, RBC; CAD/USD/CNY) + create/edit (incl. GL account code mapping)
 * - Statement import with a column-mapping modal: every bank's export differs
 *   (debit/credit split, date formats, RBC '01 May'), so the user maps columns
 *   once per account; the mapping is saved server-side and reused next month
 * - Transaction list with status filter; auto-match, manual match (pick a
 *   payment), exclude (bank fees / interest / internal FX transfers)
 * - Discrepancy panel: unmatched statement lines vs unreconciled payments
 *
 * Styling follows the Portal convention (CoaConfigPage): neutral palette,
 * zebra rows, code chips, status pills, #085E5E primary.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Ban, Check, Landmark, Link2, Loader2, Plus, RefreshCw,
  Undo2, Upload, X,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi, financeUploadFields } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

interface Account {
  id: string; name: string; bank_name: string; account_masked: string | null
  currency: string; ledger_account_code: string | null; is_active: boolean
  import_mapping: ColumnMapping | null
}
interface Txn {
  id: string; txn_date: string; description: string; reference: string | null
  amount: string; currency: string; status: string; matched_payment_id: string | null
}
interface DuePayment {
  payment_record_id: string; doc_number: string | null; amount: string
  currency: string; payment_date: string
}
interface Recon {
  account: { id: string; name: string; currency: string }
  inflow: string; outflow: string; matched_total: string; unmatched_count: number
  unmatched_transactions: { id: string; txn_date: string; description: string; amount: string }[]
  unreconciled_payments: DuePayment[]
}
interface ColumnMapping {
  date: string; description: string; reference?: string
  amount?: string; debit?: string; credit?: string
  debit_sign?: 'negative' | 'positive'
  date_format?: string; default_year?: number
}

const DATE_FORMATS: { value: string; label: string }[] = [
  { value: '', label: 'ISO (2026-05-06)' },
  { value: '%m/%d/%y', label: 'MM/DD/YY (05/06/26)' },
  { value: '%m/%d/%Y', label: 'MM/DD/YYYY (05/06/2026)' },
  { value: '%d %b', label: 'DD Mon, no year (06 May)' },
  { value: '%d-%b-%Y', label: 'DD-Mon-YYYY (06-May-2026)' },
  { value: '%d/%m/%Y', label: 'DD/MM/YYYY (06/05/2026)' },
]

function fmtMoney(v: string, ccy?: string): string {
  const n = Number(v)
  const s = n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return ccy ? `${s} ${ccy}` : s
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    matched: 'bg-green-50 text-green-700', unmatched: 'bg-amber-50 text-amber-700',
    excluded: 'bg-neutral-100 text-neutral-500',
  }
  return <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', map[status] ?? 'bg-neutral-100 text-neutral-600')}>{status}</span>
}

// ── header-row parse (tolerant of quoted commas) ─────────────────────────────────
function parseHeader(line: string): string[] {
  const out: string[] = []
  let cur = '', inQ = false
  for (const ch of line) {
    if (ch === '"') inQ = !inQ
    else if (ch === ',' && !inQ) { out.push(cur.trim()); cur = '' }
    else cur += ch
  }
  out.push(cur.trim())
  return out.filter((h) => h.length > 0)
}

export default function BankStatementCsvPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [accountId, setAccountId] = useState<string>('')
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [editAccount, setEditAccount] = useState<Partial<Account> | null>(null)
  const [matchFor, setMatchFor] = useState<Txn | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

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

  const { data: txns = [], isFetching: txnsLoading } = useQuery({
    queryKey: ['bank-txns', accountId, statusFilter],
    queryFn: () => financeApi.get<Txn[]>(
      `/bank/${accountId}/transactions${statusFilter ? `?status=${statusFilter}` : ''}`),
    enabled: !!accountId,
  })

  const { data: recon } = useQuery({
    queryKey: ['bank-recon', accountId],
    queryFn: () => financeApi.get<Recon>(`/bank/reconciliation?account_id=${accountId}`),
    enabled: !!accountId,
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['bank-txns', accountId] })
    qc.invalidateQueries({ queryKey: ['bank-recon', accountId] })
  }
  const flash = (kind: 'ok' | 'err', text: string) => {
    setBanner({ kind, text }); setTimeout(() => setBanner(null), 5000)
  }

  const autoMatch = useMutation({
    mutationFn: () => financeApi.post<{ matched: number; ambiguous: number; scanned: number }>(
      `/bank/match/auto?account_id=${accountId}`, {}),
    onSuccess: (r) => { flash('ok', `Auto-match: ${r.matched} matched, ${r.ambiguous} ambiguous (left for review), ${r.scanned} scanned`); refresh() },
    onError: (e: Error) => flash('err', e.message),
  })

  const exclude = useMutation({
    mutationFn: ({ id, excluded }: { id: string; excluded: boolean }) =>
      financeApi.post(`/bank/transactions/${id}/exclude`, { excluded }),
    onSuccess: () => refresh(),
    onError: (e: Error) => flash('err', e.message),
  })
  const unmatch = useMutation({
    mutationFn: (id: string) => financeApi.post(`/bank/transactions/${id}/unmatch`, {}),
    onSuccess: () => refresh(),
    onError: (e: Error) => flash('err', e.message),
  })

  if (!user) return <Navigate to="/login" replace />

  const byBank = useMemo(() => {
    const groups: Record<string, Account[]> = {}
    for (const a of accounts) (groups[a.bank_name] ??= []).push(a)
    return groups
  }, [accounts])

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/bank"
      title="Statement Import (CSV)"
      subtitle="Map a bank\u2019s CSV export once, then reuse it — for accounts whose PDF statement cannot be read yet"
    >
      <div className="mx-auto max-w-6xl">
        {banner && (
          <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700')}>
            {banner.text}
          </div>
        )}

        {/* account bar */}
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Landmark className="h-5 w-5 text-neutral-400" />
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)}
                  className={cn(inputCls, 'w-80')}>
            <option value="">Select a bank account…</option>
            {Object.entries(byBank).map(([bank, accts]) => (
              <optgroup key={bank} label={bank}>
                {accts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} · {a.currency}{a.account_masked ? ` · …${a.account_masked}` : ''}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          {account && (
            <span className="text-xs text-neutral-500">
              GL: {account.ledger_account_code || '— not mapped —'}
            </span>
          )}
          <div className="ml-auto flex gap-2">
            {canManage && (
              <button onClick={() => setEditAccount({ currency: 'CAD', is_active: true })}
                      className={secondaryBtn}>
                <Plus className="h-4 w-4" /> New Account
              </button>
            )}
            {account && canManage && (
              <button onClick={() => setEditAccount(account)} className={secondaryBtn}>
                Edit
              </button>
            )}
          </div>
        </div>

        {!account ? (
          <div className="rounded-lg border border-dashed border-neutral-300 p-10 text-center text-sm text-neutral-500">
            Select a bank account to view statement lines and reconciliation.
          </div>
        ) : (
          <>
            {/* summary cards */}
            {recon && (
              <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <SummaryCard label="Inflow" value={fmtMoney(recon.inflow, account.currency)} tone="pos" />
                <SummaryCard label="Outflow" value={fmtMoney(recon.outflow, account.currency)} tone="neg" />
                <SummaryCard label="Matched" value={fmtMoney(recon.matched_total, account.currency)} />
                <SummaryCard label="Unmatched lines" value={String(recon.unmatched_count)}
                             tone={recon.unmatched_count ? 'warn' : undefined} />
              </div>
            )}

            {/* toolbar */}
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
                      className={cn(inputCls, 'w-40')}>
                <option value="">All statuses</option>
                <option value="unmatched">Unmatched</option>
                <option value="matched">Matched</option>
                <option value="excluded">Excluded</option>
              </select>
              <div className="ml-auto flex gap-2">
                {canManage && (
                  <>
                    <button onClick={() => autoMatch.mutate()} disabled={autoMatch.isPending}
                            className={secondaryBtn}>
                      {autoMatch.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                      Auto-match
                    </button>
                    <input ref={fileInput} type="file" accept=".csv,text/csv" className="hidden"
                           onChange={(e) => e.target.files?.[0] && setImportFile(e.target.files[0])} />
                    <button onClick={() => fileInput.current?.click()} className={primaryBtn}>
                      <Upload className="h-4 w-4" /> Import Statement
                    </button>
                  </>
                )}
              </div>
            </div>

            {/* transactions */}
            <div className="overflow-hidden rounded-lg border border-neutral-200">
              <table className="w-full text-sm">
                <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                  <tr>
                    <th className="px-3 py-2 w-28">Date</th>
                    <th className="px-3 py-2">Description</th>
                    <th className="px-3 py-2 w-40">Reference</th>
                    <th className="px-3 py-2 w-36 text-right">Amount</th>
                    <th className="px-3 py-2 w-24">Status</th>
                    <th className="px-3 py-2 w-44" />
                  </tr>
                </thead>
                <tbody>
                  {txnsLoading && (
                    <tr><td colSpan={6} className="px-3 py-6 text-center text-neutral-400">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin" /></td></tr>
                  )}
                  {!txnsLoading && txns.length === 0 && (
                    <tr><td colSpan={6} className="px-3 py-6 text-center text-neutral-400">
                      No statement lines. Import a statement to begin.</td></tr>
                  )}
                  {txns.map((t, i) => {
                    const amt = Number(t.amount)
                    return (
                      <tr key={t.id} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                        <td className="px-3 py-2 font-mono text-xs text-neutral-600">{t.txn_date}</td>
                        <td className="px-3 py-2">{t.description}</td>
                        <td className="px-3 py-2 text-xs text-neutral-500">{t.reference || '—'}</td>
                        <td className={cn('px-3 py-2 text-right font-mono', amt < 0 ? 'text-red-600' : 'text-green-700')}>
                          {fmtMoney(t.amount)}
                        </td>
                        <td className="px-3 py-2"><StatusPill status={t.status} /></td>
                        <td className="px-3 py-2">
                          {canManage && (
                            <div className="flex justify-end gap-1.5">
                              {t.status === 'matched' ? (
                                <button onClick={() => unmatch.mutate(t.id)} title="Unmatch"
                                        className="rounded p-1 text-neutral-400 hover:text-amber-600">
                                  <Undo2 className="h-4 w-4" /></button>
                              ) : t.status === 'excluded' ? (
                                <button onClick={() => exclude.mutate({ id: t.id, excluded: false })}
                                        title="Re-include" className="rounded p-1 text-neutral-400 hover:text-[#085E5E]">
                                  <Undo2 className="h-4 w-4" /></button>
                              ) : (
                                <>
                                  {amt < 0 && (
                                    <button onClick={() => setMatchFor(t)} title="Match to payment"
                                            className="rounded p-1 text-neutral-400 hover:text-[#085E5E]">
                                      <Link2 className="h-4 w-4" /></button>
                                  )}
                                  <button onClick={() => exclude.mutate({ id: t.id, excluded: true })}
                                          title="Exclude (fee / interest / internal transfer)"
                                          className="rounded p-1 text-neutral-400 hover:text-neutral-700">
                                    <Ban className="h-4 w-4" /></button>
                                </>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* discrepancy: payments not seen on the bank */}
            {recon && recon.unreconciled_payments.length > 0 && (
              <div className="mt-6">
                <h3 className="mb-2 text-sm font-semibold text-neutral-700">
                  Payments not yet seen on any statement ({recon.unreconciled_payments.length})
                </h3>
                <div className="overflow-hidden rounded-lg border border-neutral-200">
                  <table className="w-full text-sm">
                    <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                      <tr>
                        <th className="px-3 py-2 w-28">Date</th>
                        <th className="px-3 py-2">Document</th>
                        <th className="px-3 py-2 w-36 text-right">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recon.unreconciled_payments.map((p) => (
                        <tr key={p.payment_record_id} className="border-t border-neutral-100">
                          <td className="px-3 py-2 font-mono text-xs text-neutral-600">{p.payment_date}</td>
                          <td className="px-3 py-2">{p.doc_number || p.payment_record_id.slice(0, 8)}</td>
                          <td className="px-3 py-2 text-right font-mono">{fmtMoney(p.amount, p.currency)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {importFile && account && (
        <ImportMappingModal
          account={account} file={importFile}
          onClose={() => setImportFile(null)}
          onDone={(r) => {
            setImportFile(null)
            flash('ok', `Imported ${r.imported}, ${r.duplicates} duplicates, ${r.skipped} skipped${r.errors.length ? `, ${r.errors.length} errors` : ''}`)
            refresh()
            qc.invalidateQueries({ queryKey: ['bank-accounts'] })
          }}
          onError={(m) => flash('err', m)}
        />
      )}

      {editAccount && (
        <AccountModal
          initial={editAccount}
          onClose={() => setEditAccount(null)}
          onSaved={() => { setEditAccount(null); qc.invalidateQueries({ queryKey: ['bank-accounts'] }); flash('ok', 'Account saved') }}
          onError={(m) => flash('err', m)}
        />
      )}

      {matchFor && account && (
        <ManualMatchModal
          txn={matchFor} candidates={recon?.unreconciled_payments ?? []}
          onClose={() => setMatchFor(null)}
          onMatched={() => { setMatchFor(null); refresh(); flash('ok', 'Matched') }}
          onError={(m) => flash('err', m)}
        />
      )}
    </PortalChromeLayout>
  )
}

function SummaryCard({ label, value, tone }: { label: string; value: string; tone?: 'pos' | 'neg' | 'warn' }) {
  const c = tone === 'pos' ? 'text-green-700' : tone === 'neg' ? 'text-red-600' : tone === 'warn' ? 'text-amber-600' : 'text-neutral-800'
  return (
    <div className="rounded-lg border border-neutral-200 bg-white px-4 py-3">
      <div className="text-xs text-neutral-500">{label}</div>
      <div className={cn('mt-0.5 font-mono text-lg', c)}>{value}</div>
    </div>
  )
}

// ── Import + column mapping modal ────────────────────────────────────────────────
function ImportMappingModal({ account, file, onClose, onDone, onError }: {
  account: Account; file: File; onClose: () => void
  onDone: (r: { imported: number; duplicates: number; skipped: number; errors: string[] }) => void
  onError: (m: string) => void
}) {
  const [headers, setHeaders] = useState<string[]>([])
  const saved = account.import_mapping
  const [m, setM] = useState<ColumnMapping>(saved ?? { date: '', description: '' })
  const [amountMode, setAmountMode] = useState<'signed' | 'split'>(saved?.amount ? 'signed' : saved ? 'split' : 'signed')
  const [busy, setBusy] = useState(false)

  // read header row once
  useEffect(() => {
    file.text().then((txt) => {
      const first = txt.replace(/^﻿/, '').split(/\r?\n/)[0] ?? ''
      setHeaders(parseHeader(first))
    })
  }, [file])

  const set = (patch: Partial<ColumnMapping>) => setM((p) => ({ ...p, ...patch }))
  const colSelect = (key: keyof ColumnMapping, label: string, required = false) => (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-neutral-600">{label}{required && <span className="text-red-500"> *</span>}</span>
      <select value={(m[key] as string) ?? ''} onChange={(e) => set({ [key]: e.target.value || undefined } as Partial<ColumnMapping>)}
              className={inputCls}>
        <option value="">— none —</option>
        {headers.map((h) => <option key={h} value={h}>{h}</option>)}
      </select>
    </label>
  )

  const submit = async () => {
    // build clean mapping
    const map: ColumnMapping = {
      date: m.date, description: m.description, reference: m.reference,
      date_format: m.date_format || undefined, default_year: m.default_year || undefined,
    }
    if (amountMode === 'signed') map.amount = m.amount
    else { map.debit = m.debit; map.credit = m.credit; map.debit_sign = m.debit_sign || 'negative' }
    if (!map.date || !map.description) return onError('Date and Description columns are required')
    if (amountMode === 'signed' && !map.amount) return onError('Pick the signed Amount column')
    if (amountMode === 'split' && !map.debit && !map.credit) return onError('Pick at least a Debit or Credit column')
    setBusy(true)
    try {
      const r = await financeUploadFields<{ imported: number; duplicates: number; skipped: number; errors: string[] }>(
        `/bank/${account.id}/import`, file, { mapping: JSON.stringify(map), save_mapping: 'true' })
      onDone(r)
    } catch (e) {
      onError((e as Error).message)
    } finally { setBusy(false) }
  }

  return (
    <Modal title={`Import statement — ${account.name}`} onClose={onClose} wide>
      <p className="mb-3 text-xs text-neutral-500">
        File: <span className="font-mono">{file.name}</span> · map the columns from this bank's export.
        {saved && ' Pre-filled from this account’s saved mapping.'}
      </p>
      <div className="grid grid-cols-2 gap-3">
        {colSelect('date', 'Date column', true)}
        {colSelect('description', 'Description column', true)}
        {colSelect('reference', 'Reference column')}
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Date format</span>
          <select value={m.date_format ?? ''} onChange={(e) => set({ date_format: e.target.value || undefined })}
                  className={inputCls}>
            {DATE_FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </label>
        {m.date_format === '%d %b' && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-neutral-600">Statement year (date has no year)</span>
            <input type="number" value={m.default_year ?? ''} placeholder="2026"
                   onChange={(e) => set({ default_year: e.target.value ? Number(e.target.value) : undefined })}
                   className={inputCls} />
          </label>
        )}
      </div>

      <div className="mt-4 rounded-lg border border-neutral-200 p-3">
        <div className="mb-2 flex gap-4 text-sm">
          <label className="flex items-center gap-1.5">
            <input type="radio" checked={amountMode === 'signed'} onChange={() => setAmountMode('signed')} />
            One signed amount column
          </label>
          <label className="flex items-center gap-1.5">
            <input type="radio" checked={amountMode === 'split'} onChange={() => setAmountMode('split')} />
            Separate debit / credit columns
          </label>
        </div>
        {amountMode === 'signed' ? (
          <div className="grid grid-cols-2 gap-3">{colSelect('amount', 'Amount column (negative = outflow)', true)}</div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {colSelect('debit', 'Debit column (outflow)')}
            {colSelect('credit', 'Credit column (inflow)')}
          </div>
        )}
      </div>

      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose} className={secondaryBtn}>Cancel</button>
        <button onClick={submit} disabled={busy || headers.length === 0} className={primaryBtn}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />} Import
        </button>
      </div>
    </Modal>
  )
}

// ── Account create/edit modal ────────────────────────────────────────────────────
function AccountModal({ initial, onClose, onSaved, onError }: {
  initial: Partial<Account>; onClose: () => void; onSaved: () => void; onError: (m: string) => void
}) {
  const [f, setF] = useState<Partial<Account>>(initial)
  const [busy, setBusy] = useState(false)
  const isNew = !initial.id
  const set = (patch: Partial<Account>) => setF((p) => ({ ...p, ...patch }))

  const save = async () => {
    if (!f.name || !f.bank_name) return onError('Name and bank are required')
    setBusy(true)
    try {
      const body = {
        name: f.name, bank_name: f.bank_name, account_masked: f.account_masked || null,
        currency: f.currency || 'CAD', ledger_account_code: f.ledger_account_code || null,
        is_active: f.is_active ?? true,
      }
      if (isNew) await financeApi.post('/bank/accounts', body)
      else await financeApi.put(`/bank/accounts/${f.id}`, body)
      onSaved()
    } catch (e) { onError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal title={isNew ? 'New bank account' : 'Edit bank account'} onClose={onClose}>
      <div className="grid gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Name</span>
          <input value={f.name ?? ''} onChange={(e) => set({ name: e.target.value })} className={inputCls} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Bank</span>
          <input value={f.bank_name ?? ''} onChange={(e) => set({ bank_name: e.target.value })} className={inputCls} />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-neutral-600">Account (last 4)</span>
            <input value={f.account_masked ?? ''} onChange={(e) => set({ account_masked: e.target.value })} className={inputCls} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-neutral-600">Currency</span>
            <select value={f.currency ?? 'CAD'} onChange={(e) => set({ currency: e.target.value })} className={inputCls}>
              {['CAD', 'USD', 'CNY', 'EUR'].map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">GL account code (COA bank account)</span>
          <input value={f.ledger_account_code ?? ''} onChange={(e) => set({ ledger_account_code: e.target.value })}
                 placeholder="e.g. 1010" className={inputCls} />
        </label>
        <label className="flex items-center gap-1.5 text-sm text-neutral-600">
          <input type="checkbox" checked={f.is_active ?? true} onChange={(e) => set({ is_active: e.target.checked })} />
          Active
        </label>
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose} className={secondaryBtn}>Cancel</button>
        <button onClick={save} disabled={busy} className={primaryBtn}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Save
        </button>
      </div>
    </Modal>
  )
}

// ── Manual match modal ───────────────────────────────────────────────────────────
function ManualMatchModal({ txn, candidates, onClose, onMatched, onError }: {
  txn: Txn; candidates: DuePayment[]; onClose: () => void; onMatched: () => void; onError: (m: string) => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const target = Math.abs(Number(txn.amount))
  const sameCcy = candidates.filter((c) => c.currency === txn.currency)
  // exact-amount candidates first, then the rest
  const sorted = [...sameCcy].sort((a, b) =>
    Math.abs(Number(a.amount) - target) - Math.abs(Number(b.amount) - target))

  const match = async (paymentId: string) => {
    setBusy(paymentId)
    try {
      await financeApi.post(`/bank/transactions/${txn.id}/match`, { payment_record_id: paymentId })
      onMatched()
    } catch (e) { onError((e as Error).message) } finally { setBusy(null) }
  }

  return (
    <Modal title="Match statement line to a payment" onClose={onClose} wide>
      <div className="mb-3 rounded-md bg-neutral-50 px-3 py-2 text-sm">
        <span className="font-mono text-xs text-neutral-500">{txn.txn_date}</span> · {txn.description} ·
        <span className="ml-1 font-mono text-red-600">{fmtMoney(txn.amount)} {txn.currency}</span>
      </div>
      {sorted.length === 0 ? (
        <p className="py-6 text-center text-sm text-neutral-400">
          No unreconciled {txn.currency} payments to match. Use Exclude if this is a fee or internal transfer.
        </p>
      ) : (
        <div className="max-h-80 overflow-y-auto rounded-lg border border-neutral-200">
          <table className="w-full text-sm">
            <tbody>
              {sorted.map((c) => {
                const exact = Math.abs(Number(c.amount) - target) < 0.005
                return (
                  <tr key={c.payment_record_id} className="border-t border-neutral-100 first:border-t-0">
                    <td className="px-3 py-2 font-mono text-xs text-neutral-500">{c.payment_date}</td>
                    <td className="px-3 py-2">{c.doc_number || c.payment_record_id.slice(0, 8)}</td>
                    <td className="px-3 py-2 text-right font-mono">
                      {fmtMoney(c.amount, c.currency)}
                      {exact && <span className="ml-2 rounded bg-green-50 px-1.5 py-0.5 text-xs text-green-700">exact</span>}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button onClick={() => match(c.payment_record_id)} disabled={!!busy}
                              className={cn(secondaryBtn, 'ml-auto')}>
                        {busy === c.payment_record_id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
                        Match
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-xs text-neutral-400">
        Amount mismatches are allowed (bank fees etc.) — the actor is recorded on the match.
      </p>
    </Modal>
  )
}

// ── shared modal shell ───────────────────────────────────────────────────────────
function Modal({ title, children, onClose, wide }: {
  title: string; children: React.ReactNode; onClose: () => void; wide?: boolean
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className={cn('w-full rounded-xl bg-white p-5 shadow-xl', wide ? 'max-w-2xl' : 'max-w-md')}
           onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-800">{title}</h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
