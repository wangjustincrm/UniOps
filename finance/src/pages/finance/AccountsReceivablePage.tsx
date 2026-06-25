/**
 * Accounts Receivable workbench (Phase c UI)
 *
 * - Invoices: create (customer + pre-tax amount + output-tax lines), post
 *   (revenue recognition → debit AR / credit revenue / credit output tax),
 *   record receipts (debit bank / credit AR, partial or full), void drafts
 * - Aging: customer × currency, 5 buckets, outstanding-based
 *
 * Customers come from MDM business partners (is_customer); tax codes from the
 * B2 tax engine. Styling follows the Portal convention (CoaConfigPage).
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check, FileText, HandCoins, Loader2, Plus, Send, Trash2, X,
} from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi, mdmApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

interface Invoice {
  id: string; invoice_number: string; customer_id: string; customer_name: string
  amount: string; tax_amount: string; total_amount: string; paid_amount: string
  currency: string; invoice_date: string; due_date: string; status: string
  description: string | null
}
interface Partner { id: string; name: string; currency: string; payment_terms: string }
interface TaxCode { code: string; name: string; rate: string; recoverable: boolean }
interface AgingRow {
  customer_id: string; customer_name: string; currency: string
  current: string; d1_30: string; d31_60: string; d61_90: string; d90_plus: string; total: string
}

const STATUSES = ['draft', 'posted', 'partially_paid', 'paid', 'void'] as const

function fmtMoney(v: string, ccy?: string): string {
  const s = Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return ccy ? `${s} ${ccy}` : s
}
function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    draft: 'bg-neutral-100 text-neutral-600', posted: 'bg-blue-50 text-blue-700',
    partially_paid: 'bg-amber-50 text-amber-700', paid: 'bg-green-50 text-green-700',
    void: 'bg-neutral-100 text-neutral-400',
  }
  return <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', map[status] ?? 'bg-neutral-100 text-neutral-600')}>{status.replace('_', ' ')}</span>
}

export default function AccountsReceivablePage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'invoices' | 'aging'>('invoices')
  const [statusFilter, setStatusFilter] = useState('')
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [newInvoice, setNewInvoice] = useState(false)
  const [receiptFor, setReceiptFor] = useState<Invoice | null>(null)

  const { data: perms } = useQuery({
    queryKey: ['coa-permissions'],
    queryFn: () => financeApi.get<{ can_manage: boolean }>('/coa/permissions'),
  })
  const canManage = perms?.can_manage ?? false

  const { data: invoices = [], isFetching } = useQuery({
    queryKey: ['ar-invoices', statusFilter],
    queryFn: () => financeApi.get<Invoice[]>(`/ar/invoices${statusFilter ? `?status=${statusFilter}` : ''}`),
  })
  const { data: aging = [] } = useQuery({
    queryKey: ['ar-aging'],
    queryFn: () => financeApi.get<AgingRow[]>('/ar/aging'),
    enabled: tab === 'aging',
  })

  const flash = (kind: 'ok' | 'err', text: string) => { setBanner({ kind, text }); setTimeout(() => setBanner(null), 5000) }
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['ar-invoices'] })
    qc.invalidateQueries({ queryKey: ['ar-aging'] })
  }

  const post = useMutation({
    mutationFn: (id: string) => financeApi.post(`/ar/invoices/${id}/post`, {}),
    onSuccess: () => { flash('ok', 'Revenue posted'); refresh() },
    onError: (e: Error) => flash('err', e.message),
  })
  const voidInv = useMutation({
    mutationFn: (id: string) => financeApi.post(`/ar/invoices/${id}/void`, {}),
    onSuccess: () => { flash('ok', 'Invoice voided'); refresh() },
    onError: (e: Error) => flash('err', e.message),
  })

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/ar"
      title="Accounts Receivable"
      subtitle="Customer invoices, revenue recognition, receipts, and aging"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center gap-2">
          {([['invoices', 'Invoices'], ['aging', 'Aging']] as const).map(([t, label]) => (
            <button key={t} onClick={() => setTab(t)}
                    className={cn('rounded-lg px-4 py-2 text-sm font-medium',
                      tab === t ? 'bg-[#085E5E] text-white'
                                : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50')}>
              {label}
            </button>
          ))}
        </div>

        {banner && (
          <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700')}>
            {banner.text}
          </div>
        )}

        {tab === 'invoices' ? (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
                      className={cn(inputCls, 'w-44')}>
                <option value="">All statuses</option>
                {STATUSES.map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
              </select>
              {canManage && (
                <button onClick={() => setNewInvoice(true)} className={cn(primaryBtn, 'ml-auto')}>
                  <Plus className="h-4 w-4" /> New Invoice
                </button>
              )}
            </div>

            <div className="overflow-hidden rounded-lg border border-neutral-200">
              <table className="w-full text-sm">
                <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                  <tr>
                    <th className="px-3 py-2 w-40">Invoice</th>
                    <th className="px-3 py-2">Customer</th>
                    <th className="px-3 py-2 w-24">Date</th>
                    <th className="px-3 py-2 w-24">Due</th>
                    <th className="px-3 py-2 w-32 text-right">Total</th>
                    <th className="px-3 py-2 w-32 text-right">Outstanding</th>
                    <th className="px-3 py-2 w-28">Status</th>
                    <th className="px-3 py-2 w-32" />
                  </tr>
                </thead>
                <tbody>
                  {isFetching && (
                    <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin" /></td></tr>
                  )}
                  {!isFetching && invoices.length === 0 && (
                    <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">No invoices.</td></tr>
                  )}
                  {invoices.map((inv, i) => {
                    const outstanding = Number(inv.total_amount) - Number(inv.paid_amount)
                    const open = inv.status === 'posted' || inv.status === 'partially_paid'
                    return (
                      <tr key={inv.id} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                        <td className="px-3 py-2 font-mono text-xs">{inv.invoice_number}</td>
                        <td className="px-3 py-2">{inv.customer_name}</td>
                        <td className="px-3 py-2 text-xs text-neutral-600">{inv.invoice_date}</td>
                        <td className="px-3 py-2 text-xs text-neutral-600">{inv.due_date}</td>
                        <td className="px-3 py-2 text-right font-mono">{fmtMoney(inv.total_amount, inv.currency)}</td>
                        <td className="px-3 py-2 text-right font-mono">{open ? fmtMoney(String(outstanding)) : '—'}</td>
                        <td className="px-3 py-2"><StatusPill status={inv.status} /></td>
                        <td className="px-3 py-2">
                          {canManage && (
                            <div className="flex justify-end gap-1.5">
                              {inv.status === 'draft' && (
                                <>
                                  <button onClick={() => post.mutate(inv.id)} title="Post (recognize revenue)"
                                          className="rounded p-1 text-neutral-400 hover:text-[#085E5E]">
                                    <Send className="h-4 w-4" /></button>
                                  <button onClick={() => voidInv.mutate(inv.id)} title="Void"
                                          className="rounded p-1 text-neutral-400 hover:text-red-600">
                                    <Trash2 className="h-4 w-4" /></button>
                                </>
                              )}
                              {open && (
                                <button onClick={() => setReceiptFor(inv)} title="Record receipt"
                                        className="rounded p-1 text-neutral-400 hover:text-[#085E5E]">
                                  <HandCoins className="h-4 w-4" /></button>
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
          </>
        ) : (
          <div className="overflow-hidden rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr>
                  <th className="px-3 py-2">Customer</th>
                  <th className="px-3 py-2 w-16">Ccy</th>
                  <th className="px-3 py-2 text-right">Current</th>
                  <th className="px-3 py-2 text-right">1-30</th>
                  <th className="px-3 py-2 text-right">31-60</th>
                  <th className="px-3 py-2 text-right">61-90</th>
                  <th className="px-3 py-2 text-right">90+</th>
                  <th className="px-3 py-2 text-right font-semibold">Total</th>
                </tr>
              </thead>
              <tbody>
                {aging.length === 0 && (
                  <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">No open receivables.</td></tr>
                )}
                {aging.map((r, i) => (
                  <tr key={`${r.customer_id}-${r.currency}`} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                    <td className="px-3 py-2">{r.customer_name}</td>
                    <td className="px-3 py-2 text-xs text-neutral-500">{r.currency}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.current)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d1_30)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d31_60)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d61_90)}</td>
                    <td className={cn('px-3 py-2 text-right font-mono', Number(r.d90_plus) > 0 && 'text-red-600')}>{fmtMoney(r.d90_plus)}</td>
                    <td className="px-3 py-2 text-right font-mono font-semibold">{fmtMoney(r.total, r.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {newInvoice && (
        <InvoiceModal onClose={() => setNewInvoice(false)}
          onSaved={() => { setNewInvoice(false); refresh(); flash('ok', 'Invoice created (draft)') }}
          onError={(m) => flash('err', m)} />
      )}
      {receiptFor && (
        <ReceiptModal invoice={receiptFor} onClose={() => setReceiptFor(null)}
          onSaved={(st) => { setReceiptFor(null); refresh(); flash('ok', `Receipt recorded — invoice ${st}`) }}
          onError={(m) => flash('err', m)} />
      )}
    </PortalChromeLayout>
  )
}

// ── New invoice modal ─────────────────────────────────────────────────────────────
interface DraftTaxLine { tax_code: string; taxable_base: string; tax_amount: string }

function InvoiceModal({ onClose, onSaved, onError }: {
  onClose: () => void; onSaved: () => void; onError: (m: string) => void
}) {
  const [customerId, setCustomerId] = useState('')
  const [invoiceDate, setInvoiceDate] = useState(new Date().toISOString().slice(0, 10))
  const [dueDate, setDueDate] = useState(new Date().toISOString().slice(0, 10))
  const [currency, setCurrency] = useState('CAD')
  const [amount, setAmount] = useState('')
  const [description, setDescription] = useState('')
  const [taxLines, setTaxLines] = useState<DraftTaxLine[]>([])
  const [busy, setBusy] = useState(false)

  const { data: customers } = useQuery({
    queryKey: ['ar-customers'],
    queryFn: () => mdmApi.get<{ items: Partner[] }>('/partners?role=customer&page_size=200'),
  })
  const { data: taxCodes = [] } = useQuery({
    queryKey: ['tax-codes'],
    queryFn: () => mdmApi.get<TaxCode[]>('/tax/codes'),
    staleTime: 10 * 60_000,
  })
  const customer = customers?.items.find((c) => c.id === customerId)

  const onPickCustomer = (id: string) => {
    setCustomerId(id)
    const c = customers?.items.find((x) => x.id === id)
    if (c) setCurrency(c.currency)
  }

  const taxTotal = useMemo(() => taxLines.reduce((s, t) => s + (Number(t.tax_amount) || 0), 0), [taxLines])
  const addTaxLine = () => setTaxLines((p) => [...p, { tax_code: '', taxable_base: amount || '', tax_amount: '' }])
  const setLine = (i: number, patch: Partial<DraftTaxLine>) =>
    setTaxLines((p) => p.map((l, idx) => idx === i ? { ...l, ...patch } : l))
  const onCodeChange = (i: number, code: string) => {
    const rate = Number(taxCodes.find((t) => t.code === code)?.rate ?? 0)
    const base = Number(taxLines[i].taxable_base) || 0
    setLine(i, { tax_code: code, tax_amount: (base * rate).toFixed(2) })
  }
  const onBaseChange = (i: number, base: string) => {
    const rate = Number(taxCodes.find((t) => t.code === taxLines[i].tax_code)?.rate ?? 0)
    setLine(i, { taxable_base: base, tax_amount: ((Number(base) || 0) * rate).toFixed(2) })
  }

  const save = async () => {
    if (!customerId || !customer) return onError('Pick a customer')
    if (!amount || Number(amount) <= 0) return onError('Enter a pre-tax amount')
    setBusy(true)
    try {
      await financeApi.post('/ar/invoices', {
        customer_id: customerId, customer_name: customer.name,
        invoice_date: invoiceDate, due_date: dueDate, currency,
        amount: Number(amount).toFixed(2), description: description || null,
        tax_lines: taxLines.filter((t) => t.tax_code).map((t) => ({
          tax_code: t.tax_code, taxable_base: Number(t.taxable_base || 0).toFixed(2),
          tax_amount: Number(t.tax_amount || 0).toFixed(2),
        })),
      })
      onSaved()
    } catch (e) { onError((e as Error).message) } finally { setBusy(false) }
  }

  const total = (Number(amount) || 0) + taxTotal

  return (
    <Modal title="New AR invoice" onClose={onClose} wide>
      <div className="grid grid-cols-2 gap-3">
        <label className="col-span-2 flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Customer</span>
          <select value={customerId} onChange={(e) => onPickCustomer(e.target.value)} className={inputCls}>
            <option value="">Select a customer…</option>
            {customers?.items.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.currency}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Invoice date</span>
          <input type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} className={inputCls} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Due date</span>
          <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} className={inputCls} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Currency</span>
          <select value={currency} onChange={(e) => setCurrency(e.target.value)} className={inputCls}>
            {['CAD', 'USD', 'CNY', 'EUR'].map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Pre-tax amount</span>
          <input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)}
                 placeholder="0.00" className={cn(inputCls, 'text-right font-mono')} />
        </label>
        <label className="col-span-2 flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Description</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} />
        </label>
      </div>

      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-medium text-neutral-700">Output tax lines</span>
          <button onClick={addTaxLine} className={secondaryBtn}><Plus className="h-4 w-4" /> Add</button>
        </div>
        {taxLines.length === 0 ? (
          <p className="text-xs text-neutral-400">No tax (zero-rated / exempt). Add a line for GST/HST.</p>
        ) : (
          <div className="space-y-2">
            {taxLines.map((t, i) => (
              <div key={i} className="grid grid-cols-[1fr_1fr_1fr_auto] items-end gap-2">
                <label className="flex flex-col gap-1 text-xs">
                  <span className="text-neutral-500">Tax code</span>
                  <select value={t.tax_code} onChange={(e) => onCodeChange(i, e.target.value)} className={inputCls}>
                    <option value="">—</option>
                    {taxCodes.map((tc) => <option key={`${tc.code}-${tc.rate}`} value={tc.code}>{tc.code} ({(Number(tc.rate) * 100).toFixed(0)}%)</option>)}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-xs">
                  <span className="text-neutral-500">Taxable base</span>
                  <input type="number" step="0.01" value={t.taxable_base}
                         onChange={(e) => onBaseChange(i, e.target.value)} className={cn(inputCls, 'text-right font-mono')} />
                </label>
                <label className="flex flex-col gap-1 text-xs">
                  <span className="text-neutral-500">Tax amount</span>
                  <input type="number" step="0.01" value={t.tax_amount}
                         onChange={(e) => setLine(i, { tax_amount: e.target.value })} className={cn(inputCls, 'text-right font-mono')} />
                </label>
                <button onClick={() => setTaxLines((p) => p.filter((_, idx) => idx !== i))}
                        className="mb-1 rounded p-1.5 text-neutral-400 hover:text-red-600"><X className="h-4 w-4" /></button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center justify-end gap-6 border-t border-neutral-100 pt-3 text-sm">
        <span className="text-neutral-500">Tax: <span className="font-mono">{fmtMoney(String(taxTotal))}</span></span>
        <span className="font-semibold">Total: <span className="font-mono">{fmtMoney(String(total), currency)}</span></span>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className={secondaryBtn}>Cancel</button>
        <button onClick={save} disabled={busy} className={primaryBtn}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />} Create Draft
        </button>
      </div>
    </Modal>
  )
}

// ── Receipt modal ─────────────────────────────────────────────────────────────────
function ReceiptModal({ invoice, onClose, onSaved, onError }: {
  invoice: Invoice; onClose: () => void; onSaved: (status: string) => void; onError: (m: string) => void
}) {
  const outstanding = (Number(invoice.total_amount) - Number(invoice.paid_amount)).toFixed(2)
  const [amount, setAmount] = useState(outstanding)
  const [receiptDate, setReceiptDate] = useState(new Date().toISOString().slice(0, 10))
  const [method, setMethod] = useState('bank_transfer')
  const [bankAccountId, setBankAccountId] = useState('')
  const [reference, setReference] = useState('')
  const [busy, setBusy] = useState(false)

  const { data: accounts = [] } = useQuery({
    queryKey: ['bank-accounts'],
    queryFn: () => financeApi.get<{ id: string; name: string; currency: string }[]>('/bank/accounts'),
  })
  const sameCcy = accounts.filter((a) => a.currency === invoice.currency)

  const save = async () => {
    if (!amount || Number(amount) <= 0) return onError('Enter a positive amount')
    setBusy(true)
    try {
      const r = await financeApi.post<{ invoice_status: string }>('/ar/receipts', {
        customer_id: invoice.customer_id, customer_name: invoice.customer_name,
        amount: Number(amount).toFixed(2), currency: invoice.currency, receipt_date: receiptDate,
        invoice_id: invoice.id, method, bank_account_id: bankAccountId || null,
        reference: reference || null,
      })
      onSaved(r.invoice_status)
    } catch (e) { onError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal title={`Record receipt — ${invoice.invoice_number}`} onClose={onClose}>
      <div className="mb-3 rounded-md bg-neutral-50 px-3 py-2 text-sm">
        {invoice.customer_name} · outstanding <span className="font-mono">{fmtMoney(outstanding, invoice.currency)}</span>
      </div>
      <div className="grid gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Amount received</span>
          <input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)}
                 className={cn(inputCls, 'text-right font-mono')} />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-neutral-600">Receipt date</span>
            <input type="date" value={receiptDate} onChange={(e) => setReceiptDate(e.target.value)} className={inputCls} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-neutral-600">Method</span>
            <select value={method} onChange={(e) => setMethod(e.target.value)} className={inputCls}>
              {['bank_transfer', 'cheque', 'cash', 'card', 'other'].map((m) => <option key={m} value={m}>{m.replace('_', ' ')}</option>)}
            </select>
          </label>
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Bank account (optional)</span>
          <select value={bankAccountId} onChange={(e) => setBankAccountId(e.target.value)} className={inputCls}>
            <option value="">—</option>
            {sameCcy.map((a) => <option key={a.id} value={a.id}>{a.name} · {a.currency}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-neutral-600">Reference (optional)</span>
          <input value={reference} onChange={(e) => setReference(e.target.value)} className={inputCls} />
        </label>
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose} className={secondaryBtn}>Cancel</button>
        <button onClick={save} disabled={busy} className={primaryBtn}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Record
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
