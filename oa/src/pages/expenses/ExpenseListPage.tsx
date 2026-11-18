import { useState, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Plus, ChevronDown, Receipt, Car, Plane, FileText } from 'lucide-react'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { api } from '@/lib/api'
import { STATUS } from '@/lib/status'
import { Pagination } from '@/components/ui/Pagination'
import { StatusBadge } from '@/components/ui/badge'
import { Button, DropdownPortal, MenuItem, SortTh, TBody, THead, Table, Tabs, Td, Tr, useAnchorRect, useSort, Badge, type BadgeTone, Card } from '@uniops/shell'

type ExpenseSortKey = 'claim_number' | 'claim_type' | 'employee_name' | 'net_amount' | 'status' | 'date'

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

const TYPE_CONFIG: Record<string, { label: string; tone: BadgeTone }> = {
  EXP: { label: 'EXP',     tone: 'accent' },
  MIL: { label: 'MIL',     tone: 'warning' },
  TRV: { label: 'TRV',     tone: 'blue' },
}

function TypeBadge({ type }: { type: string }) {
  const cfg = TYPE_CONFIG[type] ?? { label: type, tone: 'neutral' as const }
  return <Badge size="sm" variant={cfg.tone}>{cfg.label}</Badge>
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
  const newMenuAnchorRef = useRef<HTMLDivElement>(null)
  const newMenuRect = useAnchorRect(newMenuOpen, newMenuAnchorRef)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  // Paged by expense-api, so the API sorts (null = its default, newest first).
  const [sort, onSort] = useSort<ExpenseSortKey>(null, () => setPage(1))

  const { data, isLoading } = useQuery<ExpenseList>({
    queryKey: ['expense-list', activeStatus, activeType, page, pageSize, sort],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (activeStatus !== 'all') qs.set('status', activeStatus)
      if (activeType) qs.set('type', activeType)
      if (sort) { qs.set('sort', sort.key); qs.set('dir', sort.dir) }
      // TRA (Travel Application) shares the expense_claims table but is not a
      // reimbursement — it has its own list at /travel and detail route
      // /travel/:id, and the New Claim menu above deliberately omits it. Without
      // this exclusion every TRA (including unsubmitted drafts) also showed up
      // here, and clicking one landed on the wrong detail page.
      qs.set('exclude_type', 'TRA')
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
          <h1 className="text-2xl font-semibold text-fg">Expense Claims</h1>
          <p className="mt-0.5 text-sm text-fg-muted">Expense, mileage, travel and custom-form reimbursements</p>
        </div>

        {/* New claim dropdown */}
        <div className="relative" ref={newMenuAnchorRef}>
          <Button size="md"
            aria-haspopup="menu"
            aria-expanded={newMenuOpen}
            onClick={() => setNewMenuOpen((v) => !v)}
           
          >
            <Plus className="h-4 w-4" />
            New Claim
            <ChevronDown className="h-3.5 w-3.5" />
          </Button>
          {newMenuOpen && newMenuRect && (
            <DropdownPortal anchorRect={newMenuRect} onClose={() => setNewMenuOpen(false)} align="end" width={208} className="py-1">
                <MenuItem
                  onClick={() => { setNewMenuOpen(false); navigate('/expenses/new/exp') }}
                 
                >
                  <Receipt className="h-4 w-4 text-primary-600" />
                  <span>General Expense (EXP)</span>
                </MenuItem>
                <MenuItem
                  onClick={() => { setNewMenuOpen(false); navigate('/expenses/new/mil') }}
                 
                >
                  <Car className="h-4 w-4 text-warning-fg" />
                  <span>Mileage Claim (MIL)</span>
                </MenuItem>
                <MenuItem
                  onClick={() => { setNewMenuOpen(false); navigate('/expenses/new/trv') }}
                 
                >
                  <Plane className="h-4 w-4 text-info-fg" />
                  <span>Travel Expense (TRV)</span>
                </MenuItem>
                {(customForms?.length ?? 0) > 0 && (
                  <>
                    <div className="my-1 border-t border-line-subtle" />
                    {customForms!.map((cf) => (
                      <MenuItem
                        key={cf.code}
                        onClick={() => { setNewMenuOpen(false); navigate(`/expenses/new/cfm/${cf.code}`) }}
                       
                      >
                        <FileText className="h-4 w-4 text-fg-muted" />
                        <span>{cf.name} (CFM)</span>
                      </MenuItem>
                    ))}
                  </>
                )}
            </DropdownPortal>
          )}
        </div>
      </div>

      {/* Filter tabs */}
      <Tabs
        items={TABS.map((s) => ({ key: s, label: s === 'all' ? 'All' : s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) }))}
        value={activeStatus}
        onChange={(s) => { setParams(s === 'all' ? {} : { status: s }); setPage(1) }}
      />

      {/* Table */}
      <Card className="overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-16 text-sm text-fg-muted">Loading…</div>
        ) : !data?.items.length ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-surface-sunken">
              <Receipt className="h-6 w-6 text-fg-muted" />
            </div>
            <p className="mt-3 font-medium text-fg-secondary">No expense claims</p>
            <p className="mt-1 text-sm text-fg-muted">Click "New Claim" to submit a reimbursement.</p>
          </div>
        ) : (
          <Table>
            <THead>
              <Tr>
                <SortTh label="Claim #" sortKey="claim_number" sort={sort} onSort={onSort} className="px-4 py-3 text-left font-medium text-fg-muted text-xs" />
                <SortTh label="Type" sortKey="claim_type" sort={sort} onSort={onSort} className="px-4 py-3 text-left font-medium text-fg-muted text-xs" />
                <SortTh label="Employee" sortKey="employee_name" sort={sort} onSort={onSort} className="px-4 py-3 text-left font-medium text-fg-muted text-xs" />
                <SortTh label="Net Amount" sortKey="net_amount" sort={sort} onSort={onSort} align="right" className="px-4 py-3 font-medium text-fg-muted text-xs" />
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-4 py-3 text-left font-medium text-fg-muted text-xs" />
                <SortTh label="Date" sortKey="date" sort={sort} onSort={onSort} className="px-4 py-3 text-left font-medium text-fg-muted text-xs" />
              </Tr>
            </THead>
            <TBody>
              {data.items.map((c, i) => (
                <Tr
                  key={c.id}
                  className={cn('border-b border-line-subtle hover:bg-surface-subtle cursor-pointer', i === data.items.length - 1 && 'border-b-0')}
                  onClick={() => navigate(`/expenses/${c.id}`)}
                >
                  <Td className="font-mono text-xs text-primary-600 font-medium">{c.claim_number}</Td>
                  <Td><TypeBadge type={c.claim_type} /></Td>
                  <Td className="text-fg-secondary">{c.employee_name}</Td>
                  <Td align="right" className="font-mono text-fg">{formatAmount(c.net_amount, c.currency)}</Td>
                  <Td>
                    <div className="flex items-center gap-1.5">
                      <StatusBadge status={c.status} />
                      {c.is_over_budget && (
                        <Badge variant="warning" size="sm">Over Budget</Badge>
                      )}
                    </div>
                  </Td>
                  <Td className="text-fg-muted">{formatDate(c.submitted_at ?? c.created_at)}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
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
      </Card>
    </div>
  )
}
