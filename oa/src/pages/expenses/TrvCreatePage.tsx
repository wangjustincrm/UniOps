import { useState } from 'react'
import { useReplaceTab } from '@uniops/shell'
import { oaRoutes } from '@/app/routes'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Plus, Trash2, AlertTriangle, ChevronDown, ChevronRight, ArrowLeft, Paperclip, Upload, X } from 'lucide-react'
import { cn, formatAmount } from '@/lib/utils'
import { api, budgetApi } from '@/lib/api'
import { ReceiptScanButton } from '@/components/ReceiptScanButton'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Policy {
  meal_breakfast_limit: number
  meal_lunch_limit: number
  meal_dinner_limit: number
  meal_incidental_limit: number
  hst_rate: number
}

interface BudgetAccount {
  id: string; code: string; name: string
  // Balance fields are now per-CC and fetched on demand via budget-api /balance.
  // For the budget account dropdown we only need the catalog (id/code/name).
  annual_budget?: number; committed?: number; actual_spent?: number; available?: number
}

interface TrvLineItem {
  line_number: number
  expense_date: string
  category: string        // Transportation | Hotel | Meals-Breakfast | Meals-Lunch | Meals-Dinner | Incidental
  description: string
  budget_account_id: string
  budget_account_code: string
  budget_account_name: string
  cost_center_id: string | null
  cost_center_name: string | null
  total_amount: string    // gross
  tax_amount: string
  net_amount: string      // after tax
  _file?: File | null     // transient: receipt/invoice held until the draft is created, then uploaded
}

// ── Constants ─────────────────────────────────────────────────────────────────

const TRV_CATEGORIES = [
  { key: 'Transportation', label: 'Transportation', mealKey: null },
  { key: 'Hotel',          label: 'Hotel / Accommodation', mealKey: null },
  { key: 'Meals-Breakfast',label: 'Meals — Breakfast', mealKey: 'meal_breakfast_limit' },
  { key: 'Meals-Lunch',    label: 'Meals — Lunch',     mealKey: 'meal_lunch_limit' },
  { key: 'Meals-Dinner',   label: 'Meals — Dinner',    mealKey: 'meal_dinner_limit' },
  { key: 'Incidental',     label: 'Incidental',        mealKey: 'meal_incidental_limit' },
  { key: 'Other',          label: 'Other',             mealKey: null },
]

function today() { return new Date().toISOString().slice(0, 10) }

function emptyLine(n: number, category: string): TrvLineItem {
  return {
    line_number: n, expense_date: today(), category,
    description: '', budget_account_id: '', budget_account_code: '',
    budget_account_name: '', cost_center_id: null, cost_center_name: null,
    total_amount: '', tax_amount: '0.00', net_amount: '', _file: null,
  }
}

// ── Over-limit badge ──────────────────────────────────────────────────────────

function OverLimitBadge({ amount, limit }: { amount: number; limit: number }) {
  if (!limit || amount <= limit) return null
  return (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-amber-100 text-amber-700">
      <AlertTriangle className="h-2.5 w-2.5" />
      Over limit (${limit.toFixed(2)})
    </span>
  )
}

// ── Category section ──────────────────────────────────────────────────────────

function CategorySection({
  category, label, lines, policy, accounts,
  onAdd, onRemove, onUpdate,
}: {
  category: string; label: string
  lines: TrvLineItem[]
  policy: Policy | undefined
  accounts: BudgetAccount[]
  onAdd: () => void
  onRemove: (n: number) => void
  onUpdate: (n: number, patch: Partial<TrvLineItem>) => void
}) {
  const [open, setOpen] = useState(true)
  const mealKey = TRV_CATEGORIES.find(c => c.key === category)?.mealKey as keyof Policy | null
  // Coerce at use-site: policy Decimals arrive as JSON strings, and the query-level
  // `select` is unreliable across the shared ['expense-policy'] cache key.
  const rawLimit = mealKey && policy ? policy[mealKey] : null
  const limit = rawLimit != null ? Number(rawLimit) : null

  return (
    <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="flex w-full items-center justify-between px-4 py-3 bg-neutral-50 border-b border-neutral-100 text-sm font-semibold text-neutral-700 hover:bg-neutral-100 transition-colors"
      >
        <span className="flex items-center gap-2">
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          {label}
          {lines.length > 0 && (
            <span className="ml-1 rounded-full bg-primary-100 px-2 py-0.5 text-xs font-bold text-primary-700">
              {lines.length}
            </span>
          )}
        </span>
        {limit && (
          <span className="text-xs font-normal text-neutral-400">
            Limit ${limit.toFixed(2)}/day
          </span>
        )}
      </button>

      {open && (
        <div className="p-4 flex flex-col gap-3">
          {lines.map((li) => {
            const net = parseFloat(li.net_amount) || 0
            const isOver = limit !== null && net > limit

            return (
              <div key={li.line_number}
                className={cn(
                  'rounded-lg border p-3 flex flex-col gap-2',
                  isOver ? 'border-amber-300 bg-amber-50/40' : 'border-neutral-200',
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  {isOver && <OverLimitBadge amount={net} limit={limit!} />}
                  <button type="button" onClick={() => onRemove(li.line_number)}
                    className="ml-auto text-neutral-300 hover:text-red-500 transition-colors">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <div>
                    <label className="text-[10px] font-medium text-neutral-500 uppercase tracking-wide">Date</label>
                    <input type="date" value={li.expense_date}
                      onChange={e => onUpdate(li.line_number, { expense_date: e.target.value })}
                      className="mt-0.5 w-full rounded border border-neutral-200 px-2 py-1.5 text-sm focus:outline-none focus:border-primary-400"
                    />
                  </div>
                  <div className="col-span-2 sm:col-span-2">
                    <div className="flex items-center justify-between">
                      <label className="text-[10px] font-medium text-neutral-500 uppercase tracking-wide">Description</label>
                      <ReceiptScanButton
                        onScanned={(f) => {
                          const hst = policy?.hst_rate ?? 0.13
                          const grossOrNet = f.total_amount
                          if (grossOrNet == null) {
                            onUpdate(li.line_number, {
                              description: f.description || li.description,
                              expense_date: f.date || li.expense_date,
                            })
                            return
                          }
                          const net = f.tax_amount != null ? grossOrNet - f.tax_amount : grossOrNet
                          const tax = (net * hst).toFixed(2)
                          const total = (net + parseFloat(tax)).toFixed(2)
                          onUpdate(li.line_number, {
                            description: f.description || li.description,
                            expense_date: f.date || li.expense_date,
                            net_amount: net.toFixed(2),
                            tax_amount: tax,
                            total_amount: total,
                          })
                        }}
                      />
                    </div>
                    <input value={li.description}
                      onChange={e => onUpdate(li.line_number, { description: e.target.value })}
                      placeholder="e.g. Taxi to airport"
                      className="mt-0.5 w-full rounded border border-neutral-200 px-2 py-1.5 text-sm focus:outline-none focus:border-primary-400"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] font-medium text-neutral-500 uppercase tracking-wide">Amount (net)</label>
                    <input type="number" min="0" step="0.01" value={li.net_amount}
                      onChange={e => {
                        const net = e.target.value
                        const tax = (parseFloat(net || '0') * (policy?.hst_rate ?? 0.13)).toFixed(2)
                        const total = (parseFloat(net || '0') + parseFloat(tax)).toFixed(2)
                        onUpdate(li.line_number, { net_amount: net, tax_amount: tax, total_amount: total })
                      }}
                      className={cn(
                        'mt-0.5 w-full rounded border px-2 py-1.5 text-sm text-right focus:outline-none focus:border-primary-400',
                        isOver ? 'border-amber-300 bg-amber-50' : 'border-neutral-200',
                      )}
                    />
                  </div>
                </div>

                {/* Budget account */}
                <div>
                  <label className="text-[10px] font-medium text-neutral-500 uppercase tracking-wide">Budget Account</label>
                  <select value={li.budget_account_id}
                    onChange={e => {
                      const acc = accounts.find(a => a.id === e.target.value)
                      onUpdate(li.line_number, {
                        budget_account_id: acc?.id ?? '',
                        budget_account_code: acc?.code ?? '',
                        budget_account_name: acc?.name ?? '',
                      })
                    }}
                    className="mt-0.5 w-full rounded border border-neutral-200 bg-white px-2 py-1.5 text-sm focus:outline-none focus:border-primary-400"
                  >
                    <option value="">— Select account —</option>
                    {accounts.map(a => (
                      <option key={a.id} value={a.id}>{a.code} — {a.name}</option>
                    ))}
                  </select>
                </div>

                {/* Receipt / invoice attachment for this row */}
                <div>
                  <label className="text-[10px] font-medium text-neutral-500 uppercase tracking-wide">Receipt / Invoice</label>
                  {li._file ? (
                    <div className="mt-0.5 flex items-center gap-2 rounded border border-neutral-200 bg-neutral-50 px-2 py-1.5 text-sm">
                      <Paperclip className="h-3.5 w-3.5 text-neutral-400 shrink-0" />
                      <span className="flex-1 truncate text-neutral-700">{li._file.name}</span>
                      <button type="button" onClick={() => onUpdate(li.line_number, { _file: null })}
                        className="text-neutral-300 hover:text-red-500 transition-colors" title="Remove attachment">
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ) : (
                    <label className="mt-0.5 flex cursor-pointer items-center gap-1.5 self-start rounded border border-dashed border-neutral-300 px-2 py-1.5 text-xs font-medium text-neutral-500 hover:border-primary-400 hover:text-primary-700 transition-colors w-fit">
                      <Upload className="h-3.5 w-3.5" />
                      Attach receipt
                      <input type="file" accept="image/*,.pdf" className="hidden"
                        onChange={(e) => {
                          const f = e.target.files?.[0]
                          if (f) onUpdate(li.line_number, { _file: f })
                          e.target.value = ''
                        }} />
                    </label>
                  )}
                </div>
              </div>
            )
          })}

          <button type="button" onClick={onAdd}
            className="flex items-center gap-1.5 self-start text-sm font-medium text-primary-700 hover:text-primary-900">
            <Plus className="h-4 w-4" /> Add {label} row
          </button>
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TrvCreatePage() {
  const replaceTab = useReplaceTab(oaRoutes)
  const [fromDate, setFromDate] = useState(today())
  const [toDate, setToDate] = useState(today())
  const [destination, setDestination] = useState('')
  const [purpose, setPurpose] = useState('')
  const [notes, setNotes] = useState('')
  const [currency, setCurrency] = useState('CAD')
  const [lines, setLines] = useState<TrvLineItem[]>([])
  const [error, setError] = useState('')

  // flat counter for unique line_numbers
  const [counter, setCounter] = useState(1)

  const { data: policy } = useQuery<Policy>({
    queryKey: ['expense-policy'],
    queryFn: () => api.get<Policy>('/api/v1/policy'),
    // Decimal fields arrive as JSON strings — coerce so .toFixed()/comparisons work.
    select: (p) => ({
      ...p,
      hst_rate: Number(p.hst_rate),
      meal_breakfast_limit: Number(p.meal_breakfast_limit),
      meal_lunch_limit: Number(p.meal_lunch_limit),
      meal_dinner_limit: Number(p.meal_dinner_limit),
      meal_incidental_limit: Number(p.meal_incidental_limit),
    }),
  })

  const { data: accountsData } = useQuery<{ items: BudgetAccount[] }>({
    queryKey: ['budget-accounts'],
    queryFn: async () => {
      const items = await budgetApi.get<BudgetAccount[]>('/accounts?is_active=true')
      return { items }
    },
  })
  const accounts = accountsData?.items ?? []

  const linesByCategory = (cat: string) => lines.filter(l => l.category === cat)

  const addLine = (cat: string) => {
    setLines(prev => [...prev, emptyLine(counter, cat)])
    setCounter(c => c + 1)
  }

  const removeLine = (n: number) => setLines(prev => prev.filter(l => l.line_number !== n))

  const updateLine = (n: number, patch: Partial<TrvLineItem>) =>
    setLines(prev => prev.map(l => l.line_number === n ? { ...l, ...patch } : l))

  const totalNet = lines.reduce((s, l) => s + (parseFloat(l.net_amount) || 0), 0)
  const totalTax = lines.reduce((s, l) => s + (parseFloat(l.tax_amount) || 0), 0)
  const totalGross = totalNet + totalTax

  const hasOverLimit = lines.some(l => {
    const mealKey = TRV_CATEGORIES.find(c => c.key === l.category)?.mealKey as keyof Policy | null
    if (!mealKey || !policy) return false
    return (parseFloat(l.net_amount) || 0) > (policy[mealKey] as number)
  })

  const mutation = useMutation({
    mutationFn: async (body: object) => {
      const claim = await api.post<{ id: string }>('/api/v1/expenses', body)
      // Upload per-row receipts to the freshly-created draft. Claim-level storage;
      // filename is prefixed with the category so the line association is readable.
      // Non-fatal: an upload failure must not lose the created claim.
      for (const l of lines) {
        if (!l._file) continue
        try {
          const form = new FormData()
          form.append('file', l._file, `[${l.category}] ${l._file.name}`)
          await api.postForm(`/api/v1/expenses/${claim.id}/attachments`, form)
        } catch {
          // swallow — surfaced on the detail page where the user can retry
        }
      }
      return claim
    },
    onSuccess: (data: any) => replaceTab(`/expenses/${data.id}`),
    onError: (e: any) => setError(e.message || 'Failed to create TRV claim'),
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!destination.trim()) { setError('Travel destination is required'); return }
    if (!purpose.trim()) { setError('Travel purpose is required'); return }
    if (lines.length === 0) { setError('Add at least one expense line'); return }
    if (lines.some(l => !l.budget_account_id)) { setError('All lines must have a budget account'); return }

    mutation.mutate({
      claim_type: 'TRV',
      submission_date: today(),
      currency,
      purpose,
      notes: notes || null,
      travel_from_date: fromDate,
      travel_to_date: toDate,
      travel_destination: destination,
      line_items: lines.map((l, i) => ({
        line_number: i + 1,
        expense_date: l.expense_date,
        description: `[${l.category}] ${l.description}`.trim(),
        budget_account_id: l.budget_account_id,
        budget_account_code: l.budget_account_code,
        budget_account_name: l.budget_account_name,
        cost_center_id: l.cost_center_id,
        cost_center_name: l.cost_center_name,
        total_amount: parseFloat(l.total_amount) || 0,
        tax_amount: parseFloat(l.tax_amount) || 0,
        net_amount: parseFloat(l.net_amount) || 0,
      })),
    })
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6 max-w-3xl">
      <div>
        <a href="/expenses" className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 mb-4">
          <ArrowLeft className="h-4 w-4" />Back to Expenses
        </a>
        <h1 className="text-2xl font-bold text-neutral-900">Travel Expense Claim</h1>
        <p className="mt-0.5 text-sm text-neutral-500">TRV — reimbursement for business travel</p>
      </div>

      {/* Over-limit warning banner */}
      {hasOverLimit && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 text-amber-500 shrink-0 mt-0.5" />
          <div className="text-sm text-amber-800">
            <strong>Meal limit exceeded.</strong>{' '}
            Highlighted rows exceed the policy per-meal allowance and will require Finance Manager approval.
          </div>
        </div>
      )}

      {/* Trip header */}
      <div className="rounded-xl border border-neutral-200 bg-white p-5 flex flex-col gap-4">
        <h2 className="text-sm font-semibold text-neutral-700">Trip Details</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">From Date *</label>
            <input type="date" required value={fromDate} onChange={e => setFromDate(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">To Date *</label>
            <input type="date" required value={toDate} onChange={e => setToDate(e.target.value)}
              min={fromDate}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Destination *</label>
            <input required value={destination} onChange={e => setDestination(e.target.value)}
              placeholder="e.g. Toronto, ON"
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Currency</label>
            <select value={currency} onChange={e => setCurrency(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm focus:outline-none focus:border-primary-400">
              <option value="CAD">CAD</option>
              <option value="USD">USD</option>
            </select>
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs font-medium text-neutral-600">Business Purpose *</label>
            <input required value={purpose} onChange={e => setPurpose(e.target.value)}
              placeholder="e.g. Client meeting, conference attendance…"
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400" />
          </div>
        </div>
      </div>

      {/* Expense lines by category */}
      {TRV_CATEGORIES.map(({ key, label }) => (
        <CategorySection
          key={key}
          category={key} label={label}
          lines={linesByCategory(key)}
          policy={policy}
          accounts={accounts}
          onAdd={() => addLine(key)}
          onRemove={removeLine}
          onUpdate={updateLine}
        />
      ))}

      {/* Notes */}
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Additional Notes</label>
        <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2}
          className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 resize-none" />
      </div>

      {/* Summary */}
      <div className="rounded-xl border border-neutral-200 bg-white p-4 space-y-1.5 text-sm">
        <div className="flex justify-between text-neutral-500">
          <span>Subtotal (net)</span>
          <span className="font-mono">{formatAmount(totalNet, currency)}</span>
        </div>
        <div className="flex justify-between text-neutral-500">
          <span>Tax (HST {policy ? `${(policy.hst_rate * 100).toFixed(0)}%` : ''})</span>
          <span className="font-mono">{formatAmount(totalTax, currency)}</span>
        </div>
        <div className="flex justify-between font-semibold border-t border-neutral-100 pt-2">
          <span>Total Claim</span>
          <span className="font-mono text-primary-700">{formatAmount(totalGross, currency)}</span>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
          <AlertTriangle className="h-4 w-4 shrink-0" />{error}
        </div>
      )}

      <button type="submit" disabled={mutation.isPending}
        className="flex items-center justify-center gap-2 rounded-lg bg-[#085E5E] px-6 py-2.5 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50 transition-colors self-start">
        {mutation.isPending ? 'Saving…' : 'Save Draft'}
      </button>
    </form>
  )
}
