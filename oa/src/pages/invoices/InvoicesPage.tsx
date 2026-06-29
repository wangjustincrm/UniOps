import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { FileText, Search, X, Paperclip } from 'lucide-react'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { api } from '@/lib/api'
import { Pagination } from '@/components/ui/Pagination'
import { StatusBadge } from '@/components/ui/badge'

// ── Types ─────────────────────────────────────────────────────────────────────

interface UnifiedInvoice {
  id: string
  source: 'epms' | 'oa'
  invoice_number: string | null
  vendor_name: string | null
  total_amount: number
  currency: string
  invoice_date: string | null
  status: string
  file_name: string | null
  created_at: string
  internal_ref?: string | null
  po_number?: string | null
  pa_number?: string | null
  attachment_count: number
}

interface InvoiceList { items: UnifiedInvoice[]; total: number }

// ── Page ──────────────────────────────────────────────────────────────────────

export default function InvoicesPage() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const { data, isLoading, error } = useQuery<InvoiceList>({
    queryKey: ['oa-invoices', search, page, pageSize],
    queryFn: () => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize), source: 'oa' })
      if (search) qs.set('search', search)
      return api.get<InvoiceList>(`/api/v1/invoices/all?${qs}`)
    },
    staleTime: 30_000,
  })

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-neutral-900">Invoices</h1>
        <p className="mt-0.5 text-sm text-neutral-500">
          Vendor invoices for direct payments
        </p>
      </div>

      {/* Stats */}
      <div className="rounded-xl border border-neutral-200 bg-white p-4 text-center max-w-[200px]">
        <p className="text-xs text-neutral-400">Total</p>
        <p className="text-2xl font-bold text-neutral-900 mt-1">{data?.total ?? 0}</p>
      </div>

      {/* Search */}
      <div className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 max-w-sm">
        <Search className="h-4 w-4 text-neutral-400 shrink-0" />
        <input className="flex-1 text-sm focus:outline-none" placeholder="Search vendor or invoice #…"
          value={search} onChange={(e) => setSearch(e.target.value)} />
        {search && <button onClick={() => setSearch('')} className="text-neutral-300 hover:text-neutral-500"><X className="h-3.5 w-3.5" /></button>}
      </div>

      {/* Table */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading ? (
          <div className="py-12 text-center text-sm text-neutral-400">Loading…</div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-14 text-center">
            <p className="font-medium text-danger-600">Failed to load invoices</p>
            <p className="mt-1 text-sm text-neutral-400">{(error as Error).message}</p>
          </div>
        ) : !data?.items.length ? (
          <div className="flex flex-col items-center justify-center py-14 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-neutral-100 mb-3">
              <FileText className="h-6 w-6 text-neutral-400" />
            </div>
            <p className="font-medium text-neutral-700">No invoices found</p>
            <p className="mt-1 text-sm text-neutral-400">Upload invoices in OA to see them here.</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                {['Invoice #', 'Vendor', 'Amount', 'Status', 'Reference', 'Attachments', 'Date'].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.items.map((inv, i) => (
                <tr key={`${inv.source}-${inv.id}`}
                  onClick={() => navigate(`/invoices/${inv.source}/${inv.id}`)}
                  className={cn(
                    'border-b border-neutral-100 cursor-pointer hover:bg-neutral-50 transition-colors',
                    i === data.items.length - 1 && 'border-b-0',
                  )}>
                  <td className="px-4 py-3">
                    {/* Invoice # = vendor invoice number */}
                    <p className="font-mono text-xs text-primary-600 font-medium">
                      {inv.invoice_number || '—'}
                    </p>
                  </td>
                  <td className="px-4 py-3 text-neutral-800 max-w-[160px] truncate">{inv.vendor_name ?? '—'}</td>
                  <td className="px-4 py-3 font-mono font-medium text-neutral-900">
                    {formatAmount(inv.total_amount, inv.currency)}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={inv.status} />
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-neutral-500">
                    {inv.po_number || inv.pa_number || '—'}
                  </td>
                  <td className="px-4 py-3 text-center">
                    {inv.attachment_count > 0 ? (
                      <span className="inline-flex items-center gap-1 text-xs text-primary-600 font-medium">
                        <Paperclip className="h-3.5 w-3.5" />{inv.attachment_count}
                      </span>
                    ) : '—'}
                  </td>
                  <td className="px-4 py-3 text-neutral-500">{formatDate(inv.created_at)}</td>
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
