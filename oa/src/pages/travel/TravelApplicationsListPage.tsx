import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Plane, Trash2 } from 'lucide-react'
import { cn, formatDate } from '@/lib/utils'
import { api } from '@/lib/api'
import { STATUS } from '@/lib/status'
import { Pagination } from '@/components/ui/Pagination'
import { StatusBadge } from '@/components/ui/badge'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Button, SortTh, TBody, THead, Table, Tabs, Td, Th, Tr, useSort } from '@uniops/shell'

type TravelSortKey = 'claim_number' | 'employee_name' | 'department_name' | 'travel_destination' | 'submission_date' | 'status'

// ── Types ─────────────────────────────────────────────────────────────────────

interface TravelApp {
  id: string
  claim_number: string
  employee_name: string
  department_name: string
  submission_date: string
  travel_destination?: string | null
  status: string
  // Server-computed: unapproved + mine (or I am system_admin). The page holds
  // no permission logic of its own.
  can_delete: boolean
}

interface TravelAppList { items: TravelApp[]; total: number }

// ── Filter tabs ───────────────────────────────────────────────────────────────

const TABS = ['all', STATUS.DRAFT, STATUS.SUBMITTED, STATUS.IN_REVIEW, STATUS.APPROVED] as const

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TravelApplicationsListPage() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const activeStatus = params.get('status') ?? 'all'
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  // Paged by expense-api, so the API sorts (null = its default, newest first).
  const [sort, onSort] = useSort<TravelSortKey>(null, () => setPage(1))

  const qc = useQueryClient()
  const [pendingDelete, setPendingDelete] = useState<TravelApp | null>(null)
  const [deleteError, setDeleteError] = useState('')

  const { data, isLoading } = useQuery<TravelAppList>({
    queryKey: ['travel-list', activeStatus, page, pageSize, sort],
    queryFn: () => {
      const qs = new URLSearchParams({ type: 'TRA', page: String(page), page_size: String(pageSize) })
      if (activeStatus !== 'all') qs.set('status', activeStatus)
      if (sort) { qs.set('sort', sort.key); qs.set('dir', sort.dir) }
      return api.get<TravelAppList>(`/api/v1/expenses?${qs}`)
    },
  })

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/expenses/${id}`),
    onSuccess: () => {
      // Deleting the only row of a trailing page would otherwise strand the
      // user on an empty page.
      if ((data?.items.length ?? 0) === 1 && page > 1) setPage(page - 1)
      qc.invalidateQueries({ queryKey: ['travel-list'] })
      setPendingDelete(null)
    },
    onError: (e: unknown) => setDeleteError(e instanceof Error ? e.message : 'Delete failed'),
  })

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-fg flex items-center gap-2">
            <Plane className="h-6 w-6 text-primary-700" />
            Travel Applications
          </h1>
          <p className="mt-0.5 text-sm text-fg-muted">Pre-trip travel authorization requests</p>
        </div>

        <Button size="md"
          onClick={() => navigate('/travel/new')}
         
        >
          <Plus className="h-4 w-4" />
          New Travel Application
        </Button>
      </div>

      {/* Filter tabs */}
      <Tabs
        items={TABS.map((s) => ({ key: s, label: s === 'all' ? 'All' : s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) }))}
        value={activeStatus}
        onChange={(s) => { setParams(s === 'all' ? {} : { status: s }); setPage(1) }}
      />

      {/* Table */}
      <div className="rounded-xl border border-line bg-surface overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-16 text-sm text-fg-muted">Loading…</div>
        ) : !data?.items.length ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-surface-sunken">
              <Plane className="h-6 w-6 text-fg-muted" />
            </div>
            <p className="mt-3 font-medium text-fg-secondary">No travel applications</p>
            <p className="mt-1 text-sm text-fg-muted">Click "New Travel Application" to request travel authorization.</p>
          </div>
        ) : (
          <Table>
            <THead>
              <Tr>
                <SortTh label="Application #" sortKey="claim_number" sort={sort} onSort={onSort} className="px-4 py-3 text-left font-medium text-fg-muted text-xs" />
                <SortTh label="Applicant" sortKey="employee_name" sort={sort} onSort={onSort} className="px-4 py-3 text-left font-medium text-fg-muted text-xs" />
                <SortTh label="Department" sortKey="department_name" sort={sort} onSort={onSort} className="px-4 py-3 text-left font-medium text-fg-muted text-xs" />
                <SortTh label="Destination" sortKey="travel_destination" sort={sort} onSort={onSort} className="px-4 py-3 text-left font-medium text-fg-muted text-xs" />
                <SortTh label="Date" sortKey="submission_date" sort={sort} onSort={onSort} className="px-4 py-3 text-left font-medium text-fg-muted text-xs" />
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-4 py-3 text-left font-medium text-fg-muted text-xs" />
                <Th align="right">Actions</Th>
              </Tr>
            </THead>
            <TBody>
              {data.items.map((a, i) => (
                <Tr
                  key={a.id}
                  className={cn('border-b border-line-subtle hover:bg-surface-subtle cursor-pointer', i === data.items.length - 1 && 'border-b-0')}
                  onClick={() => navigate(`/travel/${a.id}`)}
                >
                  <Td className="font-mono text-xs text-primary-600 font-medium">{a.claim_number}</Td>
                  <Td className="text-fg-secondary">{a.employee_name}</Td>
                  <Td className="text-fg-muted">{a.department_name}</Td>
                  <Td className="text-fg-secondary">{a.travel_destination || '—'}</Td>
                  <Td className="text-fg-muted">{formatDate(a.submission_date)}</Td>
                  <Td><StatusBadge status={a.status} /></Td>
                  <Td align="right">
                    {a.can_delete && (
                      <Button variant="danger-ghost" size="icon-sm" aria-label="Remove"
                        // The row itself navigates to the detail page.
                        onClick={(e) => { e.stopPropagation(); setDeleteError(''); setPendingDelete(a) }}
                        title="Delete this travel application"
                       
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    )}
                  </Td>
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
      </div>

      {pendingDelete && (
        <ConfirmDialog
          title={`Delete ${pendingDelete.claim_number}?`}
          message="This travel application and its approval history will be permanently deleted. This cannot be undone."
          onConfirm={() => del.mutate(pendingDelete.id)}
          onClose={() => setPendingDelete(null)}
          loading={del.isPending}
          error={deleteError}
        />
      )}
    </div>
  )
}
