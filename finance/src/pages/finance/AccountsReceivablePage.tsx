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
import { SortTh, sortParams, useSort, useSortableRows } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi, mdmApi } from '@/lib/api'
import { cn, formatDate, todayISO } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { ListPager } from '@/components/ListPager'

const PAGE_SIZE = 50
/** GET /ar/invoices sort keys (finance-api ar._INVOICE_SORT). */
type ArSortKey = 'invoice_number' | 'customer_name' | 'invoice_date' | 'due_date'
  | 'total_amount' | 'outstanding' | 'status'

const inputCls = 'h-9 rounded-lg border border-line-strong bg-surface px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-primary-700 px-3 py-2 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm font-medium text-fg-secondary hover:bg-surface-subtle disabled:opacity-50'

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
    draft: 'bg-surface-sunken text-fg-secondary', posted: 'bg-info-soft text-info-fg',
    partially_paid: 'bg-warning-soft text-warning-fg', paid: 'bg-success-soft text-success-fg',
    void: 'bg-surface-sunken text-fg-muted',
  }
  return <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', map[status] ?? 'bg-surface-sunken text-fg-secondary')}>{status.replace('_', ' ')}</span>
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

  // Paged and sorted by the API; any filter or sort change goes back to page 1.
  const [page, setPage] = useState(1)
  const [sort, onSort] = useSort<ArSortKey>(null, () => setPage(1))
  const { data: invPage, isFetching } = useQuery({
    queryKey: ['ar-invoices', statusFilter, page, sort],
    queryFn: () => {
      const qs = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) })
      if (statusFilter) qs.set('status', statusFilter)
      const { sort: key, dir } = sortParams(sort)
      if (key && dir) { qs.set('sort', key); qs.set('dir', dir) }
      return financeApi.get<{ total: number; items: Invoice[] }>(`/ar/invoices?${qs}`)
    },
  })
  const invoices = invPage?.items ?? []
  const invTotal = invPage?.total ?? 0
  const { data: aging = [] } = useQuery({
    queryKey: ['ar-aging'],
    queryFn: () => financeApi.get<AgingRow[]>('/ar/aging'),
    enabled: tab === 'aging',
  })
  // Aging is one plain row per customer x currency, loaded whole.
  const agingSort = useSortableRows(aging, {
    customer: (r) => r.customer_name, currency: (r) => r.currency,
    current: (r) => Number(r.current), d1_30: (r) => Number(r.d1_30),
    d31_60: (r) => Number(r.d31_60), d61_90: (r) => Number(r.d61_90),
    d90_plus: (r) => Number(r.d90_plus), total: (r) => Number(r.total),
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
                      tab === t ? 'bg-primary-700 text-white'
                                : 'border border-line-strong bg-surface text-fg-secondary hover:bg-surface-subtle')}>
              {label}
            </button>
          ))}
        </div>

        {banner && (
          <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-danger-soft text-danger-fg' : 'bg-success-soft text-success-fg')}>
            {banner.text}
          </div>
        )}

        {tab === 'invoices' ? (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1) }}
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

            <div className="overflow-hidden rounded-lg border border-line">
              <table className="w-full text-sm">
                <thead className="bg-surface-subtle text-left text-xs text-fg-muted">
                  <tr>
                    <SortTh label="Invoice" sortKey="invoice_number" sort={sort} onSort={onSort} className="px-3 py-2 w-40" />
                    <SortTh label="Customer" sortKey="customer_name" sort={sort} onSort={onSort} className="px-3 py-2" />
                    <SortTh label="Date" sortKey="invoice_date" sort={sort} onSort={onSort} className="px-3 py-2 w-24" />
                    <SortTh label="Due" sortKey="due_date" sort={sort} onSort={onSort} className="px-3 py-2 w-24" />
                    <SortTh label="Total" sortKey="total_amount" sort={sort} onSort={onSort} align="right" className="px-3 py-2 w-32" />
                    <SortTh label="Outstanding" sortKey="outstanding" sort={sort} onSort={onSort} align="right" className="px-3 py-2 w-32" />
                    <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-3 py-2 w-28" />
                    <th className="px-3 py-2 w-32" />
                  </tr>
                </thead>
                <tbody>
                  {isFetching && (
                    <tr><td colSpan={8} className="px-3 py-6 text-center text-fg-muted">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin" /></td></tr>
                  )}
                  {!isFetching && invoices.length === 0 && (
                    <tr><td colSpan={8} className="px-3 py-6 text-center text-fg-muted">No invoices.</td></tr>
                  )}
                  {invoices.map((inv, i) => {
                    const outstanding = Number(inv.total_amount) - Number(inv.paid_amount)
                    const open = inv.status === 'posted' || inv.status === 'partially_paid'
                    return (
                      <tr key={inv.id} className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40')}>
                        <td className="px-3 py-2 font-mono text-xs">{inv.invoice_number}</td>
                        <td className="px-3 py-2">{inv.customer_name}</td>
                        <td className="px-3 py-2 text-xs text-fg-secondary">{formatDate(inv.invoice_date)}</td>
                        <td className="px-3 py-2 text-xs text-fg-secondary">{formatDate(inv.due_date)}</td>
                        <td className="px-3 py-2 text-right font-mono">{fmtMoney(inv.total_amount, inv.currency)}</td>
                        <td className="px-3 py-2 text-right font-mono">{open ? fmtMoney(String(outstanding)) : '—'}</td>
                        <td className="px-3 py-2"><StatusPill status={inv.status} /></td>
                        <td className="px-3 py-2">
                          {canManage && (
                            <div className="flex justify-end gap-1.5">
                              {inv.status === 'draft' && (
                                <>
                                  <button onClick={() => post.mutate(inv.id)} title="Post (recognize revenue)"
                                          className="rounded p-1 text-fg-muted hover:text-primary-700">
                                    <Send className="h-4 w-4" /></button>
                                  <button onClick={() => voidInv.mutate(inv.id)} title="Void"
                                          className="rounded p-1 text-fg-muted hover:text-danger-fg">
                                    <Trash2 className="h-4 w-4" /></button>
                                </>
                              )}
                              {open && (
                                <button onClick={() => setReceiptFor(inv)} title="Record receipt"
                                        className="rounded p-1 text-fg-muted hover:text-primary-700">
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
            <ListPager page={page} pageSize={PAGE_SIZE} total={invTotal} noun="invoices" onPage={setPage} />
          </>
        ) : (
          <div className="overflow-hidden rounded-lg border border-line">
            <table className="w-full text-sm">
              <thead className="bg-surface-subtle text-left text-xs text-fg-muted">
                <tr>
                  <SortTh label="Customer" sortKey="customer" sort={agingSort.sort} onSort={agingSort.onSort} className="px-3 py-2" />
                  <SortTh label="Ccy" sortKey="currency" sort={agingSort.sort} onSort={agingSort.onSort} className="px-3 py-2 w-16" />
                  <SortTh label="Current" sortKey="current" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="1-30" sortKey="d1_30" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="31-60" sortKey="d31_60" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="61-90" sortKey="d61_90" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="90+" sortKey="d90_plus" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="Total" sortKey="total" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2 font-semibold" />
                </tr>
              </thead>
              <tbody>
                {aging.length === 0 && (
                  <tr><td colSpan={8} className="px-3 py-6 text-center text-fg-muted">No open receivables.</td></tr>
                )}
                {agingSort.rows.map((r, i) => (
                  <tr key={`${r.customer_id}-${r.currency}`} className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40')}>
                    <td className="px-3 py-2">{r.customer_name}</td>
                    <td className="px-3 py-2 text-xs text-fg-muted">{r.currency}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.current)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d1_30)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d31_60)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d61_90)}</td>
                    <td className={cn('px-3 py-2 text-right font-mono', Number(r.d90_plus) > 0 && 'text-danger-fg')}>{fmtMoney(r.d90_plus)}</td>
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
  const [invoiceDate, setInvoiceDate] = useState(todayISO())
  const [dueDate, setDueDate] = useState(todayISO())
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
          <span className="text-fg-secondary">Customer</span>
          <select value={customerId} onChange={(e) => onPickCustomer(e.target.value)} className={inputCls}>
            <option value="">Select a customer…</option>
            {customers?.items.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.currency}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-fg-secondary">Invoice date</span>
          <input type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} className={inputCls} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-fg-secondary">Due date</span>
          <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} className={inputCls} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-fg-secondary">Currency</span>
          <select value={currency} onChange={(e) => setCurrency(e.target.value)} className={inputCls}>
            {['CAD', 'USD', 'CNY', 'EUR'].map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-fg-secondary">Pre-tax amount</span>
          <input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)}
                 placeholder="0.00" className={cn(inputCls, 'text-right font-mono')} />
        </label>
        <label className="col-span-2 flex flex-col gap-1 text-sm">
          <span className="text-fg-secondary">Description</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} />
        </label>
      </div>

      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-medium text-fg-secondary">Output tax lines</span>
          <button onClick={addTaxLine} className={secondaryBtn}><Plus className="h-4 w-4" /> Add</button>
        </div>
        {taxLines.length === 0 ? (
          <p className="text-xs text-fg-muted">No tax (zero-rated / exempt). Add a line for GST/HST.</p>
        ) : (
          <div className="space-y-2">
            {taxLines.map((t, i) => (
              <div key={i} className="grid grid-cols-[1fr_1fr_1fr_auto] items-end gap-2">
                <label className="flex flex-col gap-1 text-xs">
                  <span className="text-fg-muted">Tax code</span>
                  <select value={t.tax_code} onChange={(e) => onCodeChange(i, e.target.value)} className={inputCls}>
                    <option value="">—</option>
                    {taxCodes.map((tc) => <option key={`${tc.code}-${tc.rate}`} value={tc.code}>{tc.code} ({(Number(tc.rate) * 100).toFixed(0)}%)</option>)}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-xs">
                  <span className="text-fg-muted">Taxable base</span>
                  <input type="number" step="0.01" value={t.taxable_base}
                         onChange={(e) => onBaseChange(i, e.target.value)} className={cn(inputCls, 'text-right font-mono')} />
                </label>
                <label className="flex flex-col gap-1 text-xs">
                  <span className="text-fg-muted">Tax amount</span>
                  <input type="number" step="0.01" value={t.tax_amount}
                         onChange={(e) => setLine(i, { tax_amount: e.target.value })} className={cn(inputCls, 'text-right font-mono')} />
                </label>
                <button onClick={() => setTaxLines((p) => p.filter((_, idx) => idx !== i))}
                        className="mb-1 rounded p-1.5 text-fg-muted hover:text-danger-fg"><X className="h-4 w-4" /></button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center justify-end gap-6 border-t border-line-subtle pt-3 text-sm">
        <span className="text-fg-muted">Tax: <span className="font-mono">{fmtMoney(String(taxTotal))}</span></span>
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
  const [receiptDate, setReceiptDate] = useState(todayISO())
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
      <div className="mb-3 rounded-md bg-surface-subtle px-3 py-2 text-sm">
        {invoice.customer_name} · outstanding <span className="font-mono">{fmtMoney(outstanding, invoice.currency)}</span>
      </div>
      <div className="grid gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-fg-secondary">Amount received</span>
          <input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)}
                 className={cn(inputCls, 'text-right font-mono')} />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-fg-secondary">Receipt date</span>
            <input type="date" value={receiptDate} onChange={(e) => setReceiptDate(e.target.value)} className={inputCls} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-fg-secondary">Method</span>
            <select value={method} onChange={(e) => setMethod(e.target.value)} className={inputCls}>
              {['bank_transfer', 'cheque', 'cash', 'card', 'other'].map((m) => <option key={m} value={m}>{m.replace('_', ' ')}</option>)}
            </select>
          </label>
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-fg-secondary">Bank account (optional)</span>
          <select value={bankAccountId} onChange={(e) => setBankAccountId(e.target.value)} className={inputCls}>
            <option value="">—</option>
            {sameCcy.map((a) => <option key={a.id} value={a.id}>{a.name} · {a.currency}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-fg-secondary">Reference (optional)</span>
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
      <div className={cn('w-full rounded-xl bg-surface p-5 shadow-xl', wide ? 'max-w-2xl' : 'max-w-md')}
           onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-fg">{title}</h2>
          <button onClick={onClose} className="rounded p-1 text-fg-muted hover:text-fg-secondary">
            <X className="h-5 w-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
