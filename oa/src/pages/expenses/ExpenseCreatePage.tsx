import { useState, useEffect } from 'react'
import { useReplaceTab, Button } from '@uniops/shell'
import { oaRoutes } from '@/app/routes'
import { useQuery } from '@tanstack/react-query'
import { Plus, Trash2, AlertTriangle, Paperclip, X } from 'lucide-react'
import { cn, todayLocal } from '@/lib/utils'
import { useEditableClaim, useSaveClaim, type EditableFormProps } from '@/lib/editableClaim'
import { ErrorBanner } from '@/components/ui/ErrorBanner'
import { api, mdmApi } from '@/lib/api'
import { ReceiptScanButton, type ReceiptFields } from '@/components/ReceiptScanButton'
import { AccountPicker, useBudgetAccounts, type BudgetAccount } from '@/components/BudgetAccountPicker'
import { CardField } from '@/components/LineCardField'
import { INVALID_FIELD, isBlankLine, lineMessage, missingFields, type LineField } from '@/lib/lineValidation'

// ── Types ─────────────────────────────────────────────────────────────────────

interface LineItem {
  // Saved line's id when editing; sent back so the line keeps it and the
  // receipt scanned on it can be found by a rescan (ATT-OA-005).
  id?: string
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
  // Transient: the receipt scanned for this line, attached to the claim on save.
  _file?: File | null
}

interface TaxCode { code: string; name: string; rate: string; recoverable: boolean }

interface Policy { hst_rate: number }

// ── Helpers ───────────────────────────────────────────────────────────────────

// Local calendar date, not UTC — see todayLocal in lib/utils.
const today = todayLocal

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

// EXP-003 / OA §2.2: every line needs a description, an L2 budget account and a net amount.
const REQUIRED: LineField[] = ['description', 'budget_account_id', 'net_amount']

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ExpenseCreatePage({ editClaimId }: EditableFormProps = {}) {
  const replaceTab = useReplaceTab(oaRoutes)
  const { claim: editing, isLoading: loadingClaim, error: loadError } = useEditableClaim(editClaimId)

  const { data: taxCodes = [] } = useQuery<TaxCode[]>({
    queryKey: ['tax-codes'],
    queryFn: () => mdmApi.get<TaxCode[]>('/tax/codes'),
    staleTime: 10 * 60_000,
  })

  const { data: policy } = useQuery<Policy>({
    queryKey: ['expense-policy'],
    queryFn: () => api.get<Policy>('/api/v1/policy'),
  })
  const { data: accounts = [] } = useBudgetAccounts()

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
  // Set by a save refused for missing line fields; from then on the red
  // outlines and messages follow the form as the user fills it in.
  const [showLineErrors, setShowLineErrors] = useState(false)
  const [formError, setFormError] = useState('')

  // Fill the form from the claim being edited. Keyed on the claim id, not on
  // the object: react-query hands back a new object on every refetch, and
  // re-running this on each one would wipe out whatever the user had typed.
  useEffect(() => {
    if (!editing) return
    setSubmissionDate(editing.submission_date)
    setCurrency(editing.currency)
    setNotes(editing.notes ?? '')
    setLines(
      editing.line_items.length
        ? editing.line_items.map((li, i) => ({
            id: li.id,
            line_number: i + 1,
            expense_date: li.expense_date,
            description: li.description,
            budget_account_id: li.budget_account_id,
            budget_account_code: li.budget_account_code,
            budget_account_name: li.budget_account_name,
            cost_center_id: li.cost_center_id,
            cost_center_name: li.cost_center_name,
            total_amount: String(li.total_amount),
            tax_amount: String(li.tax_amount),
            net_amount: String(li.net_amount),
            tax_code: li.tax_code,
          }))
        : [emptyLine(1)],
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing?.id])

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

  // Blank lines are left out of the save; every other line is saved or the
  // save is refused — never dropped (ISS-0193).
  const keptLines = lines.filter((l) => !isBlankLine(l))
  const lineMissing = (l: LineItem): LineField[] =>
    showLineErrors && !isBlankLine(l) ? missingFields(l, REQUIRED) : []
  const lineIssues = showLineErrors
    ? lines.flatMap((l, idx) => {
        const missing = missingFields(l, REQUIRED)
        return !isBlankLine(l) && missing.length ? [lineMessage(`Line ${idx + 1}`, missing)] : []
      })
    : []

  // Receipts only of the lines actually saved, each tied to its saved line
  // (line_number = position among them, as in the payload). Every receipt on
  // this form is a Scan, so a rescan replaces the line's earlier scan.
  const createMutation = useSaveClaim(editClaimId, (id) => replaceTab(`/expenses/${id}`), {
    receipts: () => keptLines.flatMap((l, i) =>
      l._file ? [{ file: l._file, lineNumber: i + 1, source: 'scan' as const }] : []),
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setFormError('')
    if (keptLines.length === 0) {
      setShowLineErrors(false)
      setFormError('Add at least one line item')
      return
    }
    if (keptLines.some((l) => missingFields(l, REQUIRED).length)) {
      setShowLineErrors(true)
      return
    }

    const payload = keptLines.map((l, i) => ({
      id: l.id ?? null,
      line_number: i + 1,
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

  // One line's controls, used by both the desktop table row and the phone
  // card (ISS-0255) — one set of handlers, one red-outline rule (ISS-0193).
  const renderLine = (line: LineItem, idx: number) => {
    const missing = lineMissing(line)
    const acc = accounts.find((a) => a.id === line.budget_account_id)
    const isOverBudget = acc ? acc.available < (parseFloat(line.net_amount) || 0) : false
    return {
      isOverBudget,
      date: (
        <input
          type="date"
          value={line.expense_date}
          onChange={(e) => updateLine(idx, { expense_date: e.target.value })}
          className={cn('w-full rounded border px-2 py-1.5 text-xs focus:outline-none focus:border-primary-400', isOverBudget ? 'border-warning-300' : 'border-neutral-200')}
        />
      ),
      description: (
        <div className="flex flex-col gap-1">
          <input
            type="text"
            value={line.description}
            onChange={(e) => updateLine(idx, { description: e.target.value })}
            placeholder="What was purchased"
            aria-invalid={missing.includes('description') || undefined}
            className={cn('w-full rounded border px-2 py-1.5 text-xs focus:outline-none focus:border-primary-400', isOverBudget ? 'border-warning-300' : 'border-neutral-200', missing.includes('description') && INVALID_FIELD)}
          />
          <ReceiptScanButton
            className="self-start"
            onScanned={(f) => handleReceiptScan(idx, f)}
            onFile={(file) => updateLine(idx, { _file: file })}
          />
          {line._file && (
            <div
              className="flex items-center gap-1 text-[10px] text-neutral-500"
              title="Will be attached to the claim when you save"
            >
              <Paperclip className="h-3 w-3 shrink-0" />
              <span className="truncate max-w-[160px]">{line._file.name}</span>
              <button
                type="button"
                aria-label="Don't attach this receipt"
                onClick={() => updateLine(idx, { _file: null })}
                className="text-neutral-300 hover:text-danger-500"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          )}
        </div>
      ),
      account: (
        <AccountPicker
          value={line.budget_account_id}
          onChange={(acc) => handleAccountSelect(idx, acc)}
          accounts={accounts}
          invalid={missing.includes('budget_account_id')}
        />
      ),
      net: (
        <input
          type="number"
          step="0.01"
          min="0"
          value={line.net_amount}
          onChange={(e) => handleNetChange(idx, e.target.value)}
          placeholder="0.00"
          aria-invalid={missing.includes('net_amount') || undefined}
          className={cn('w-full rounded border px-2 py-1.5 text-xs text-right font-mono focus:outline-none focus:border-primary-400', isOverBudget ? 'border-warning-300' : 'border-neutral-200', missing.includes('net_amount') && INVALID_FIELD)}
        />
      ),
      tax: (
        <input
          type="number"
          step="0.01"
          min="0"
          value={line.tax_amount}
          onChange={(e) => handleTaxChange(idx, e.target.value)}
          placeholder="0.00"
          className="w-full rounded border border-neutral-200 px-2 py-1.5 text-xs text-right font-mono focus:outline-none focus:border-primary-400"
        />
      ),
      taxCode: (
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
      ),
      total: (
        <div className="rounded bg-neutral-50 px-2 py-1.5 text-xs text-right font-mono text-neutral-700">
          {line.total_amount ? parseFloat(line.total_amount).toFixed(2) : '—'}
        </div>
      ),
      remove: lines.length > 1 ? (
        <button
          type="button"
          aria-label={`Remove line ${idx + 1}`}
          onClick={() => setLines((prev) => prev.filter((_, i) => i !== idx).map((l, i) => ({ ...l, line_number: i + 1 })))}
          className="rounded p-1 text-neutral-300 hover:text-danger-400 transition-colors"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      ) : null,
    }
  }

  if (loadingClaim) {
    return <div className="p-8 text-sm text-neutral-500">Loading claim…</div>
  }
  if (loadError) {
    return <ErrorBanner message={loadError.message} />
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6 max-w-6xl">
      {/* Page header — wraps on a phone instead of squeezing the buttons (ISS-0224) */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">
            {editing ? `Edit ${editing.claim_number}` : 'New General Expense Claim'}
          </h1>
          <p className="mt-0.5 text-sm text-neutral-500">Receipt-based employee reimbursement</p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button type="button" variant="secondary" size="sm" className="whitespace-nowrap"
                  onClick={() => replaceTab(editing ? `/expenses/${editing.id}` : '/expenses')}>
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={createMutation.isPending} className="whitespace-nowrap">
            {createMutation.isPending ? 'Saving…' : editing ? 'Save Changes' : 'Save as Draft'}
          </Button>
        </div>
      </div>

      {/* Form header */}
      <div className="rounded-xl border border-neutral-200 bg-white p-5">
        <h2 className="mb-4 text-sm font-semibold text-neutral-700">Claim Details</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
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

      {/* Line items — a table from lg up; below that each line is a card with
          its fields stacked full width (ISS-0255). Both are built from the
          same renderLine() controls, so state and handlers are shared. */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-neutral-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-neutral-700">Line Items</h2>
          <span className="text-xs text-neutral-400">Default rate: {(hstRate * 100).toFixed(0)}% · per-line tax code overrides</span>
        </div>

        <div className="hidden lg:block overflow-x-auto">
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
                const f = renderLine(line, idx)
                return (
                  <tr
                    key={idx}
                    className={cn(
                      'border-b border-neutral-100',
                      f.isOverBudget && 'bg-warning-50/50',
                    )}
                  >
                    <td className="px-3 py-2 text-xs text-neutral-400 font-mono">{idx + 1}</td>
                    <td className="px-3 py-2">{f.date}</td>
                    <td className="px-3 py-2">{f.description}</td>
                    <td className="px-3 py-2">{f.account}</td>
                    <td className="px-3 py-2">{f.net}</td>
                    <td className="px-3 py-2">{f.tax}</td>
                    <td className="px-3 py-2">{f.taxCode}</td>
                    <td className="px-3 py-2">{f.total}</td>
                    <td className="px-3 py-2">{f.remove}</td>
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

        {/* Phone / tablet: one card per line */}
        <div className="flex flex-col gap-3 p-3 lg:hidden">
          {lines.map((line, idx) => {
            const f = renderLine(line, idx)
            return (
              <div
                key={idx}
                className={cn(
                  'rounded-lg border p-3',
                  f.isOverBudget ? 'border-warning-300 bg-warning-50/40' : 'border-neutral-200',
                )}
              >
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-semibold text-neutral-700">Line {idx + 1}</span>
                  {f.remove}
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <CardField label="Description" className="sm:col-span-2">{f.description}</CardField>
                  <CardField label="Date">{f.date}</CardField>
                  <CardField label="Budget Account">{f.account}</CardField>
                  <CardField label="Net (C)">{f.net}</CardField>
                  <CardField label="Tax (B)">{f.tax}</CardField>
                  <CardField label="Tax Code">{f.taxCode}</CardField>
                  <CardField label="Total (A)">{f.total}</CardField>
                </div>
              </div>
            )
          })}
          <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs">
            <div className="flex justify-between text-neutral-500">
              <span>Sub-total net (C)</span>
              <span className="font-mono font-semibold text-primary-700">{grandNet.toFixed(2)}</span>
            </div>
            <div className="flex justify-between text-neutral-500">
              <span>Tax (B)</span>
              <span className="font-mono">{grandTax.toFixed(2)}</span>
            </div>
            <div className="flex justify-between font-semibold text-neutral-700">
              <span>Total (A)</span>
              <span className="font-mono">{grandTotal.toFixed(2)}</span>
            </div>
          </div>
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

      {/* Missing line fields — one message per line, live until fixed */}
      {lineIssues.length > 0 && (
        <div role="alert" className="flex items-start gap-2 rounded-lg border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">Complete these lines before saving:</p>
            <ul className="mt-1 list-disc pl-5">
              {lineIssues.map((m) => <li key={m}>{m}</li>)}
            </ul>
          </div>
        </div>
      )}
      {formError && <ErrorBanner message={formError} />}

      {/* Error */}
      {createMutation.isError && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-700">
          {(createMutation.error as Error).message}
        </div>
      )}
    </form>
  )
}
