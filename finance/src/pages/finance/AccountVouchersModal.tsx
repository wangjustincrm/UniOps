/**
 * Voucher drill-down modal (能力③) — the posted JV lines composing one account
 * (+optional cost center) in a period. Each row links into JvDetailModal via
 * onOpenJv. Used by AccountBalancePage and BudgetActualPage.
 */
import { useQuery } from '@tanstack/react-query'
import { BookOpen, Loader2, X } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'

function money(v: string | null | undefined) {
  const n = Number(v ?? 0)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : ''
}

interface VoucherRow {
  jv_id: string; jv_number: string; voucher_date: string; summary: string | null
  local_debit: string; local_credit: string; cost_center_id: string | null
  source_doc_type: string | null; source_doc_id: string | null; source_doc_number: string | null
}
interface VouchersResp { account_code: string; period: string; rows: VoucherRow[] }

export function AccountVouchersModal({ accountCode, period, dimsValues, title, onClose, onOpenJv }: {
  accountCode: string; period: string; dimsValues?: string | null
  title: string; onClose: () => void; onOpenJv: (jvId: string) => void
}) {
  const qs = dimsValues
    ? `?period=${period}&dims_values=${encodeURIComponent(dimsValues)}`
    : `?period=${period}`
  const { data, isLoading } = useQuery({
    queryKey: ['ab-vouchers', accountCode, period, dimsValues ?? ''],
    queryFn: () => financeApi.get<VouchersResp>(`/gl/account-balance/${accountCode}/vouchers${qs}`),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="max-h-[85vh] w-full max-w-4xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-800">
            <BookOpen className="h-4 w-4 text-neutral-400" /> {title}
          </h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>
        {isLoading ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="overflow-hidden rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr>
                  <th className="px-3 py-2 w-24">Date</th>
                  <th className="px-3 py-2 w-36">Voucher</th>
                  <th className="px-3 py-2">Summary</th>
                  <th className="px-3 py-2 w-28 text-right">Debit</th>
                  <th className="px-3 py-2 w-28 text-right">Credit</th>
                </tr>
              </thead>
              <tbody>
                {(data?.rows ?? []).length === 0 && (
                  <tr><td colSpan={5} className="px-3 py-6 text-center text-neutral-400">No vouchers.</td></tr>
                )}
                {(data?.rows ?? []).map((r, i) => (
                  <tr key={`${r.jv_id}-${i}`} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                    <td className="px-3 py-2 font-mono text-xs text-neutral-600">{r.voucher_date}</td>
                    <td className="px-3 py-2">
                      <button onClick={() => onOpenJv(r.jv_id)}
                              className="font-mono text-xs text-[#085E5E] hover:underline">
                        {r.jv_number}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-neutral-700">{r.summary || '—'}</td>
                    <td className="px-3 py-2 text-right font-mono">{money(r.local_debit)}</td>
                    <td className="px-3 py-2 text-right font-mono">{money(r.local_credit)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
