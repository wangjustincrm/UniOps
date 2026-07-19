/**
 * Budget vs Actual (预实对比) — per (cost center × income-expense item) budget /
 * actual / variance for the 5 expense categories, with category-level Payroll /
 * Depreciation tie-out rows and an exceptions panel for unmapped lines. Actual =
 * NC posted JV period debit (via /gl/budget-actual-grid); budget = budget-api
 * current approved plan. Read-only; each actual drills into its composing vouchers.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccountVouchersModal } from './AccountVouchersModal'
import { JvDetailModal } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const linkBtn = 'text-xs font-medium text-[#085E5E] hover:underline'

const CATEGORY_LABEL: Record<string, string> = {
  MOH: 'Manufacturing Overhead', RD: 'R&D', SELL: 'Selling', GA: 'G&A', FN: 'Financial',
}

interface DetailRow {
  cost_center_id: string | null; cost_center_code: string | null; cost_center_name: string | null
  income_expense_item_id: string | null; income_expense_code: string | null; income_expense_name: string | null
  budget: string; actual: string; variance: string
}
interface Category {
  account_code: string; category: string; detail: DetailRow[]
  payroll_actual: string; depreciation_actual: string
  detail_actual_total: string; category_actual_total: string; tie_ok: boolean
}
interface Unmapped {
  account_code: string; nc_cc_code: string | null
  income_expense_code: string | null; income_expense_name: string | null
  actual: string; line_count: number
}
interface GridResp { period: string; categories: Category[]; unmapped: Unmapped[] }

function money(v: string | number) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
function thisMonth() { return new Date().toISOString().slice(0, 7) }

interface Drill { accountCode: string; dimsValues?: string | null; title: string }

export default function BudgetActualPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [period, setPeriod] = useState(thisMonth())
  const [drill, setDrill] = useState<Drill | null>(null)
  const [jvId, setJvId] = useState<string | null>(null)

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })

  const { data, isFetching } = useQuery({
    queryKey: ['budget-actual-grid', period],
    queryFn: () => financeApi.get<GridResp>(`/gl/budget-actual-grid?period=${period}`),
  })

  const unmapped = useMemo(() => data?.unmapped ?? [], [data])

  const onJvActed = () => {
    qc.invalidateQueries({ queryKey: ['account-balance'] })
    qc.invalidateQueries({ queryKey: ['ab-expand'] })
    qc.invalidateQueries({ queryKey: ['ab-vouchers'] })
    qc.invalidateQueries({ queryKey: ['budget-actual-grid'] })
  }

  const openDetailDrill = (accountCode: string, r: DetailRow) => {
    const parts = [`cost_center:${r.cost_center_id ?? 'none'}`]
    if (r.income_expense_item_id) parts.push(`income_expense_item:${r.income_expense_item_id}`)
    setDrill({
      accountCode, dimsValues: parts.join(','),
      title: `Vouchers — ${accountCode} · ${r.cost_center_code ?? 'no cost center'} · ${r.income_expense_code ?? '—'} · ${period}`,
    })
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/budget-actual"
      title="Budget vs Actual"
      subtitle="Budget (approved plan) vs NC posted actual, per cost center × income-expense item (CAD)"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4">
          <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)}
                 className={cn(inputCls, 'w-40')} />
        </div>

        {isFetching && !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="space-y-5">
            {(data?.categories ?? []).map((cat) => (
              <div key={cat.account_code} className="overflow-hidden rounded-lg border border-neutral-200">
                <div className="flex items-center justify-between bg-neutral-50 px-3 py-2">
                  <span className="text-sm font-semibold text-neutral-700">
                    {CATEGORY_LABEL[cat.category] ?? cat.category} ({cat.account_code})
                  </span>
                  <span className={cn('rounded px-2 py-0.5 text-xs font-medium',
                    cat.tie_ok ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700')}>
                    {cat.tie_ok ? 'Tied to NC' : 'Tie-out OFF'}
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-neutral-100 text-xs text-neutral-500">
                        <th className="px-3 py-2 text-left font-medium">Cost Center</th>
                        <th className="px-3 py-2 text-left font-medium">Income / Expense</th>
                        <th className="px-3 py-2 text-right font-medium">Budget</th>
                        <th className="px-3 py-2 text-right font-medium">Actual</th>
                        <th className="px-3 py-2 text-right font-medium">Variance</th>
                        <th className="px-3 py-2 w-20"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {cat.detail.length === 0 && (
                        <tr><td colSpan={6} className="px-3 py-4 text-center text-xs text-neutral-400">No actuals this period.</td></tr>
                      )}
                      {cat.detail.map((r, i) => (
                        <tr key={`${r.cost_center_id ?? 'none'}-${r.income_expense_item_id ?? i}`}
                            className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                          <td className="px-3 py-2">
                            {r.cost_center_code
                              ? <><span className="font-mono text-xs">{r.cost_center_code}</span><span className="ml-1 text-neutral-600">{r.cost_center_name}</span></>
                              : <span className="text-neutral-400">(no cost center)</span>}
                          </td>
                          <td className="px-3 py-2">
                            {r.income_expense_code
                              ? <><span className="font-mono text-xs">{r.income_expense_code}</span><span className="ml-1 text-neutral-600">{r.income_expense_name}</span></>
                              : <span className="text-neutral-400">—</span>}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-neutral-600">{money(r.budget)}</td>
                          <td className="px-3 py-2 text-right font-mono">{money(r.actual)}</td>
                          <td className={cn('px-3 py-2 text-right font-mono',
                            Number(r.variance) < 0 && 'text-red-600')}>{money(r.variance)}</td>
                          <td className="px-3 py-2 text-right">
                            <button className={linkBtn} onClick={() => openDetailDrill(cat.account_code, r)}>Vouchers</button>
                          </td>
                        </tr>
                      ))}
                      {/* category-level tie-out rows (finance tracks P/D by category, not cost center) */}
                      {[{ label: 'Payroll', v: cat.payroll_actual }, { label: 'Depreciation', v: cat.depreciation_actual }]
                        .filter((x) => Number(x.v) !== 0)
                        .map((x) => (
                          <tr key={x.label} className="border-t border-neutral-100 bg-neutral-50 text-neutral-500">
                            <td className="px-3 py-2 italic" colSpan={2}>{x.label} (category-level)</td>
                            <td className="px-3 py-2 text-right">—</td>
                            <td className="px-3 py-2 text-right font-mono">{money(x.v)}</td>
                            <td className="px-3 py-2 text-right">—</td>
                            <td></td>
                          </tr>
                        ))}
                    </tbody>
                    <tfoot>
                      <tr className="border-t border-neutral-200 bg-neutral-50 font-semibold">
                        <td className="px-3 py-2" colSpan={3}>Category total</td>
                        <td className="px-3 py-2 text-right font-mono">{money(cat.category_actual_total)}</td>
                        <td colSpan={2}></td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              </div>
            ))}

            {unmapped.length > 0 && (
              <div className="overflow-hidden rounded-lg border border-amber-200">
                <div className="bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-800">
                  Exceptions — posted lines with no cost center ({unmapped.length}). Fix in NC or add to the mapping.
                </div>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-neutral-100 text-xs text-neutral-500">
                      <th className="px-3 py-2 text-left font-medium">Account</th>
                      <th className="px-3 py-2 text-left font-medium">NC Cost Center</th>
                      <th className="px-3 py-2 text-left font-medium">Income / Expense</th>
                      <th className="px-3 py-2 text-right font-medium">Actual</th>
                      <th className="px-3 py-2 text-right font-medium">Lines</th>
                      <th className="px-3 py-2 w-20"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {unmapped.map((u, i) => (
                      <tr key={`${u.account_code}-${u.nc_cc_code}-${i}`}
                          className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                        <td className="px-3 py-2 font-mono text-xs">{u.account_code}</td>
                        <td className="px-3 py-2 font-mono text-xs">{u.nc_cc_code ?? '—'}</td>
                        <td className="px-3 py-2">
                          {u.income_expense_code
                            ? <><span className="font-mono text-xs">{u.income_expense_code}</span><span className="ml-1 text-neutral-600">{u.income_expense_name}</span></>
                            : <span className="text-neutral-400">—</span>}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">{money(u.actual)}</td>
                        <td className="px-3 py-2 text-right font-mono text-neutral-500">{u.line_count}</td>
                        <td className="px-3 py-2 text-right">
                          <button className={linkBtn}
                                  onClick={() => setDrill({
                                    accountCode: u.account_code, dimsValues: 'cost_center:none',
                                    title: `Vouchers — ${u.account_code} · no cost center · ${period}`,
                                  })}>Vouchers</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>

      {drill && (
        <AccountVouchersModal accountCode={drill.accountCode} period={period}
                              dimsValues={drill.dimsValues} title={drill.title}
                              onClose={() => setDrill(null)} onOpenJv={(id) => setJvId(id)} />
      )}
      {jvId && (
        <JvDetailModal jvId={jvId} canAct={perms?.can_act ?? false} onClose={() => setJvId(null)} onActed={onJvActed} />
      )}
    </PortalChromeLayout>
  )
}
