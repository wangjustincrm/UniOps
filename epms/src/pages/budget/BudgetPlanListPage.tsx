import { useState, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, Save, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StatusBadge, statusLabel } from '@/components/ui/badge'
import { Checkbox, Modal, Select, SortTh, TBody, THead, Table, TableMessage, Td, Textarea, Th, Tr, useSortableRows } from '@uniops/shell'
import { cn, formatDate, todayISO } from '@/lib/utils'
import { usePlans, useCreatePlan, useDeletePlan, useAvailableFiscalYears } from '@/hooks/useBudget'
import { useCostCenters } from '@/hooks/useCostCenters'
import { useAuthStore } from '@/stores/auth.store'
import { useConfirmDialog } from '@/components/ui/ConfirmDialog'
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

/** The plant's calendar year (company time zone), read when used. */
const currentYear = () => Number(todayISO().slice(0, 4))

const WRITE_ROLES = new Set(['system_admin', 'finance_manager', 'finance_bp', 'dept_manager'])

export default function BudgetPlanListPage() {
  const navigate = useNavigate()
  const { user } = useAuthStore()
  const canCreate = !!user && WRITE_ROLES.has(user.role)
  const [filterStatus, setFilterStatus] = useState<string>('all')
  const [filterYear, setFilterYear] = useState<number | 'all'>(currentYear())
  const [filterCcId, setFilterCcId] = useState<string>('all')
  const [includeHistory, setIncludeHistory] = useState(false)
  const [showCreate, setShowCreate] = useState(false)

  const { data: ccData } = useCostCenters({ active_only: true })
  const costCenters = ccData ?? []
  const yearOptions = useAvailableFiscalYears()
  const deleteMut = useDeletePlan()
  const confirmDlg = useConfirmDialog()

  const handleDelete = (e: React.MouseEvent, planId: string, label: string) => {
    e.stopPropagation()
    // Asked in-page; a refusal shows in the dialog (both were browser pop-ups).
    confirmDlg.ask({
      title: 'Delete Draft Plan',
      subtitle: label,
      message: `Delete draft plan "${label}"? This cannot be undone.`,
      confirmLabel: 'Delete',
      failureFallback: 'Delete failed',
      onConfirm: () => deleteMut.mutateAsync(planId),
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
  // Click-to-sort on top of that default (the plan list is loaded whole).
  const { rows: shownPlans, sort, onSort } = useSortableRows(sortedPlans, {
    cost_center: (p) => ccById.get(p.cost_center_id)?.code,
    fiscal_year: (p) => p.fiscal_year,
    version: (p) => p.version,
    status: (p) => statusLabel(p.status),
    submitted_at: (p) => p.submitted_at,
    approved_at: (p) => p.approved_at,
    updated_at: (p) => p.updated_at,
  })

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-fg">Budget Plans</h1>
          <p className="text-sm text-fg-muted mt-0.5">
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
        <Select selectSize="sm" value={filterYear} onChange={(e) => setFilterYear(e.target.value === 'all' ? 'all' : Number(e.target.value))}
         >
          <option value="all">All Years</option>
          {yearOptions.map((y) => <option key={y} value={y}>FY {y}</option>)}
        </Select>
        <Select selectSize="sm" value={filterCcId} onChange={(e) => setFilterCcId(e.target.value)}
         >
          <option value="all">All Cost Centers</option>
          {costCenters.map((cc) => <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>)}
        </Select>
        <Select selectSize="sm" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}
         >
          {STATUS_FILTER.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </Select>
        <label className="ml-auto flex items-center gap-2 text-xs text-fg-secondary cursor-pointer select-none">
          <Checkbox
           
            checked={includeHistory}
            onChange={(e) => setIncludeHistory(e.target.checked)}
           
          />
          Show superseded versions
        </label>
      </div>

      {/* Plans table */}
      <div className="rounded-xl border border-line bg-surface overflow-hidden">
        <Table>
          <THead>
            <Tr>
              <SortTh label="Cost Center" sortKey="cost_center" sort={sort} onSort={onSort} />
              <SortTh label="Fiscal Year" sortKey="fiscal_year" sort={sort} onSort={onSort} />
              <SortTh label="Version" sortKey="version" sort={sort} onSort={onSort} />
              <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} />
              <SortTh label="Submitted" sortKey="submitted_at" sort={sort} onSort={onSort} />
              <SortTh label="Approved" sortKey="approved_at" sort={sort} onSort={onSort} />
              <SortTh label="Last Updated" sortKey="updated_at" sort={sort} onSort={onSort} />
              <Th className="w-24"></Th>
            </Tr>
          </THead>
          <TBody>
            {isLoading ? (
              <TableMessage colSpan={8} className="py-12">Loading…</TableMessage>
            ) : shownPlans.length === 0 ? (
              <TableMessage colSpan={8} className="py-12">No plans match the current filters</TableMessage>
            ) : (
              shownPlans.map((p) => {
                const cc = ccById.get(p.cost_center_id)
                const isSuperseded = !p.is_current && p.status === 'approved'
                return (
                  <Tr key={p.id}
                    className={cn(
                      'border-b border-line-subtle cursor-pointer',
                      isSuperseded ? 'bg-surface-subtle/60 text-fg-muted hover:bg-surface-sunken' : 'hover:bg-surface-subtle',
                    )}
                    onClick={() => navigate(`/budget/plans/${p.id}`)}
                  >
                    <Td>
                      <span className="font-mono text-xs text-fg-muted mr-2">{cc?.code ?? '—'}</span>
                      {cc?.name ?? p.cost_center_id}
                    </Td>
                    <Td>FY {p.fiscal_year}</Td>
                    <Td>
                      <span className="font-mono text-xs">v{p.version}</span>
                      {p.is_current ? (
                        <span className="ml-2 rounded-full bg-primary-50 text-primary-700 px-2 py-0.5 text-2xs font-semibold">
                          Current
                        </span>
                      ) : isSuperseded ? (
                        <span className="ml-2 rounded-full bg-surface-sunken text-fg-muted px-2 py-0.5 text-2xs font-semibold">
                          Superseded
                        </span>
                      ) : null}
                    </Td>
                    <Td>
                      <StatusBadge status={p.status as DocumentStatus} />
                    </Td>
                    <Td>{p.submitted_at ? formatDate(p.submitted_at) : '—'}</Td>
                    <Td>{p.approved_at ? formatDate(p.approved_at) : '—'}</Td>
                    <Td className="text-fg-muted">{formatDate(p.updated_at)}</Td>
                    <Td className="text-right">
                      <div className="flex items-center justify-end gap-3">
                        {p.status === 'draft' && canCreate && (
                          <Button variant="danger-ghost" size="icon-xs"
                            type="button"
                            onClick={(e) => handleDelete(
                              e, p.id,
                              `${cc?.code ?? p.cost_center_id} · FY ${p.fiscal_year} · v${p.version}`,
                            )}
                            disabled={deleteMut.isPending}
                           
                            title="Delete draft"
                            aria-label="Delete draft plan"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        )}
                        <Link to={`/budget/plans/${p.id}`} className="text-primary-600 hover:underline text-xs">
                          Open →
                        </Link>
                      </div>
                    </Td>
                  </Tr>
                )
              })
            )}
          </TBody>
        </Table>
      </div>

      {confirmDlg.dialog}
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
  const defaultYear = yearOptions.includes(currentYear()) ? currentYear() : (yearOptions[0] ?? currentYear())
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
    <Modal
      open
      onClose={onClose}
      // Backdrop closes, Escape did not: kept.
      closeOnBackdrop
      closeOnEscape={false}
      size="full"
      className="max-w-md"
      title="New Budget Plan"
      bodyClassName="p-0"
    >
        <form onSubmit={handleSubmit} className="flex flex-col gap-4 p-5">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">Cost Center <span className="text-danger-fg">*</span></label>
            <Select value={ccId} onChange={(e) => setCcId(e.target.value)} required
             >
              <option value="">Select…</option>
              {costCenters.map((cc) => <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>)}
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">Fiscal Year <span className="text-danger-fg">*</span></label>
            <Select value={year} onChange={(e) => setYear(Number(e.target.value))}
             >
              {yearOptions.map((y) => <option key={y} value={y}>FY {y}</option>)}
            </Select>
            {yearOptions.length === 0 && (
              <p className="text-2xs text-warning-fg">
                No fiscal years configured — ask an admin to add at least one year in Portal → Budget Config.
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">Notes</label>
            <Textarea value={notes} onChange={(e) => setNotes(e.target.value)}
              className="min-h-16" />
          </div>
          {error && <p className="text-sm text-danger-fg">{error}</p>}
          <p className="text-xs text-fg-muted">
            Creating a plan will seed every active Account × 12 months with amount = 0.
            You can then enter monthly amounts in the grid editor.
          </p>
          <div className="flex items-center justify-end gap-2 pt-2 border-t border-line-subtle">
            <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={createMut.isPending}>
              <Save className="h-4 w-4" />
              {createMut.isPending ? 'Creating…' : 'Create & Open'}
            </Button>
          </div>
        </form>
    </Modal>
  )
}

