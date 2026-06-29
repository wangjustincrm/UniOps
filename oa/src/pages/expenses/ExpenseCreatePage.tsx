import { useState, useMemo, useRef, useEffect, useLayoutEffect } from 'react'
import { createPortal } from 'react-dom'
import { useReplaceTab } from '@uniops/shell'
import { oaRoutes } from '@/app/routes'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Plus, Trash2, AlertTriangle, ChevronRight, ChevronLeft } from 'lucide-react'
import { cn, formatAmount } from '@/lib/utils'
import { api, budgetApi, mdmApi } from '@/lib/api'
import { ReceiptScanButton, type ReceiptFields } from '@/components/ReceiptScanButton'

// ── Types ─────────────────────────────────────────────────────────────────────

interface BudgetAccount {
  id: string
  code: string
  name: string
  l1_id: string
  l1_code: string
  l1_name: string
  annual_budget: number
  committed: number
  actual_spent: number
  available: number
}

interface LineItem {
  line_number: number
  expense_date: string
  description: string
  budget_account_id: string
  budget_account_code: string
  budget_account_name: string
  cost_center_id: string | null
  cost_center_name: string | null
  total_amount: string
  tax_amount: string
  net_amount: string
  tax_code: string | null
}

interface TaxCode { code: string; name: string; rate: string; recoverable: boolean }

interface Policy { hst_rate: number }

// ── Budget account picker ─────────────────────────────────────────────────────

interface L1Group {
  l1_id: string
  l1_code: string
  l1_name: string
  accounts: BudgetAccount[]
}

function AccountPicker({
  value, onChange, accounts,
}: {
  value: string
  onChange: (acc: BudgetAccount) => void
  accounts: BudgetAccount[]
}) {
  const [search, setSearch] = useState('')
  const [open, setOpen] = useState(false)
  // Two-level drill-down: pick an L1 category first, then an account within it.
  const [activeL1, setActiveL1] = useState<string | null>(null)
  const selected = accounts.find((a) => a.id === value)

  const l1Groups = useMemo<L1Group[]>(() => {
    const m = new Map<string, L1Group>()
    for (const a of accounts) {
      if (!m.has(a.l1_id)) {
        m.set(a.l1_id, { l1_id: a.l1_id, l1_code: a.l1_code, l1_name: a.l1_name, accounts: [] })
      }
      m.get(a.l1_id)!.accounts.push(a)
    }
    return Array.from(m.values()).sort((x, y) => x.l1_code.localeCompare(y.l1_code))
  }, [accounts])

  const activeGroup = l1Groups.find((g) => g.l1_id === activeL1) ?? null

  // The popover renders in a portal on <body> with fixed positioning so it can
  // escape the Line Items card (overflow-hidden) and the table's overflow-x-auto.
  const triggerRef = useRef<HTMLButtonElement>(null)
  const [pos, setPos] = useState<{ left: number; width: number; top?: number; bottom?: number } | null>(null)

  const reposition = () => {
    const el = triggerRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const width = 320
    const estH = 300
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8))
    // Flip above the trigger when there isn't room below.
    const openUp = r.bottom + 4 + estH > window.innerHeight && r.top > estH
    setPos(openUp
      ? { left, width, bottom: window.innerHeight - r.top + 4 }
      : { left, width, top: r.bottom + 4 })
  }

  const openPicker = () => {
    // Jump straight into the selected account's category when re-opening.
    setActiveL1(selected ? selected.l1_id : null)
    setSearch('')
    reposition()
    setOpen(true)
  }
  const close = () => { setOpen(false); setSearch('') }

  useLayoutEffect(() => { if (open) reposition() }, [open, activeL1])
  useEffect(() => {
    if (!open) return
    const handler = () => reposition()
    window.addEventListener('resize', handler)
    window.addEventListener('scroll', handler, true)
    return () => {
      window.removeEventListener('resize', handler)
      window.removeEventListener('scroll', handler, true)
    }
  }, [open])

  const q = search.toLowerCase()
  const filteredL1 = l1Groups.filter((g) => `${g.l1_code} ${g.l1_name}`.toLowerCase().includes(q))
  const filteredAccts = (activeGroup?.accounts ?? []).filter((a) =>
    `${a.code} ${a.name}`.toLowerCase().includes(q)
  )

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? close() : openPicker())}
        className={cn(
          'w-full rounded border px-2 py-1.5 text-left text-xs truncate',
          selected ? 'border-neutral-200 text-neutral-800' : 'border-neutral-200 text-neutral-400',
        )}
      >
        {selected ? `${selected.l1_code} › ${selected.code} — ${selected.name}` : 'Select account…'}
      </button>
      {open && pos && createPortal(
        <>
          <div className="fixed inset-0 z-40" onClick={close} />
          <div
            className="fixed z-50 rounded-lg border border-neutral-200 bg-white shadow-lg overflow-hidden"
            style={{ top: pos.top, bottom: pos.bottom, left: pos.left, width: pos.width }}
          >
            {/* Header: breadcrumb / back when inside an L1 */}
            <div className="border-b border-neutral-100 p-2">
              {activeGroup && (
                <button
                  type="button"
                  onClick={() => { setActiveL1(null); setSearch('') }}
                  className="mb-2 flex items-center gap-1 text-xs font-medium text-primary-600 hover:text-primary-700"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  <span className="truncate">{activeGroup.l1_name && activeGroup.l1_name !== activeGroup.l1_code ? `${activeGroup.l1_code} — ${activeGroup.l1_name}` : activeGroup.l1_code}</span>
                </button>
              )}
              <input
                autoFocus
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={activeGroup ? 'Search accounts…' : 'Search categories…'}
                className="w-full rounded border border-neutral-200 px-2 py-1.5 text-xs focus:outline-none focus:border-primary-400"
              />
            </div>

            <div className="max-h-48 overflow-y-auto">
              {/* Level 1: L1 categories */}
              {!activeGroup && filteredL1.map((g) => (
                <button
                  key={g.l1_id}
                  type="button"
                  onClick={() => { setActiveL1(g.l1_id); setSearch('') }}
                  className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-neutral-50 border-b border-neutral-50"
                >
                  <span className="text-xs font-medium text-neutral-800 truncate">
                    <span className="font-mono text-neutral-500">{g.l1_code}</span>{g.l1_name && g.l1_name !== g.l1_code ? <span className="ml-1">{g.l1_name}</span> : null}
                  </span>
                  <span className="flex items-center gap-1 shrink-0 text-[10px] text-neutral-400">
                    {g.accounts.length}
                    <ChevronRight className="h-3.5 w-3.5" />
                  </span>
                </button>
              ))}
              {!activeGroup && filteredL1.length === 0 && (
                <p className="py-4 text-center text-xs text-neutral-400">No categories found</p>
              )}

              {/* Level 2: accounts within the chosen L1 */}
              {activeGroup && filteredAccts.map((a) => {
                const pct = a.annual_budget > 0
                  ? Math.min(100, Math.round(((a.committed + a.actual_spent) / a.annual_budget) * 100))
                  : 0
                const isLow = a.available < 0
                return (
                  <button
                    key={a.id}
                    type="button"
                    onClick={() => { onChange(a); close() }}
                    className="flex w-full flex-col px-3 py-2 text-left hover:bg-neutral-50 border-b border-neutral-50"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-neutral-800">{a.code} — {a.name}</span>
                      <span className={cn('text-xs font-mono', isLow ? 'text-danger-600' : 'text-neutral-500')}>
                        {formatAmount(a.available)}
                      </span>
                    </div>
                    <div className="mt-1 h-1 w-full rounded-full bg-neutral-100">
                      <div
                        className={cn('h-1 rounded-full', pct >= 90 ? 'bg-danger-400' : pct >= 70 ? 'bg-warning-400' : 'bg-primary-400')}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <p className="mt-0.5 text-[10px] text-neutral-400">
                      Budget: {formatAmount(a.annual_budget)} · Used: {pct}%
                    </p>
                  </button>
                )
              })}
              {activeGroup && filteredAccts.length === 0 && (
                <p className="py-4 text-center text-xs text-neutral-400">No accounts found</p>
              )}
            </div>
          </div>
        </>,
        document.body,
      )}
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function today() {
  return new Date().toISOString().slice(0, 10)
}

function calcTax(total: string, rate: number): string {
  const a = parseFloat(total)
  if (isNaN(a) || a <= 0) return '0.00'
  return (Math.round(a * rate / (1 + rate) * 100) / 100).toFixed(2)
}

function calcNet(total: string, tax: string): string {
  const a = parseFloat(total), b = parseFloat(tax)
  if (isNaN(a)) return '0.00'
  return (a - (isNaN(b) ? 0 : b)).toFixed(2)
}

// Net-first model: user types the pre-tax Net, tax defaults to net × rate, total = net + tax.
function calcTaxFromNet(net: string, rate: number): string {
  const n = parseFloat(net)
  if (isNaN(n) || n <= 0) return '0.00'
  return (Math.round(n * rate * 100) / 100).toFixed(2)
}

function calcTotal(net: string, tax: string): string {
  const n = parseFloat(net), t = parseFloat(tax)
  if (isNaN(n)) return '0.00'
  return (n + (isNaN(t) ? 0 : t)).toFixed(2)
}

function emptyLine(n: number): LineItem {
  return {
    line_number: n,
    expense_date: today(),
    description: '',
    budget_account_id: '',
    budget_account_code: '',
    budget_account_name: '',
    cost_center_id: null,
    cost_center_name: null,
    total_amount: '',
    tax_amount: '',
    net_amount: '',
    tax_code: null,
  }
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ExpenseCreatePage() {
  const replaceTab = useReplaceTab(oaRoutes)

  const { data: taxCodes = [] } = useQuery<TaxCode[]>({
    queryKey: ['tax-codes'],
    queryFn: () => mdmApi.get<TaxCode[]>('/tax/codes'),
    staleTime: 10 * 60_000,
  })

  const { data: policy } = useQuery<Policy>({
    queryKey: ['expense-policy'],
    queryFn: () => api.get<Policy>('/api/v1/policy'),
  })
  // Budget catalog + per-account figures now live in budget-api (:8007); the old
  // expense-api /budget/accounts route was removed. /actuals/summary returns every
  // active account with annual_budget/committed/actual_spent/available for the year.
  const fiscalYear = new Date().getUTCFullYear()
  const { data: accounts = [] } = useQuery<BudgetAccount[]>({
    queryKey: ['budget-accounts', fiscalYear],
    queryFn: async () => {
      // /actuals/summary carries account figures + l1_id/l1_code; /l1 carries l1 names.
      const [res, l1s] = await Promise.all([
        budgetApi.get<{
          accounts: Array<{
            account_id: string; account_code: string; account_name: string
            l1_id: string; l1_code: string
            annual_budget: number; committed: number; actual_spent: number; available: number
          }>
        }>(`/actuals/summary?fiscal_year=${fiscalYear}`),
        budgetApi.get<Array<{ id: string; code: string; name: string }>>('/l1'),
      ])
      const l1NameById = new Map(l1s.map((l) => [l.id, l.name]))
      // Decimal columns arrive as JSON strings — coerce before arithmetic.
      return res.accounts.map((a) => ({
        id: a.account_id,
        code: a.account_code,
        name: a.account_name,
        l1_id: a.l1_id,
        l1_code: a.l1_code,
        l1_name: l1NameById.get(a.l1_id) ?? a.l1_code,
        annual_budget: Number(a.annual_budget),
        committed: Number(a.committed),
        actual_spent: Number(a.actual_spent),
        available: Number(a.available),
      }))
    },
  })

  // Decimal arrives as a JSON string — coerce so `1 + rate` is numeric, not string concat.
  // policy.hst_rate is now only the fallback for lines with no tax code selected;
  // the rate normally comes from the line's chosen mdm-api tax code.
  const hstRate = Number(policy?.hst_rate ?? 0.13)

  // Rate for a line's selected tax code (Finance Tax Settings / mdm-api).
  // No code selected → fall back to the org default policy rate.
  const rateForCode = (code: string | null): number => {
    const tc = taxCodes.find((t) => t.code === code)
    return tc ? Number(tc.rate) : hstRate
  }

  const [submissionDate, setSubmissionDate] = useState(today())
  const [currency, setCurrency] = useState('CAD')
  const [notes, setNotes] = useState('')
  const [lines, setLines] = useState<LineItem[]>([emptyLine(1)])

  const updateLine = (idx: number, patch: Partial<LineItem>) => {
    setLines((prev) => {
      const next = [...prev]
      next[idx] = { ...next[idx], ...patch }
      return next
    })
  }

  const handleNetChange = (idx: number, val: string) => {
    const tax = calcTaxFromNet(val, rateForCode(lines[idx].tax_code))
    const total = calcTotal(val, tax)
    updateLine(idx, { net_amount: val, tax_amount: tax, total_amount: total })
  }

  const handleTaxChange = (idx: number, val: string) => {
    const total = calcTotal(lines[idx].net_amount, val)
    updateLine(idx, { tax_amount: val, total_amount: total })
  }

  // Selecting a tax code re-derives the line's tax from net at that code's rate.
  const handleTaxCodeChange = (idx: number, code: string | null) => {
    const line = lines[idx]
    const tax = calcTaxFromNet(line.net_amount, rateForCode(code))
    const total = calcTotal(line.net_amount, tax)
    updateLine(idx, { tax_code: code, tax_amount: tax, total_amount: total })
  }

  const handleAccountSelect = (idx: number, acc: BudgetAccount) => {
    updateLine(idx, {
      budget_account_id: acc.id,
      budget_account_code: acc.code,
      budget_account_name: acc.name,
    })
  }

  const handleReceiptScan = (idx: number, f: ReceiptFields) => {
    const total = f.total_amount != null ? String(f.total_amount) : lines[idx].total_amount
    const tax = f.tax_amount != null ? String(f.tax_amount) : calcTax(total, hstRate)
    const net = calcNet(total, tax)
    updateLine(idx, {
      description: f.description || lines[idx].description,
      expense_date: f.date || lines[idx].expense_date,
      total_amount: total,
      tax_amount: tax,
      net_amount: net,
    })
  }

  const grandTotal = lines.reduce((s, l) => s + (parseFloat(l.total_amount) || 0), 0)
  const grandTax = lines.reduce((s, l) => s + (parseFloat(l.tax_amount) || 0), 0)
  const grandNet = lines.reduce((s, l) => s + (parseFloat(l.net_amount) || 0), 0)

  // Over-budget check
  const accountDeltas: Record<string, number> = {}
  for (const l of lines) {
    if (l.budget_account_id) {
      accountDeltas[l.budget_account_id] = (accountDeltas[l.budget_account_id] ?? 0) + (parseFloat(l.net_amount) || 0)
    }
  }
  const overBudgetLines = lines.filter((l) => {
    const acc = accounts.find((a) => a.id === l.budget_account_id)
    return acc && acc.available - (accountDeltas[acc.id] ?? 0) < 0
  })

  const createMutation = useMutation({
    mutationFn: (body: object) => api.post('/api/v1/expenses', body),
    onSuccess: (data: any) => replaceTab(`/expenses/${data.id}`),
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const validLines = lines.filter((l) => l.description && l.budget_account_id && l.net_amount)
    if (validLines.length === 0) return alert('Add at least one line item')

    const payload = validLines.map((l) => ({
      line_number: l.line_number,
      expense_date: l.expense_date,
      description: l.description,
      budget_account_id: l.budget_account_id,
      budget_account_code: l.budget_account_code,
      budget_account_name: l.budget_account_name,
      cost_center_id: l.cost_center_id,
      cost_center_name: l.cost_center_name,
      total_amount: parseFloat(l.total_amount).toFixed(2),
      tax_amount: parseFloat(l.tax_amount || '0').toFixed(2),
      net_amount: parseFloat(l.net_amount).toFixed(2),
      tax_code: l.tax_code,
    }))

    createMutation.mutate({
      claim_type: 'EXP',
      submission_date: submissionDate,
      currency,
      notes: notes || null,
      line_items: payload,
      trip_items: [],
    })
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6 max-w-6xl">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">New General Expense Claim</h1>
          <p className="mt-0.5 text-sm text-neutral-500">Receipt-based employee reimbursement</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => replaceTab('/expenses')}
            className="rounded-lg border border-neutral-200 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={createMutation.isPending}
            className="rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800 transition-colors disabled:opacity-50"
          >
            {createMutation.isPending ? 'Saving…' : 'Save as Draft'}
          </button>
        </div>
      </div>

      {/* Form header */}
      <div className="rounded-xl border border-neutral-200 bg-white p-5">
        <h2 className="mb-4 text-sm font-semibold text-neutral-700">Claim Details</h2>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Submission Date</label>
            <input
              type="date"
              value={submissionDate}
              onChange={(e) => setSubmissionDate(e.target.value)}
              required
              className="w-full rounded border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Currency</label>
            <select
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
              className="w-full rounded border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
            >
              <option>CAD</option>
              <option>USD</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Notes / Purpose</label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional"
              className="w-full rounded border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
            />
          </div>
        </div>
      </div>

      {/* Over-budget warning */}
      {overBudgetLines.length > 0 && (
        <div className="flex items-start gap-3 rounded-lg border border-warning-200 bg-warning-50 px-4 py-3 text-sm text-warning-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {overBudgetLines.length} line item{overBudgetLines.length > 1 ? 's' : ''} exceed the available budget.
            Finance Manager approval will be required.
          </span>
        </div>
      )}

      {/* Line items */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-neutral-700">Line Items</h2>
          <span className="text-xs text-neutral-400">Default rate: {(hstRate * 100).toFixed(0)}% · per-line tax code overrides</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 border-b border-neutral-100">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium text-neutral-500 w-8">#</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-neutral-500 w-32">Date</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-neutral-500">Description</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-neutral-500 w-64">Budget Account</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-neutral-500 w-28">Net (C)</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-neutral-500 w-28">Tax (B)</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-neutral-500 w-32">Tax Code</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-neutral-500 w-28">Total (A)</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {lines.map((line, idx) => {
                const acc = accounts.find((a) => a.id === line.budget_account_id)
                const isOverBudget = acc ? acc.available < (parseFloat(line.net_amount) || 0) : false
                return (
                  <tr
                    key={idx}
                    className={cn(
                      'border-b border-neutral-100',
                      isOverBudget && 'bg-warning-50/50',
                    )}
                  >
                    <td className="px-3 py-2 text-xs text-neutral-400 font-mono">{idx + 1}</td>
                    <td className="px-3 py-2">
                      <input
                        type="date"
                        value={line.expense_date}
                        onChange={(e) => updateLine(idx, { expense_date: e.target.value })}
                        className={cn('w-full rounded border px-2 py-1.5 text-xs focus:outline-none focus:border-primary-400', isOverBudget ? 'border-warning-300' : 'border-neutral-200')}
                      />
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-col gap-1">
                        <input
                          type="text"
                          value={line.description}
                          onChange={(e) => updateLine(idx, { description: e.target.value })}
                          placeholder="What was purchased"
                          className={cn('w-full rounded border px-2 py-1.5 text-xs focus:outline-none focus:border-primary-400', isOverBudget ? 'border-warning-300' : 'border-neutral-200')}
                        />
                        <ReceiptScanButton
                          className="self-start"
                          onScanned={(f) => handleReceiptScan(idx, f)}
                        />
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <AccountPicker
                        value={line.budget_account_id}
                        onChange={(acc) => handleAccountSelect(idx, acc)}
                        accounts={accounts}
                      />
                    </td>
                    <td className="px-3 py-2">
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={line.net_amount}
                        onChange={(e) => handleNetChange(idx, e.target.value)}
                        placeholder="0.00"
                        className={cn('w-full rounded border px-2 py-1.5 text-xs text-right font-mono focus:outline-none focus:border-primary-400', isOverBudget ? 'border-warning-300' : 'border-neutral-200')}
                      />
                    </td>
                    <td className="px-3 py-2">
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={line.tax_amount}
                        onChange={(e) => handleTaxChange(idx, e.target.value)}
                        placeholder="0.00"
                        className="w-full rounded border border-neutral-200 px-2 py-1.5 text-xs text-right font-mono focus:outline-none focus:border-primary-400"
                      />
                    </td>
                    <td className="px-3 py-2">
                      <select
                        value={line.tax_code ?? ''}
                        onChange={(e) => handleTaxCodeChange(idx, e.target.value || null)}
                        title="Tax code — drives the line's tax rate (Finance Tax Settings)"
                        className="w-full rounded border border-neutral-200 px-1.5 py-1.5 text-xs focus:outline-none focus:border-primary-400"
                      >
                        <option value="">— none —</option>
                        {taxCodes.map((tc) => (
                          <option key={`${tc.code}-${tc.rate}`} value={tc.code}>
                            {tc.code}{tc.recoverable ? '' : ' (NR)'}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-3 py-2">
                      <div className="rounded bg-neutral-50 px-2 py-1.5 text-xs text-right font-mono text-neutral-700">
                        {line.total_amount ? parseFloat(line.total_amount).toFixed(2) : '—'}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      {lines.length > 1 && (
                        <button
                          type="button"
                          onClick={() => setLines((prev) => prev.filter((_, i) => i !== idx).map((l, i) => ({ ...l, line_number: i + 1 })))}
                          className="rounded p-1 text-neutral-300 hover:text-danger-400 transition-colors"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>

            {/* Sub-total row */}
            <tfoot>
              <tr className="border-t-2 border-neutral-200 bg-neutral-50">
                <td colSpan={4} className="px-3 py-2 text-xs font-semibold text-neutral-700">Sub-total</td>
                <td className="px-3 py-2 text-right text-xs font-mono font-semibold text-primary-700">{grandNet.toFixed(2)}</td>
                <td className="px-3 py-2 text-right text-xs font-mono text-neutral-500">{grandTax.toFixed(2)}</td>
                <td />
                <td className="px-3 py-2 text-right text-xs font-mono font-semibold">{grandTotal.toFixed(2)}</td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>

        {/* Add row button */}
        {lines.length < 20 && (
          <div className="border-t border-neutral-100 p-3">
            <button
              type="button"
              onClick={() => setLines((prev) => [...prev, emptyLine(prev.length + 1)])}
              className="flex items-center gap-2 text-xs text-primary-600 hover:text-primary-700 font-medium"
            >
              <Plus className="h-3.5 w-3.5" />
              Add line item ({lines.length}/20)
            </button>
          </div>
        )}
      </div>

      {/* Error */}
      {createMutation.isError && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700">
          {(createMutation.error as Error).message}
        </div>
      )}
    </form>
  )
}
