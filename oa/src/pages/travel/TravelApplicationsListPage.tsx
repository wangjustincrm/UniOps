import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Plus, Plane } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { Pagination } from '@/components/ui/Pagination'
import { StatusBadge } from '@/components/ui/badge'

// ── Types ─────────────────────────────────────────────────────────────────────

interface TravelApp {
  id: string
  claim_number: string
  employee_name: string
  department_name: string
  submission_date: string
  travel_destination?: string | null
  status: string
}

interface TravelAppList { items: TravelApp[]; total: number }

// ── Filter tabs ───────────────────────────────────────────────────────────────

const TABS = ['all', 'draft', 'submitted', 'in_review', 'approved'] as const

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TravelApplicationsListPage() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const activeStatus = params.get('status') ?? 'all'
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const { data, isLoading } = useQuery<TravelAppList>({
    queryKey: ['travel-list', activeStatus, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ type: 'TRA', page: String(page), page_size: String(pageSize) })
      if (activeStatus !== 'all') qs.set('status', activeStatus)
      return api.get<TravelAppList>(`/api/v1/expenses?${qs}`)
    },
  })

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900 flex items-center gap-2">
            <Plane className="h-6 w-6 text-primary-700" />
            Travel Applications
          </h1>
          <p className="mt-0.5 text-sm text-neutral-500">Pre-trip travel authorization requests</p>
        </div>

        <button
          onClick={() => navigate('/travel/new')}
          className="inline-flex items-center gap-2 rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800 transition-colors"
        >
          <Plus className="h-4 w-4" />
          New Travel Application
        </button>
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
              <Plane className="h-6 w-6 text-neutral-400" />
            </div>
            <p className="mt-3 font-medium text-neutral-700">No travel applications</p>
            <p className="mt-1 text-sm text-neutral-400">Click "New Travel Application" to request travel authorization.</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Application #</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Applicant</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Department</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Destination</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Date</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((a, i) => (
                <tr
                  key={a.id}
                  className={cn('border-b border-neutral-100 hover:bg-neutral-50 cursor-pointer', i === data.items.length - 1 && 'border-b-0')}
                  onClick={() => navigate(`/travel/${a.id}`)}
                >
                  <td className="px-4 py-3 font-mono text-xs text-primary-600 font-medium">{a.claim_number}</td>
                  <td className="px-4 py-3 text-neutral-700">{a.employee_name}</td>
                  <td className="px-4 py-3 text-neutral-500">{a.department_name}</td>
                  <td className="px-4 py-3 text-neutral-700">{a.travel_destination || '—'}</td>
                  <td className="px-4 py-3 text-neutral-500">{a.submission_date}</td>
                  <td className="px-4 py-3"><StatusBadge status={a.status} /></td>
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
