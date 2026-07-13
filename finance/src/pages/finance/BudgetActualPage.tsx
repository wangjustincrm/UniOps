/**
 * Budget Actual (能力④) — actuals of the 4 expense accounts per cost center,
 * grouped by category (5101→MOH · 5301→RD · 6601→SELL · 6602→GA). Actual =
 * period DEBIT movement of posted JV lines (expenses net to ~0 via 结转).
 * Each row drills into its composing vouchers.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccountVouchersModal } from './AccountVouchersModal'
import { JvDetailModal } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const linkBtn = 'text-xs font-medium text-[#085E5E] hover:underline'

const CATEGORIES: { key: string; label: string; account: string }[] = [
  { key: 'MOH', label: 'Manufacturing Overhead (5101)', account: '5101' },
  { key: 'RD', label: 'R&D Expenses (5301)', account: '5301' },
  { key: 'SELL', label: 'Selling Expenses (6601)', account: '6601' },
  { key: 'GA', label: 'G&A Expenses (6602)', account: '6602' },
]

interface BaRow {
  account_code: string; category: string
  cost_center_id: string | null; cost_center_code: string | null; cost_center_name: string | null
  actual: string
}
interface BaResp { period: string; rows: BaRow[] }

function money(v: string | number) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
function thisMonth() { return new Date().toISOString().slice(0, 7) }

interface Drill { accountCode: string; costCenterId?: string | null; title: string }

export default function BudgetActualPage() {
  const { user } = useAuthStore()
  const [period, setPeriod] = useState(thisMonth())
  const [drill, setDrill] = useState<Drill | null>(null)
  const [jvId, setJvId] = useState<string | null>(null)

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })

  const { data, isFetching } = useQuery({
    queryKey: ['budget-actual', period],
    queryFn: () => financeApi.get<BaResp>(`/gl/budget-actual?period=${period}`),
  })

  const grouped = useMemo(() => {
    const g: Record<string, BaRow[]> = {}
    for (const r of data?.rows ?? []) (g[r.category] ??= []).push(r)
    for (const rows of Object.values(g)) rows.sort((a, b) => Number(b.actual) - Number(a.actual))
    return g
  }, [data])

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/budget-actual"
      title="Budget Actual"
      subtitle="GL-side actuals per cost center — period debit of the four expense accounts (posted JV, CAD)"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4">
          <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)}
                 className={cn(inputCls, 'w-40')} />
        </div>

        {isFetching && !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {CATEGORIES.map((cat) => {
              const rows = grouped[cat.key] ?? []
              const total = rows.reduce((s, r) => s + Number(r.actual), 0)
              return (
                <div key={cat.key} className="overflow-hidden rounded-lg border border-neutral-200">
                  <div className="flex items-center justify-between bg-neutral-50 px-3 py-2">
                    <span className="text-sm font-semibold text-neutral-700">{cat.label}</span>
                    <span className="font-mono text-sm font-semibold">{money(total)}</span>
                  </div>
                  <table className="w-full text-sm">
                    <tbody>
                      {rows.length === 0 && (
                        <tr><td className="px-3 py-4 text-center text-xs text-neutral-400">No actuals this period.</td></tr>
                      )}
                      {rows.map((r, i) => (
                        <tr key={r.cost_center_id ?? `none-${i}`}
                            className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                          <td className="px-3 py-2">
                            {r.cost_center_code
                              ? <><span className="font-mono text-xs">{r.cost_center_code}</span><span className="ml-1 text-neutral-600">{r.cost_center_name}</span></>
                              : <span className="text-neutral-400">(no cost center)</span>}
                          </td>
                          <td className="px-3 py-2 w-32 text-right font-mono">{money(r.actual)}</td>
                          <td className="px-3 py-2 w-24 text-right">
                            <button className={linkBtn}
                                    onClick={() => setDrill({
                                      accountCode: r.account_code, costCenterId: r.cost_center_id,
                                      title: `Vouchers — ${r.account_code} · ${r.cost_center_code ?? 'no cost center'} · ${period}`,
                                    })}>
                              Vouchers
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {drill && (
        <AccountVouchersModal accountCode={drill.accountCode} period={period}
                              costCenterId={drill.costCenterId} title={drill.title}
                              onClose={() => setDrill(null)} onOpenJv={(id) => setJvId(id)} />
      )}
      {jvId && (
        <JvDetailModal jvId={jvId} canAct={perms?.can_act ?? false} onClose={() => setJvId(null)} />
      )}
    </PortalChromeLayout>
  )
}
