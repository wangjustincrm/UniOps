/**
 * Account Balance report (科目余额表) — Plan 4, 能力①②③ UI.
 * Per-account opening / period Dr / period Cr / closing over POSTED JV lines
 * (local CAD). Rows expand inline by cost center (能力②); every row and
 * expansion drills into its composing vouchers (能力③ → JvDetailModal).
 */
import { Fragment, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Loader2, Scale } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccountVouchersModal } from './AccountVouchersModal'
import { JvDetailModal } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const linkBtn = 'text-xs font-medium text-[#085E5E] hover:underline'

interface AbRow {
  account_code: string; account_name: string; account_type: string | null
  opening: string; period_debit: string; period_credit: string; closing: string
}
interface AbResp {
  period: string; rows: AbRow[]
  totals: { period_debit: string; period_credit: string; closing: string }
  balanced: boolean
}
interface ExpandRow { cost_center_id: string | null; cost_center_code: string | null; cost_center_name: string | null; amount: string }
interface ExpandResp { account_code: string; period: string; rows: ExpandRow[] }

function money(v: string) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
function thisMonth() { return new Date().toISOString().slice(0, 7) }

interface Drill { accountCode: string; costCenterId?: string | null; title: string }

export default function AccountBalancePage() {
  const { user } = useAuthStore()
  const [period, setPeriod] = useState(thisMonth())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [drill, setDrill] = useState<Drill | null>(null)
  const [jvId, setJvId] = useState<string | null>(null)

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })

  const { data, isFetching } = useQuery({
    queryKey: ['account-balance', period],
    queryFn: () => financeApi.get<AbResp>(`/gl/account-balance?period=${period}`),
  })

  const toggle = (code: string) => setExpanded((p) => {
    const n = new Set(p); if (n.has(code)) n.delete(code); else n.add(code); return n
  })

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/account-balance"
      title="Account Balance"
      subtitle="Opening / period movement / closing per account over posted journal vouchers (CAD)"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center gap-2">
          <input type="month" value={period}
                 onChange={(e) => { setPeriod(e.target.value); setExpanded(new Set()) }}
                 className={cn(inputCls, 'w-40')} />
          {data && (
            <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
              data.balanced ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700')}>
              <Scale className="h-3 w-3" /> {data.balanced ? 'Balanced' : 'Out of balance'}
            </span>
          )}
        </div>

        {isFetching && !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : data && (
          <div className="overflow-hidden rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr>
                  <th className="w-8 px-2 py-2" />
                  <th className="px-3 py-2 w-24">Code</th>
                  <th className="px-3 py-2">Account</th>
                  <th className="px-3 py-2 w-28 text-right">Opening</th>
                  <th className="px-3 py-2 w-28 text-right">Period Dr</th>
                  <th className="px-3 py-2 w-28 text-right">Period Cr</th>
                  <th className="px-3 py-2 w-28 text-right">Closing</th>
                  <th className="px-3 py-2 w-24" />
                </tr>
              </thead>
              <tbody>
                {data.rows.length === 0 && (
                  <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">No posted activity up to {data.period}.</td></tr>
                )}
                {data.rows.map((r, i) => (
                  <Fragment key={r.account_code}>
                    <tr className={cn('border-t border-neutral-100 hover:bg-primary-50/40', i % 2 && 'bg-neutral-50/40')}>
                      <td className="px-2 py-2">
                        <button onClick={() => toggle(r.account_code)}
                                className="rounded p-0.5 text-neutral-400 hover:text-neutral-700"
                                title="Expand by cost center">
                          {expanded.has(r.account_code) ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">{r.account_code}</td>
                      <td className="px-3 py-2">{r.account_name}</td>
                      <td className="px-3 py-2 text-right font-mono text-neutral-500">{money(r.opening)}</td>
                      <td className="px-3 py-2 text-right font-mono">{money(r.period_debit)}</td>
                      <td className="px-3 py-2 text-right font-mono">{money(r.period_credit)}</td>
                      <td className="px-3 py-2 text-right font-mono font-semibold">{money(r.closing)}</td>
                      <td className="px-3 py-2 text-right">
                        <button className={linkBtn}
                                onClick={() => setDrill({ accountCode: r.account_code, title: `Vouchers — ${r.account_code} ${r.account_name} · ${period}` })}>
                          Vouchers
                        </button>
                      </td>
                    </tr>
                    {expanded.has(r.account_code) && (
                      <CostCenterExpansion accountCode={r.account_code} accountName={r.account_name}
                                           period={period} onDrill={setDrill} />
                    )}
                  </Fragment>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-neutral-200 bg-neutral-50 font-semibold">
                  <td className="px-3 py-2" colSpan={4}>Totals</td>
                  <td className="px-3 py-2 text-right font-mono">{money(data.totals.period_debit)}</td>
                  <td className="px-3 py-2 text-right font-mono">{money(data.totals.period_credit)}</td>
                  <td className="px-3 py-2 text-right font-mono">{money(data.totals.closing)}</td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </div>

      {drill && (
        <AccountVouchersModal accountCode={drill.accountCode} period={period}
                              costCenterId={drill.costCenterId} title={drill.title}
                              onClose={() => setDrill(null)}
                              onOpenJv={(id) => setJvId(id)} />
      )}
      {jvId && (
        <JvDetailModal jvId={jvId} canAct={perms?.can_act ?? false} onClose={() => setJvId(null)} />
      )}
    </PortalChromeLayout>
  )
}

function CostCenterExpansion({ accountCode, accountName, period, onDrill }: {
  accountCode: string; accountName: string; period: string; onDrill: (d: Drill) => void
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['ab-expand', accountCode, period],
    queryFn: () => financeApi.get<ExpandResp>(`/gl/account-balance/${accountCode}/expand?period=${period}`),
  })
  if (isLoading) {
    return (
      <tr className="border-t border-neutral-100 bg-neutral-50/60">
        <td colSpan={8} className="px-3 py-3 text-center"><Loader2 className="mx-auto h-4 w-4 animate-spin text-neutral-400" /></td>
      </tr>
    )
  }
  const rows = data?.rows ?? []
  if (rows.length === 0) {
    return (
      <tr className="border-t border-neutral-100 bg-neutral-50/60">
        <td colSpan={8} className="px-3 py-2 pl-12 text-xs text-neutral-400">No movement this period.</td>
      </tr>
    )
  }
  return (
    <>
      {rows.map((cc) => (
        <tr key={cc.cost_center_id ?? 'none'} className="border-t border-neutral-100 bg-neutral-50/60 text-xs">
          <td />
          <td colSpan={2} className="px-3 py-1.5 pl-8 text-neutral-600">
            {cc.cost_center_code ? `${cc.cost_center_code} · ${cc.cost_center_name ?? ''}` : '(no cost center)'}
          </td>
          <td colSpan={4} className="px-3 py-1.5 text-right font-mono">{money(cc.amount)}</td>
          <td className="px-3 py-1.5 text-right">
            <button className={linkBtn}
                    onClick={() => onDrill({
                      accountCode, costCenterId: cc.cost_center_id,
                      title: `Vouchers — ${accountCode} ${accountName} · ${cc.cost_center_code ?? 'no cost center'} · ${period}`,
                    })}>
              Vouchers
            </button>
          </td>
        </tr>
      ))}
    </>
  )
}
