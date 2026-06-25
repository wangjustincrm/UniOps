/**
 * Bank & Cards settings (Finance) — manage payment accounts.
 *
 * One table for all funding accounts: bank accounts (cash asset GL) and company
 * credit cards (Credit Card Payable liability GL). Both feed the same payment-
 * source picker used by Payment Batches and PA "Mark as Processed", and both
 * reconcile through the Bank Reconciliation workbench (statement import).
 *
 * Styling follows the Portal convention (CoaConfigPage): neutral palette, zebra
 * rows, status pills, #085E5E primary.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, CreditCard, Landmark, Loader2, Pencil, Plus, X } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

interface Account {
  id: string; name: string; bank_name: string; kind: string
  account_masked: string | null; currency: string
  ledger_account_code: string | null; is_active: boolean
}

const KIND_LABEL: Record<string, string> = { bank: 'Bank Account', credit_card: 'Credit Card' }

function KindBadge({ kind }: { kind: string }) {
  const card = kind === 'credit_card'
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
      card ? 'bg-violet-50 text-violet-700' : 'bg-sky-50 text-sky-700')}>
      {card ? <CreditCard className="h-3 w-3" /> : <Landmark className="h-3 w-3" />}
      {KIND_LABEL[kind] ?? kind}
    </span>
  )
}

export default function BankSettingsPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [kindFilter, setKindFilter] = useState('')
  const [editing, setEditing] = useState<Partial<Account> | null>(null)
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const { data: perms } = useQuery({
    queryKey: ['coa-permissions'],
    queryFn: () => financeApi.get<{ can_manage: boolean }>('/coa/permissions'),
  })
  const canManage = perms?.can_manage ?? false

  const { data: accounts = [], isFetching } = useQuery({
    queryKey: ['bank-accounts'],
    queryFn: () => financeApi.get<Account[]>('/bank/accounts'),
  })

  const flash = (k: 'ok' | 'err', text: string) => { setBanner({ kind: k, text }); setTimeout(() => setBanner(null), 5000) }
  const rows = useMemo(
    () => accounts.filter((a) => !kindFilter || a.kind === kindFilter),
    [accounts, kindFilter])

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/bank-settings"
      title="Bank & Cards"
      subtitle="Bank accounts and company credit cards — the payment sources for runs and PA processing"
    >
      <div className="mx-auto max-w-6xl">
        {banner && (
          <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700')}>
            {banner.text}
          </div>
        )}

        <div className="mb-3 flex flex-wrap items-center gap-3">
          <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)} className={cn(inputCls, 'w-44')}>
            <option value="">All accounts</option>
            <option value="bank">Bank Accounts</option>
            <option value="credit_card">Credit Cards</option>
          </select>
          {canManage && (
            <button onClick={() => setEditing({ kind: 'bank', currency: 'CAD', is_active: true })}
                    className={cn(primaryBtn, 'ml-auto')}>
              <Plus className="h-4 w-4" /> New Account
            </button>
          )}
        </div>

        <div className="overflow-hidden rounded-lg border border-neutral-200">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
              <tr>
                <th className="px-3 py-2">Name</th>
                <th className="px-3 py-2">Bank / Issuer</th>
                <th className="px-3 py-2 w-32">Type</th>
                <th className="px-3 py-2 w-24">Account</th>
                <th className="px-3 py-2 w-16">Ccy</th>
                <th className="px-3 py-2 w-28">GL Account</th>
                <th className="px-3 py-2 w-20">Status</th>
                <th className="px-3 py-2 w-16" />
              </tr>
            </thead>
            <tbody>
              {isFetching && (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin" /></td></tr>
              )}
              {!isFetching && rows.length === 0 && (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">No accounts.</td></tr>
              )}
              {rows.map((a, i) => (
                <tr key={a.id} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40', !a.is_active && 'opacity-50')}>
                  <td className="px-3 py-2 font-medium text-neutral-800">{a.name}</td>
                  <td className="px-3 py-2 text-neutral-600">{a.bank_name}</td>
                  <td className="px-3 py-2"><KindBadge kind={a.kind} /></td>
                  <td className="px-3 py-2 font-mono text-xs text-neutral-500">{a.account_masked ? `…${a.account_masked}` : '—'}</td>
                  <td className="px-3 py-2 text-xs text-neutral-500">{a.currency}</td>
                  <td className="px-3 py-2 font-mono text-xs">{a.ledger_account_code || <span className="text-amber-600">unmapped</span>}</td>
                  <td className="px-3 py-2">
                    <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium',
                      a.is_active ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                      {a.is_active ? 'active' : 'inactive'}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    {canManage && (
                      <button onClick={() => setEditing(a)} className="rounded p-1 text-neutral-400 hover:text-[#085E5E]">
                        <Pencil className="h-4 w-4" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-neutral-400">
          Credit cards map to a liability GL account (e.g. 2050 Credit Card Payable); bank accounts map to a cash asset (e.g. 1010).
        </p>
      </div>

      {editing && (
        <AccountModal initial={editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); qc.invalidateQueries({ queryKey: ['bank-accounts'] }); flash('ok', 'Account saved') }}
          onError={(m) => flash('err', m)} />
      )}
    </PortalChromeLayout>
  )
}

function AccountModal({ initial, onClose, onSaved, onError }: {
  initial: Partial<Account>; onClose: () => void; onSaved: () => void; onError: (m: string) => void
}) {
  const [f, setF] = useState<Partial<Account>>(initial)
  const [busy, setBusy] = useState(false)
  const isNew = !initial.id
  const isCard = f.kind === 'credit_card'
  const set = (patch: Partial<Account>) => setF((p) => ({ ...p, ...patch }))

  const save = async () => {
    if (!f.name || !f.bank_name) return onError(isCard ? 'Card name and issuer are required' : 'Name and bank are required')
    setBusy(true)
    try {
      const body = {
        name: f.name, bank_name: f.bank_name, kind: f.kind || 'bank',
        account_masked: f.account_masked || null, currency: f.currency || 'CAD',
        ledger_account_code: f.ledger_account_code || null, is_active: f.is_active ?? true,
      }
      if (isNew) await financeApi.post('/bank/accounts', body)
      else await financeApi.put(`/bank/accounts/${f.id}`, body)
      onSaved()
    } catch (e) { onError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-800">{isNew ? 'New account' : 'Edit account'}</h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700"><X className="h-5 w-5" /></button>
        </div>
        <div className="grid gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-neutral-600">Type</span>
            <select value={f.kind ?? 'bank'} onChange={(e) => set({ kind: e.target.value })} className={inputCls}>
              <option value="bank">Bank Account</option>
              <option value="credit_card">Credit Card</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-neutral-600">{isCard ? 'Card name' : 'Account name'}</span>
            <input value={f.name ?? ''} onChange={(e) => set({ name: e.target.value })}
                   placeholder={isCard ? 'e.g. Amex Corporate' : 'e.g. RBC Operating'} className={inputCls} />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-neutral-600">{isCard ? 'Card issuer' : 'Bank'}</span>
              <input value={f.bank_name ?? ''} onChange={(e) => set({ bank_name: e.target.value })}
                     placeholder={isCard ? 'Visa / MC / Amex' : 'Royal Bank of Canada'} className={inputCls} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-neutral-600">{isCard ? 'Card last 4' : 'Account last 4'}</span>
              <input value={f.account_masked ?? ''} onChange={(e) => set({ account_masked: e.target.value })} className={inputCls} />
            </label>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-neutral-600">Currency</span>
              <select value={f.currency ?? 'CAD'} onChange={(e) => set({ currency: e.target.value })} className={inputCls}>
                {['CAD', 'USD', 'CNY', 'EUR'].map((c) => <option key={c}>{c}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-neutral-600">GL account code</span>
              <input value={f.ledger_account_code ?? ''} onChange={(e) => set({ ledger_account_code: e.target.value })}
                     placeholder={isCard ? '2050 (liability)' : '1010 (cash)'} className={cn(inputCls, 'font-mono')} />
            </label>
          </div>
          <p className="text-xs text-neutral-400">
            {isCard
              ? 'Card payments credit this liability (Credit Card Payable). Pay the card statement later from a bank account.'
              : 'Bank payments credit this cash asset account.'}
          </p>
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
      </div>
    </div>
  )
}
