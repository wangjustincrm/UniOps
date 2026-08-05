import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Plus, ChevronDown, Receipt, Car, Plane, FileText } from 'lucide-react'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { api } from '@/lib/api'
import { STATUS } from '@/lib/status'
import { Pagination } from '@/components/ui/Pagination'
import { StatusBadge } from '@/components/ui/badge'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ExpenseClaim {
  id: string
  claim_number: string
  claim_type: 'EXP' | 'MIL' | 'TRV' | string
  employee_name: string
  department_name: string
  submission_date: string
  total_amount: number
  net_amount: number
  currency: string
  status: string
  is_over_budget: boolean
  submitted_at: string | null
  created_at: string
}

interface ExpenseList { items: ExpenseClaim[]; total: number }

interface CustomFormSummary { code: string; name: string; is_active: boolean }

// ── Badges ────────────────────────────────────────────────────────────────────

const TYPE_CONFIG: Record<string, { label: string; class: string }> = {
  EXP: { label: 'EXP',     class: 'bg-primary-50 text-primary-700' },
  MIL: { label: 'MIL',     class: 'bg-warning-50 text-warning-700' },
  TRV: { label: 'TRV',     class: 'bg-info-50 text-info-700' },
}

function TypeBadge({ type }: { type: string }) {
  const cfg = TYPE_CONFIG[type] ?? { label: type, class: 'bg-neutral-100 text-neutral-500' }
  return (
    <span className={cn('inline-flex rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide', cfg.class)}>
      {cfg.label}
    </span>
  )
}


// ── Filter tabs ───────────────────────────────────────────────────────────────

const TABS = ['all', STATUS.DRAFT, STATUS.SUBMITTED, STATUS.IN_REVIEW, STATUS.APPROVED, STATUS.PAID] as const

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ExpenseListPage() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const activeStatus = params.get('status') ?? 'all'
  const activeType = params.get('type') ?? ''
  const [newMenuOpen, setNewMenuOpen] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const { data, isLoading } = useQuery<ExpenseList>({
    queryKey: ['expense-list', activeStatus, activeType, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (activeStatus !== 'all') qs.set('status', activeStatus)
      if (activeType) qs.set('type', activeType)
      return api.get<ExpenseList>(`/api/v1/expenses?${qs}`)
    },
  })

  // Active custom forms power the "New Claim" dropdown's CFM entries.
  const { data: customForms } = useQuery<CustomFormSummary[]>({
    queryKey: ['custom-forms-active'],
    queryFn: () => api.get<CustomFormSummary[]>('/api/v1/expenses/custom-forms?active_only=true'),
  })

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Expense Claims</h1>
          <p className="mt-0.5 text-sm text-neutral-500">Expense, mileage, travel and custom-form reimbursements</p>
        </div>

        {/* New claim dropdown */}
        <div className="relative">
          <button
            onClick={() => setNewMenuOpen((v) => !v)}
            className="inline-flex items-center gap-2 rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800 transition-colors"
          >
            <Plus className="h-4 w-4" />
            New Claim
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
          {newMenuOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setNewMenuOpen(false)} />
              <div className="absolute right-0 top-full mt-1 z-20 w-52 rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
                <button
                  onClick={() => { setNewMenuOpen(false); navigate('/expenses/new/exp') }}
                  className="flex w-full items-center gap-3 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
                >
                  <Receipt className="h-4 w-4 text-primary-600" />
                  <span>General Expense (EXP)</span>
                </button>
                <button
                  onClick={() => { setNewMenuOpen(false); navigate('/expenses/new/mil') }}
                  className="flex w-full items-center gap-3 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
                >
                  <Car className="h-4 w-4 text-warning-600" />
                  <span>Mileage Claim (MIL)</span>
                </button>
                <button
                  onClick={() => { setNewMenuOpen(false); navigate('/expenses/new/trv') }}
                  className="flex w-full items-center gap-3 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
                >
                  <Plane className="h-4 w-4 text-info-600" />
                  <span>Travel Expense (TRV)</span>
                </button>
                {(customForms?.length ?? 0) > 0 && (
                  <>
                    <div className="my-1 border-t border-neutral-100" />
                    {customForms!.map((cf) => (
                      <button
                        key={cf.code}
                        onClick={() => { setNewMenuOpen(false); navigate(`/expenses/new/cfm/${cf.code}`) }}
                        className="flex w-full items-center gap-3 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
                      >
                        <FileText className="h-4 w-4 text-neutral-500" />
                        <span>{cf.name} (CFM)</span>
                      </button>
                    ))}
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 border-b border-neutral-200">
        {TABS.map((s) => (
          <button
            key={s}
            onClick={() => { setParams(s === 'all' ? {} : { status: s }); setPage(1) }}
            className={cn(
              'px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
              activeStatus === s
                ? 'border-primary-700 text-primary-700'
                : 'border-transparent text-neutral-500 hover:text-neutral-700',
            )}
          >
            {s === 'all' ? 'All' : s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-16 text-sm text-neutral-400">Loading…</div>
        ) : !data?.items.length ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-neutral-100">
              <Receipt className="h-6 w-6 text-neutral-400" />
            </div>
            <p className="mt-3 font-medium text-neutral-700">No expense claims</p>
            <p className="mt-1 text-sm text-neutral-400">Click "New Claim" to submit a reimbursement.</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Claim #</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Type</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Employee</th>
                <th className="px-4 py-3 text-right font-medium text-neutral-500 text-xs uppercase tracking-wide">Net Amount</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Status</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Date</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((c, i) => (
                <tr
                  key={c.id}
                  className={cn('border-b border-neutral-100 hover:bg-neutral-50 cursor-pointer', i === data.items.length - 1 && 'border-b-0')}
                  onClick={() => navigate(`/expenses/${c.id}`)}
                >
                  <td className="px-4 py-3 font-mono text-xs text-primary-600 font-medium">{c.claim_number}</td>
                  <td className="px-4 py-3"><TypeBadge type={c.claim_type} /></td>
                  <td className="px-4 py-3 text-neutral-700">{c.employee_name}</td>
                  <td className="px-4 py-3 text-right font-mono text-neutral-900">{formatAmount(c.net_amount, c.currency)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      <StatusBadge status={c.status} />
                      {c.is_over_budget && (
                        <span className="text-[10px] font-medium text-warning-600 bg-warning-50 rounded px-1 py-0.5">Over Budget</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-neutral-500">{formatDate(c.submitted_at ?? c.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {(data?.total ?? 0) > 0 && (
          <Pagination
            page={page}
            pageSize={pageSize}
            total={data!.total}
            onPageChange={setPage}
            onPageSizeChange={(s) => { setPageSize(s); setPage(1) }}
          />
        )}
      </div>
    </div>
  )
}
