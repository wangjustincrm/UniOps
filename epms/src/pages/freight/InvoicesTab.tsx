import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Upload, Loader2, FileText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Pagination } from '@/components/ui/Pagination'
import { cn, formatAmount, formatDate } from '@/lib/utils'
import { dueInfo } from '@/lib/dueDate'
import { useFreightCarriers, useFreightInvoices } from '@/hooks/useFreight'
import { FreightInvoiceStatusBadge, SELECT_CLS, TH_CLS, num } from './freightUi'
import { UploadInvoiceDialog } from './UploadInvoiceDialog'

export function InvoicesTab() {
  const navigate = useNavigate()
  const [status, setStatus] = useState('')
  const [carrierId, setCarrierId] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [uploadOpen, setUploadOpen] = useState(false)

  const { data: carriers = [] } = useFreightCarriers()
  // Server order: due date ascending — the bill that must be paid first is on top.
  const { data, isPending } = useFreightInvoices({
    status: status || undefined, carrier_id: carrierId || undefined, page, page_size: pageSize,
  })
  const rows = data?.items ?? []
  const total = data?.total ?? 0

  return (
    <>
      <div className="rounded-xl border border-neutral-200 bg-white shadow-sm overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-neutral-200 px-5 py-3">
          <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }} className={SELECT_CLS}>
            <option value="">All Statuses</option>
            <option value="unmatched">Matching</option>
            <option value="matched">Matched</option>
            <option value="paid">Paid</option>
          </select>
          <select value={carrierId} onChange={(e) => { setCarrierId(e.target.value); setPage(1) }} className={SELECT_CLS}>
            <option value="">All Carriers</option>
            {carriers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <span className="text-sm text-neutral-400">
            {isPending ? 'Loading…' : `${total} result${total !== 1 ? 's' : ''}`}
          </span>
          <Button onClick={() => setUploadOpen(true)} className="gap-2 ml-auto">
            <Upload className="h-4 w-4" /> Upload Invoice
          </Button>
        </div>

        {isPending ? (
          <div className="flex flex-col items-center justify-center py-16">
            <Loader2 className="h-8 w-8 text-neutral-300 mb-3 animate-spin" />
            <p className="text-sm text-neutral-400">Loading invoices…</p>
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <FileText className="h-10 w-10 text-neutral-200 mb-3" />
            <p className="text-sm font-medium text-neutral-500">No courier invoices found</p>
            <p className="text-xs text-neutral-400 mt-1">Upload a carrier invoice PDF to start matching</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-100 bg-neutral-50">
                  {['Carrier', 'Invoice #', 'Invoice Date', 'Due Date', 'Amount', 'Tax', 'Total', 'Lines', 'Status', 'PA'].map((h) => (
                    <th key={h} className={cn(TH_CLS, ['Amount', 'Tax', 'Total', 'Lines'].includes(h) && 'text-right')}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((inv) => {
                  const due = dueInfo(inv.due_date, inv.status)
                  const urgent = due.tone === 'overdue' || due.tone === 'soon'
                  return (
                    <tr key={inv.id} onClick={() => navigate(`/freight/invoices/${inv.id}`)}
                      className="border-b border-neutral-100 bg-white hover:bg-primary-50/60 cursor-pointer transition-colors">
                      <td className="px-3 py-2.5 font-medium text-neutral-800">{inv.carrier_code ?? '—'}</td>
                      <td className="px-3 py-2.5">
                        <Link to={`/freight/invoices/${inv.id}`} onClick={(e) => e.stopPropagation()}
                          className="font-mono text-xs font-semibold text-primary-700 hover:underline">
                          {inv.vendor_invoice_number}
                        </Link>
                        <div className="text-[10px] text-neutral-400 font-mono">{inv.internal_ref}</div>
                      </td>
                      <td className="px-3 py-2.5 text-xs text-neutral-600">{formatDate(inv.invoice_date)}</td>
                      <td className={cn('px-3 py-2.5 text-xs whitespace-nowrap', urgent ? 'font-semibold text-danger-600' : 'text-neutral-600')}>
                        {formatDate(inv.due_date)}
                        {due.label && <div className="text-[10px]">{due.label}</div>}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-xs">{formatAmount(num(inv.amount), inv.currency)}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-xs">{formatAmount(num(inv.tax_amount), inv.currency)}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-xs font-semibold">{formatAmount(num(inv.total_amount), inv.currency)}</td>
                      <td className="px-3 py-2.5 text-right text-xs text-neutral-600">{inv.line_count}</td>
                      <td className="px-3 py-2.5"><FreightInvoiceStatusBadge status={inv.status} /></td>
                      <td className="px-3 py-2.5 text-xs">
                        {inv.pa_id ? (
                          <Link to={`/pa/${inv.pa_id}`} onClick={(e) => e.stopPropagation()}
                            className="font-mono text-primary-700 hover:underline">
                            {inv.pa_number}
                          </Link>
                        ) : <span className="text-neutral-300">—</span>}
                        {inv.pa_status && <div className="text-[10px] text-neutral-400 capitalize">{inv.pa_status.replace(/_/g, ' ')}</div>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      </div>

      {uploadOpen && (
        <UploadInvoiceDialog
          onClose={() => setUploadOpen(false)}
          onCreated={(id) => { setUploadOpen(false); navigate(`/freight/invoices/${id}`) }}
        />
      )}
    </>
  )
}
