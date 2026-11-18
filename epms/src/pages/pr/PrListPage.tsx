import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Search, Plus, Filter, Copy } from 'lucide-react'
import { SortTh, sortParams, useSort, Checkbox, Select, TBody, THead, Table, Td, Th, Tr } from '@uniops/shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { StatusBadge } from '@/components/ui/badge'
import { CurrentStepHint } from '@/components/ui/CurrentStepHint'
import { Pagination } from '@/components/ui/Pagination'
import { formatCAD, formatDate } from '@/lib/utils'
import { SkeletonRow } from '@/components/ui/skeleton'
import { usePrs } from '@/hooks/usePrs'
import { useQuery } from '@tanstack/react-query'
import { userService } from '@/services/users'
import { useRolePermissions, useScopedDepartments } from '@/hooks/useConfig'
import { useAuthStore } from '@/stores/auth.store'

const TYPE_LABELS: Record<number, string> = {
  1: 'Raw Mat./Pack.',
  2: 'Consumables',
  3: 'Spare Parts',
  4: 'Service',
  5: 'Fixed Asset',
  6: 'Project',
}

const STATUS_FILTER_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'all', label: 'All Statuses' },
  { value: 'draft', label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'in_review', label: 'In Review' },
  { value: 'approved', label: 'Approved' },
  { value: 'returned', label: 'Returned' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'cancelled', label: 'Cancelled' },
]

// GET /pr sort keys (epms-api crud/pr.py SORTABLE). Status sorts status →
// current approval step (chain order) → approver, across all pages.
type SortField = 'number' | 'title' | 'created_by_name' | 'vendor_name' | 'amount' | 'type' | 'is_prepaid' | 'status' | 'submitted_at'

export default function PrListPage() {
  const { user } = useAuthStore()
  // Effective roles = primary JWT role ∪ Role-Management assignments (a Requester
  // who is ALSO a Department Admin must be able to pick other requesters). Fall
  // back to the JWT role until the permissions query resolves.
  const roles = useRolePermissions().data?.roles ?? (user?.role ? [user.role] : [])
  const isRequesterOnly = roles.length > 0 && roles.every((r) => r === 'requester')

  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [deptFilter, setDeptFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [prepaidFilter, setPrepaidFilter] = useState('all')
  // Requester filter: a pure requester is locked to their own PRs; anyone who can
  // see others' PRs (Department Admin, procurement, finance, …) can pick one.
  const [requesterFilter, setRequesterFilter] = useState<string>('all')
  const [page, setPage] = useState(1)
  // Paged by the API, so the API sorts; a new order starts again at page 1.
  // Opens on Submitted ↓ (newest submissions first; drafts with no submitted date last).
  const [sort, onSort] = useSort<SortField>({ key: 'submitted_at', dir: 'desc' }, () => setPage(1))
  const [pageSize, setPageSize] = useState(20)
  const [selectedPrId, setSelectedPrId] = useState<string | null>(null)
  const navigate = useNavigate()

  // Department + Requester filters both draw from the SAME server-computed scope
  // (GET /config/me/scoped-departments), which mirrors the PR list's own
  // visibility. This replaces the old client-side guess (a hard-coded
  // company-wide role set + the viewer's single JWT department) that under-scoped
  // a multi-department Director to his own primary department and over-scoped a
  // GM to every requester.
  const { data: scopedDepts } = useScopedDepartments()
  const departments = scopedDepts?.items ?? []          // active-only, already scoped
  const seesAllDepts = scopedDepts?.unrestricted ?? false
  const scopedDeptIds = departments.map((d) => d.id)

  // Requester picker options — active requesters via the non-admin /users/directory
  // (GET /users is system_admin-only and would 403 for a requester who is also a
  // Department Admin). Scoped to the caller's departments: all when unrestricted,
  // otherwise exactly their scoped set (department_ids covers a Director's/GM's
  // several departments — not just one).
  const { data: usersData } = useQuery({
    queryKey: ['pr-requester-picker', seesAllDepts, scopedDeptIds],
    queryFn: () => userService.directory({
      role: 'requester',
      department_ids: seesAllDepts ? undefined : scopedDeptIds,
    }),
    // Only fire once scope is known: unrestricted → company-wide query is
    // correct; restricted → require a non-empty scoped set (a restricted user
    // with NO departments must offer NO requesters, never fall through to an
    // unscoped query that returns everyone).
    enabled: !isRequesterOnly && (seesAllDepts || scopedDeptIds.length > 0),
    staleTime: 60_000,
  })
  const requesters = (usersData?.items ?? [])
    .slice()
    .sort((a, b) => a.full_name.localeCompare(b.full_name))

  // A pure requester is pinned to their own PRs; otherwise honour the picker.
  const createdByFilter = isRequesterOnly
    ? (user?.id ?? undefined)
    : requesterFilter !== 'all' ? requesterFilter : undefined

  const { data, isLoading } = usePrs({
    search: search || undefined,
    status: statusFilter !== 'all' ? statusFilter : undefined,
    department_id: deptFilter !== 'all' ? deptFilter : undefined,
    pr_type: typeFilter !== 'all' ? Number(typeFilter) : undefined,
    is_prepaid: prepaidFilter === 'all' ? undefined : prepaidFilter === 'yes',
    created_by: createdByFilter,
    ...sortParams(sort),
    page,
    page_size: pageSize,
  })
  const prs = data?.items ?? []
  const total = data?.total ?? 0

  const handleFilterChange = (setter: (v: string) => void) => (v: string) => {
    setter(v)
    setPage(1)
    setSelectedPrId(null)
  }

  // Requester is dropped for a pure requester — the list is already only their
  // own PRs, so the column would repeat their name on every row.
  const columns = ([
    ['number', 'PR Number'],
    ['title', 'Title'],
    ...(isRequesterOnly ? [] : [['created_by_name', 'Requester']]),
    ['vendor_name', 'Vendor'],
    ['amount', 'Amount (CAD)'],
    ['type', 'Type'],
    ['is_prepaid', 'Prepaid'],
    ['status', 'Status'],
    ['submitted_at', 'Submitted'],
  ] as [SortField, string][])
  // checkbox + data columns + the View action cell
  const colCount = columns.length + 2

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-fg">
          {isRequesterOnly ? 'My Purchase Requisitions' : 'Purchase Requisitions'}
        </h1>
        <div className="flex items-center gap-2">
          {selectedPrId && (
            <Button
              variant="secondary"
              onClick={() => navigate(`/pr/new?copyFrom=${selectedPrId}`)}
            >
              <Copy className="h-4 w-4" />
              Copy
            </Button>
          )}
          <Link to="/pr/new">
            <Button><Plus className="h-4 w-4" />New PR</Button>
          </Link>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-60">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
          <Input
            placeholder="Search by PR#, title, vendor, budget code…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); setSelectedPrId(null) }}
            className="pl-9"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Filter className="h-4 w-4 text-fg-muted" />
          <Select
            value={statusFilter}
            onChange={(e) => handleFilterChange(setStatusFilter)(e.target.value)}
           
          >
            {STATUS_FILTER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </Select>
          {/* A pure requester's list is always their own PRs, so neither
              filter has anything to offer (PRD §3.2.3, ISS-0167). */}
          {!isRequesterOnly && (
            <>
              <Select
                value={deptFilter}
                onChange={(e) => handleFilterChange(setDeptFilter)(e.target.value)}
               
              >
                <option value="all">All Departments</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </Select>
              <Select
                value={requesterFilter}
                onChange={(e) => handleFilterChange(setRequesterFilter)(e.target.value)}
                title="Filter by Requester"
               
              >
                <option value="all">All Requesters</option>
                {requesters.map((u) => (
                  <option key={u.id} value={u.id}>{u.full_name}</option>
                ))}
              </Select>
            </>
          )}
          <Select
            value={typeFilter}
            onChange={(e) => handleFilterChange(setTypeFilter)(e.target.value)}
           
          >
            <option value="all">All Types</option>
            {Object.entries(TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </Select>
          <Select
            value={prepaidFilter}
            onChange={(e) => handleFilterChange(setPrepaidFilter)(e.target.value)}
           
          >
            <option value="all">All (Prepaid)</option>
            <option value="yes">Prepaid</option>
            <option value="no">Not Prepaid</option>
          </Select>
        </div>
      </div>

      <div className="rounded-xl bg-surface shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <Table>
            <THead>
              <Tr className="bg-surface-subtle">
                <Th className="w-10" />
                {columns.map(([field, label]) => (
                  <SortTh key={field} label={label} sortKey={field} sort={sort} onSort={onSort}
                    align={field === 'amount' ? 'right' : 'left'}
                    className="px-4 py-3 text-xs font-semibold text-fg-muted" />
                ))}
                <Th />
              </Tr>
            </THead>
            <TBody>
              {isLoading && Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={colCount} />)}
              {!isLoading && prs.length === 0 && (
                <Tr><Td align="center" colSpan={colCount} className="text-sm text-fg-muted">No purchase requisitions found</Td></Tr>
              )}
              {prs.map((pr) => (
                <Tr key={pr.id} className="bg-surface hover:bg-primary-50/60 transition-colors">
                  <Td>
                    <Checkbox
                     
                      checked={selectedPrId === pr.id}
                      onChange={() => setSelectedPrId(selectedPrId === pr.id ? null : pr.id)}
                      onClick={(e) => e.stopPropagation()}
                     
                    />
                  </Td>
                  <Td>
                    <Link to={`/pr/${pr.id}`} className="font-medium text-primary-600 hover:underline whitespace-nowrap">{pr.number}</Link>
                  </Td>
                  <Td className="text-fg">
                    <p className="line-clamp-2 max-w-52 break-words" title={pr.title}>{pr.title}</p>
                  </Td>
                  {!isRequesterOnly && (
                    <Td className="text-fg-secondary max-w-40 truncate" title={pr.created_by_name ?? undefined}>{pr.created_by_name ?? '—'}</Td>
                  )}
                  <Td className="text-fg-secondary max-w-40 truncate">{pr.vendor_name}</Td>
                  <Td className="amount text-fg">{formatCAD(pr.amount)}</Td>
                  <Td className="text-fg-muted whitespace-nowrap">{TYPE_LABELS[pr.type] ?? `Type ${pr.type}`}</Td>
                  <Td>
                    {pr.is_prepaid && (
                      <span className="inline-flex items-center rounded-full bg-warning-soft px-2 py-0.5 text-2xs font-semibold text-warning-fg border border-warning-line">
                        Prepaid
                      </span>
                    )}
                  </Td>
                  <Td>
                    <StatusBadge status={pr.status} />
                    <CurrentStepHint current_step={pr.current_step} />
                  </Td>
                  <Td className="text-fg-muted whitespace-nowrap">{formatDate(pr.submitted_at ?? '')}</Td>
                  <Td>
                    <Link to={`/pr/${pr.id}`}><Button variant="ghost" size="sm">View</Button></Link>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      </div>
    </div>
  )
}
