import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Search, Plus, ChevronUp, ChevronDown, Filter, Copy } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { StatusBadge } from '@/components/ui/badge'
import { CurrentStepHint } from '@/components/ui/CurrentStepHint'
import { Pagination } from '@/components/ui/Pagination'
import { formatCAD, formatDate } from '@/lib/utils'
import { compareByStatusThenStep } from '@/lib/currentStepSort'
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

type SortField = 'number' | 'title' | 'amount' | 'status' | 'submitted_at' | 'is_prepaid'
type SortDir = 'asc' | 'desc'

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
  const [sortField, setSortField] = useState<SortField>('submitted_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [page, setPage] = useState(1)
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
    page,
    page_size: pageSize,
  })
  const prs = data?.items ?? []
  const total = data?.total ?? 0

  const handleSort = (field: SortField) => {
    if (sortField === field) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortField(field); setSortDir('desc') }
  }

  const handleFilterChange = (setter: (v: string) => void) => (v: string) => {
    setter(v)
    setPage(1)
    setSelectedPrId(null)
  }

  const sorted = [...prs].sort((a, b) => {
    let cmp = 0
    if (sortField === 'amount') cmp = a.amount - b.amount
    else if (sortField === 'submitted_at') cmp = (a.submitted_at ?? '').localeCompare(b.submitted_at ?? '')
    else if (sortField === 'status') cmp = compareByStatusThenStep(a, b)
    else cmp = String(a[sortField as keyof typeof a]).localeCompare(String(b[sortField as keyof typeof b]))
    return sortDir === 'asc' ? cmp : -cmp
  })

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return <ChevronUp className="h-3 w-3 text-neutral-300" />
    return sortDir === 'asc'
      ? <ChevronUp className="h-3 w-3 text-primary-600" />
      : <ChevronDown className="h-3 w-3 text-primary-600" />
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-neutral-900">
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
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
          <Input
            placeholder="Search by PR#, title, vendor, budget code…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); setSelectedPrId(null) }}
            className="pl-9"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Filter className="h-4 w-4 text-neutral-400" />
          <select
            value={statusFilter}
            onChange={(e) => handleFilterChange(setStatusFilter)(e.target.value)}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            {STATUS_FILTER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <select
            value={deptFilter}
            onChange={(e) => handleFilterChange(setDeptFilter)(e.target.value)}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            <option value="all">All Departments</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
          <select
            value={isRequesterOnly ? (user?.id ?? 'all') : requesterFilter}
            onChange={(e) => handleFilterChange(setRequesterFilter)(e.target.value)}
            disabled={isRequesterOnly}
            title="Filter by Requester"
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-50 disabled:text-neutral-500"
          >
            {isRequesterOnly ? (
              <option value={user?.id ?? 'all'}>{user?.name ? `${user.name} (me)` : 'My Requests'}</option>
            ) : (
              <>
                <option value="all">All Requesters</option>
                {requesters.map((u) => (
                  <option key={u.id} value={u.id}>{u.full_name}</option>
                ))}
              </>
            )}
          </select>
          <select
            value={typeFilter}
            onChange={(e) => handleFilterChange(setTypeFilter)(e.target.value)}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            <option value="all">All Types</option>
            {Object.entries(TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <select
            value={prepaidFilter}
            onChange={(e) => handleFilterChange(setPrepaidFilter)(e.target.value)}
            className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm text-neutral-700 focus:outline-none focus:ring-2 focus:ring-primary-600"
          >
            <option value="all">All (Prepaid)</option>
            <option value="yes">Prepaid</option>
            <option value="no">Not Prepaid</option>
          </select>
        </div>
      </div>

      <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 bg-neutral-50">
                <th className="w-10 px-4 py-3" />
                {(
                  [
                    ['number', 'PR Number'],
                    ['title', 'Title'],
                    ['vendor_name', 'Vendor'],
                    ['amount', 'Amount (CAD)'],
                    ['type', 'Type'],
                    ['is_prepaid', 'Prepaid'],
                    ['status', 'Status'],
                    ['submitted_at', 'Submitted'],
                  ] as [SortField | 'vendor_name' | 'type' | 'is_prepaid', string][]
                ).map(([field, label]) => (
                  <th key={field} className={`px-4 py-3 text-xs font-semibold uppercase tracking-wide text-neutral-500 ${field === 'amount' ? 'text-right' : 'text-left'}`}>
                    {['vendor_name', 'type', 'is_prepaid'].includes(field) ? label : (
                      <button onClick={() => handleSort(field as SortField)} className="flex items-center gap-1 hover:text-neutral-700">
                        {label}<SortIcon field={field as SortField} />
                      </button>
                    )}
                  </th>
                ))}
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {isLoading && Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={10} />)}
              {!isLoading && sorted.length === 0 && (
                <tr><td colSpan={10} className="py-16 text-center text-sm text-neutral-400">No purchase requisitions found</td></tr>
              )}
              {sorted.map((pr) => (
                <tr key={pr.id} className="border-b border-neutral-100 bg-white hover:bg-primary-50/60 transition-colors">
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selectedPrId === pr.id}
                      onChange={() => setSelectedPrId(selectedPrId === pr.id ? null : pr.id)}
                      onClick={(e) => e.stopPropagation()}
                      className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-600 cursor-pointer"
                    />
                  </td>
                  <td className="px-4 py-3">
                    <Link to={`/pr/${pr.id}`} className="font-medium text-primary-600 hover:underline whitespace-nowrap">{pr.number}</Link>
                  </td>
                  <td className="px-4 py-3 text-neutral-900">
                    <p className="line-clamp-2 max-w-52 break-words" title={pr.title}>{pr.title}</p>
                  </td>
                  <td className="px-4 py-3 text-neutral-600 max-w-40 truncate">{pr.vendor_name}</td>
                  <td className="px-4 py-3 amount text-neutral-900">{formatCAD(pr.amount)}</td>
                  <td className="px-4 py-3 text-neutral-500 whitespace-nowrap">{TYPE_LABELS[pr.type] ?? `Type ${pr.type}`}</td>
                  <td className="px-4 py-3">
                    {pr.is_prepaid && (
                      <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700 border border-amber-200">
                        Prepaid
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={pr.status} />
                    <CurrentStepHint current_step={pr.current_step} />
                  </td>
                  <td className="px-4 py-3 text-neutral-500 whitespace-nowrap">{formatDate(pr.submitted_at ?? '')}</td>
                  <td className="px-4 py-3">
                    <Link to={`/pr/${pr.id}`}><Button variant="ghost" size="sm">View</Button></Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      </div>
    </div>
  )
}
