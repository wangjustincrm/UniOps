import { useState, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, X, Save, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { StatusBadge } from '@/components/ui/badge'
import { cn, formatDate } from '@/lib/utils'
import { usePlans, useCreatePlan, useDeletePlan } from '@/hooks/useBudget'
import { useCostCenters } from '@/hooks/useCostCenters'
import { useConfig } from '@/hooks/useConfig'
import { useAuthStore } from '@/stores/auth.store'
import type { DocumentStatus } from '@/types'

const STATUS_FILTER = [
  { value: 'all', label: 'All' },
  { value: 'draft', label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'in_review', label: 'In Review' },
  { value: 'approved', label: 'Approved' },
  { value: 'returned', label: 'Returned' },
  { value: 'rejected', label: 'Rejected' },
]

const currentYear = new Date().getUTCFullYear()
/** Fallback if CompanyConfig.budget_admin_config.available_fiscal_years is unset. */
const FALLBACK_YEAR_OPTIONS = [currentYear + 1, currentYear, currentYear - 1, currentYear - 2]

function useAvailableFiscalYears(): number[] {
  const { data: config } = useConfig()
  const stored = config?.budget_admin_config?.available_fiscal_years
  if (Array.isArray(stored) && stored.length > 0) {
    // Configured order is ascending; the dropdown / filter prefers newest first.
    return [...stored].sort((a, b) => b - a)
  }
  return FALLBACK_YEAR_OPTIONS
}

const WRITE_ROLES = new Set(['system_admin', 'finance_manager', 'finance_bp', 'dept_manager'])

export default function BudgetPlanListPage() {
  const navigate = useNavigate()
  const { user } = useAuthStore()
  const canCreate = !!user && WRITE_ROLES.has(user.role)
  const [filterStatus, setFilterStatus] = useState<string>('all')
  const [filterYear, setFilterYear] = useState<number | 'all'>(currentYear)
  const [filterCcId, setFilterCcId] = useState<string>('all')
  const [includeHistory, setIncludeHistory] = useState(false)
  const [showCreate, setShowCreate] = useState(false)

  const { data: ccData } = useCostCenters({ active_only: true })
  const costCenters = ccData ?? []
  const yearOptions = useAvailableFiscalYears()
  const deleteMut = useDeletePlan()

  const handleDelete = (e: React.MouseEvent, planId: string, label: string) => {
    e.stopPropagation()
    if (!confirm(`Delete draft plan "${label}"? This cannot be undone.`)) return
    deleteMut.mutate(planId, {
      onError: (err) => alert(err instanceof Error ? err.message : 'Delete failed'),
    })
  }

  const plansFilter = useMemo(() => {
    const f: { cost_center_id?: string; fiscal_year?: number; status?: string; include_history?: boolean } = {}
    if (filterCcId !== 'all') f.cost_center_id = filterCcId
    if (filterYear !== 'all') f.fiscal_year = filterYear
    if (filterStatus !== 'all') f.status = filterStatus
    if (includeHistory) f.include_history = true
    return f
  }, [filterStatus, filterYear, filterCcId, includeHistory])

  const { data: plans = [], isLoading } = usePlans(plansFilter)

  const ccById = useMemo(() => {
    const m = new Map<string, { code: string; name: string }>()
    for (const cc of costCenters) m.set(cc.id, { code: cc.code, name: cc.name })
    return m
  }, [costCenters])

  // Default sort: Cost Center code → fiscal year (desc) → version (desc).
  // Plans whose CC isn't in the cost center map sort last.
  const sortedPlans = useMemo(() => {
    return [...plans].sort((a, b) => {
      const codeA = ccById.get(a.cost_center_id)?.code ?? '￿'
      const codeB = ccById.get(b.cost_center_id)?.code ?? '￿'
      const byCc = codeA.localeCompare(codeB)
      if (byCc !== 0) return byCc
      if (a.fiscal_year !== b.fiscal_year) return b.fiscal_year - a.fiscal_year
      return b.version - a.version
    })
  }, [plans, ccById])

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Budget Plans</h1>
          <p className="text-sm text-neutral-500 mt-0.5">
            Annual budget plans per Cost Center · Fiscal Year
          </p>
        </div>
        {canCreate && (
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" /> New Plan
          </Button>
        )}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <select value={filterYear} onChange={(e) => setFilterYear(e.target.value === 'all' ? 'all' : Number(e.target.value))}
          className="h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm">
          <option value="all">All Years</option>
          {yearOptions.map((y) => <option key={y} value={y}>FY {y}</option>)}
        </select>
        <select value={filterCcId} onChange={(e) => setFilterCcId(e.target.value)}
          className="h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm">
          <option value="all">All Cost Centers</option>
          {costCenters.map((cc) => <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>)}
        </select>
        <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}
          className="h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm">
          {STATUS_FILTER.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <label className="ml-auto flex items-center gap-2 text-xs text-neutral-600 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={includeHistory}
            onChange={(e) => setIncludeHistory(e.target.checked)}
            className="accent-primary-600"
          />
          Show superseded versions
        </label>
      </div>

      {/* Plans table */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-neutral-50 border-b border-neutral-200">
            <tr>
              <Th>Cost Center</Th>
              <Th>Fiscal Year</Th>
              <Th>Version</Th>
              <Th>Status</Th>
              <Th>Submitted</Th>
              <Th>Approved</Th>
              <Th>Last Updated</Th>
              <Th className="w-24"></Th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={8} className="py-12 text-center text-sm text-neutral-400">Loading…</td></tr>
            ) : sortedPlans.length === 0 ? (
              <tr><td colSpan={8} className="py-12 text-center text-sm text-neutral-400">No plans match the current filters</td></tr>
            ) : (
              sortedPlans.map((p) => {
                const cc = ccById.get(p.cost_center_id)
                const isSuperseded = !p.is_current && p.status === 'approved'
                return (
                  <tr key={p.id}
                    className={cn(
                      'border-b border-neutral-100 cursor-pointer',
                      isSuperseded ? 'bg-neutral-50/60 text-neutral-500 hover:bg-neutral-100' : 'hover:bg-neutral-50',
                    )}
                    onClick={() => navigate(`/budget/plans/${p.id}`)}
                  >
                    <Td>
                      <span className="font-mono text-xs text-neutral-500 mr-2">{cc?.code ?? '—'}</span>
                      {cc?.name ?? p.cost_center_id}
                    </Td>
                    <Td>FY {p.fiscal_year}</Td>
                    <Td>
                      <span className="font-mono text-xs">v{p.version}</span>
                      {p.is_current ? (
                        <span className="ml-2 rounded-full bg-primary-50 text-primary-700 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide">
                          Current
                        </span>
                      ) : isSuperseded ? (
                        <span className="ml-2 rounded-full bg-neutral-100 text-neutral-500 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide">
                          Superseded
                        </span>
                      ) : null}
                    </Td>
                    <Td>
                      <StatusBadge status={p.status as DocumentStatus} />
                    </Td>
                    <Td>{p.submitted_at ? formatDate(p.submitted_at) : '—'}</Td>
                    <Td>{p.approved_at ? formatDate(p.approved_at) : '—'}</Td>
                    <Td className="text-neutral-500">{formatDate(p.updated_at)}</Td>
                    <Td className="text-right">
                      <div className="flex items-center justify-end gap-3">
                        {p.status === 'draft' && canCreate && (
                          <button
                            type="button"
                            onClick={(e) => handleDelete(
                              e, p.id,
                              `${cc?.code ?? p.cost_center_id} · FY ${p.fiscal_year} · v${p.version}`,
                            )}
                            disabled={deleteMut.isPending}
                            className="text-neutral-400 hover:text-danger-600 transition-colors disabled:opacity-40"
                            title="Delete draft"
                            aria-label="Delete draft plan"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                        <Link to={`/budget/plans/${p.id}`} className="text-primary-600 hover:underline text-xs">
                          Open →
                        </Link>
                      </div>
                    </Td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {showCreate && canCreate && (
        <CreatePlanModal onClose={() => setShowCreate(false)} />
      )}
    </div>
  )
}

// ── Create plan modal ──────────────────────────────────────────────────────

function CreatePlanModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const yearOptions = useAvailableFiscalYears()
  // Default to current year if it's in the options, otherwise the most recent
  // configured year — avoids submitting a year that's no longer offered.
  const defaultYear = yearOptions.includes(currentYear) ? currentYear : (yearOptions[0] ?? currentYear)
  const [ccId, setCcId] = useState('')
  const [year, setYear] = useState<number>(defaultYear)
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)
  const createMut = useCreatePlan()
  const { data: ccData } = useCostCenters({ active_only: true })
  const costCenters = ccData ?? []

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!ccId) { setError('Select a Cost Center'); return }
    setError(null)
    try {
      const plan = await createMut.mutateAsync({ cost_center_id: ccId, fiscal_year: year, notes: notes || undefined })
      navigate(`/budget/plans/${plan.id}`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Create failed')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-neutral-200">
          <h3 className="text-sm font-semibold">New Budget Plan</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-neutral-100"><X className="h-4 w-4" /></button>
        </div>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4 p-5">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Cost Center <span className="text-danger-600">*</span></label>
            <select value={ccId} onChange={(e) => setCcId(e.target.value)} required
              className="h-10 rounded-md border border-neutral-300 px-3 text-sm">
              <option value="">Select…</option>
              {costCenters.map((cc) => <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Fiscal Year <span className="text-danger-600">*</span></label>
            <select value={year} onChange={(e) => setYear(Number(e.target.value))}
              className="h-10 rounded-md border border-neutral-300 px-3 text-sm">
              {yearOptions.map((y) => <option key={y} value={y}>FY {y}</option>)}
            </select>
            {yearOptions.length === 0 && (
              <p className="text-[11px] text-amber-700">
                No fiscal years configured — ask an admin to add at least one year in Portal → Budget Config.
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Notes</label>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)}
              className="rounded-md border border-neutral-300 px-3 py-2 text-sm min-h-16" />
          </div>
          {error && <p className="text-sm text-danger-600">{error}</p>}
          <p className="text-xs text-neutral-400">
            Creating a plan will seed every active Account × 12 months with amount = 0.
            You can then enter monthly amounts in the grid editor.
          </p>
          <div className="flex items-center justify-end gap-2 pt-2 border-t border-neutral-100">
            <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={createMut.isPending}>
              <Save className="h-4 w-4" />
              {createMut.isPending ? 'Creating…' : 'Create & Open'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}

function Th({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <th className={cn('text-left px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-neutral-500', className)}>{children}</th>
}

function Td({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <td className={cn('px-4 py-2.5 text-sm', className)}>{children}</td>
}
