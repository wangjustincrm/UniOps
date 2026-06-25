import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { api } from '@/lib/api'
import { Pagination } from '@/components/ui/Pagination'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Pa {
  id: string
  pa_number: string
  status: string
  vendor_name: string
  payment_amount: number
  currency: string
  submitted_at: string | null
  created_at?: string
}

interface PaList { items: Pa[]; total: number }

// ── Status badge ──────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  draft:     'bg-neutral-100 text-neutral-600',
  submitted: 'bg-yellow-50 text-yellow-700',
  in_review: 'bg-blue-50 text-blue-700',
  approved:  'bg-green-50 text-green-700',
  processed: 'bg-neutral-100 text-neutral-500',
  returned:  'bg-orange-50 text-orange-700',
  cancelled: 'bg-red-50 text-red-700',
  paid:      'bg-emerald-50 text-emerald-700',
}

function StatusBadge({ status }: { status: string }) {
  const label = status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium', STATUS_COLORS[status] ?? 'bg-neutral-100 text-neutral-600')}>
      {label}
    </span>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function PaListPage() {
  const [params, setParams] = useSearchParams()
  const status = params.get('status') ?? 'all'
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  // PA-DIR from expense-api
  const { data: dirData, isLoading: dirLoading, error: dirError } = useQuery<PaList>({
    queryKey: ['pa-list-dir', status, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      if (status !== 'all') qs.set('status', status)
      return api.get<PaList>(`/api/v1/pa?${qs}`)
    },
  })

  const items: Pa[] = dirData?.items ?? []
  const total = dirData?.total ?? 0
  const isLoading = dirLoading
  const error = dirError

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Payment Applications</h1>
          <p className="mt-0.5 text-sm text-neutral-500">Manage vendor payment requests</p>
        </div>
        <div className="flex items-center gap-2">
          <a
            href="/pa/new/direct"
            className="inline-flex items-center gap-2 rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white hover:bg-[#064A4A] transition-colors"
          >
            <Plus className="h-4 w-4" />
            Direct PA
          </a>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 border-b border-neutral-200">
        {['all', 'submitted', 'in_review', 'approved', 'processed'].map((s) => (
          <button
            key={s}
            onClick={() => { setParams(s === 'all' ? {} : { status: s }); setPage(1) }}
            className={cn(
              'px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
              status === s
                ? 'border-[#085E5E] text-[#085E5E]'
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
        ) : error ? (
          <div className="flex items-center justify-center py-16 text-sm text-red-500">
            Failed to load payment applications
          </div>
        ) : !items.length ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <CreditCardIcon />
            <p className="mt-3 font-medium text-neutral-700">No payment applications</p>
            <p className="mt-1 text-sm text-neutral-400">Create one from a PO or start a direct payment.</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">PA Number</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Vendor</th>
                <th className="px-4 py-3 text-right font-medium text-neutral-500 text-xs uppercase tracking-wide">Amount</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Status</th>
                <th className="px-4 py-3 text-left font-medium text-neutral-500 text-xs uppercase tracking-wide">Date</th>
              </tr>
            </thead>
            <tbody>
              {items.map((pa, i) => (
                <tr
                  key={pa.id}
                  className={cn('border-b border-neutral-100 hover:bg-neutral-50 cursor-pointer', i === items.length - 1 && 'border-b-0')}
                  onClick={() => window.location.href = `/pa/${pa.id}`}
                >
                  <td className="px-4 py-3 font-mono text-xs text-primary-600 font-medium">{pa.pa_number}</td>
                  <td className="px-4 py-3 text-neutral-700 max-w-xs truncate">{pa.vendor_name}</td>
                  <td className="px-4 py-3 text-right font-mono text-neutral-900">{formatAmount(pa.payment_amount, pa.currency)}</td>
                  <td className="px-4 py-3"><StatusBadge status={pa.status} /></td>
                  <td className="px-4 py-3 text-neutral-500">{formatDate(pa.submitted_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {total > pageSize && (
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={setPage}
            onPageSizeChange={(s) => { setPageSize(s); setPage(1) }}
          />
        )}
      </div>
    </div>
  )
}

function CreditCardIcon() {
  return (
    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-neutral-100">
      <svg className="h-6 w-6 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z" />
      </svg>
    </div>
  )
}
