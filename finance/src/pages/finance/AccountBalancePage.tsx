/**
 * Account Balance report (科目余额表) — 能力①②③ with generic multi-dim expansion.
 * Rows expand by any subset of the account's configured aux dimensions
 * (/dims → checkbox picker → /expand?dims=a,b → grouped rows), and every
 * expansion row drills into its composing vouchers with a dims_values combo.
 */
import { Fragment, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Loader2, Scale, X } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccountVouchersModal } from './AccountVouchersModal'
import { JvDetailModal } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'
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
interface DimOption { dim_code: string; label: string; supported: boolean }
interface ExpandKey { dim_code: string; id: string | null; code: string | null; name: string | null }
interface ExpandResp { account_code: string; period: string; dims: string[]; rows: { keys: ExpandKey[]; amount: string }[] }

function money(v: string) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
function thisMonth() { return new Date().toISOString().slice(0, 7) }

interface Drill { accountCode: string; dimsValues?: string | null; title: string }

export default function AccountBalancePage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [period, setPeriod] = useState(thisMonth())
  const [expanded, setExpanded] = useState<Record<string, string[]>>({})
  const [picker, setPicker] = useState<string | null>(null)   // account_code being configured
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

  const onJvActed = () => {
    qc.invalidateQueries({ queryKey: ['account-balance'] })
    qc.invalidateQueries({ queryKey: ['ab-expand'] })
    qc.invalidateQueries({ queryKey: ['ab-vouchers'] })
    qc.invalidateQueries({ queryKey: ['budget-actual'] })
  }

  const toggle = (code: string) => {
    if (expanded[code]) {
      setExpanded((p) => { const n = { ...p }; delete n[code]; return n })
    } else {
      setPicker(code)   // choose dims before expanding
    }
  }

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
                 onChange={(e) => { setPeriod(e.target.value); setExpanded({}) }}
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
                                title="Expand by auxiliary dimensions">
                          {expanded[r.account_code] ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
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
                    {expanded[r.account_code] && (
                      <DimExpansion accountCode={r.account_code} accountName={r.account_name}
                                    period={period} dims={expanded[r.account_code]}
                                    onDrill={setDrill} />
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

      {picker && (
        <DimPickerModal accountCode={picker} onClose={() => setPicker(null)}
                        onApply={(dims) => {
                          setExpanded((p) => ({ ...p, [picker]: dims }))
                          setPicker(null)
                        }} />
      )}
      {drill && (
        <AccountVouchersModal accountCode={drill.accountCode} period={period}
                              dimsValues={drill.dimsValues} title={drill.title}
                              onClose={() => setDrill(null)}
                              onOpenJv={(id) => setJvId(id)} />
      )}
      {jvId && (
        <JvDetailModal jvId={jvId} canAct={perms?.can_act ?? false}
                       onClose={() => setJvId(null)} onActed={onJvActed} />
      )}
    </PortalChromeLayout>
  )
}

function DimPickerModal({ accountCode, onClose, onApply }: {
  accountCode: string; onClose: () => void; onApply: (dims: string[]) => void
}) {
  const [chosen, setChosen] = useState<string[]>(['cost_center'])
  const { data, isLoading } = useQuery({
    queryKey: ['ab-dims', accountCode],
    queryFn: () => financeApi.get<{ account_code: string; dims: DimOption[] }>(
      `/gl/account-balance/${accountCode}/dims`),
  })
  const flip = (d: string) => setChosen((p) =>
    p.includes(d) ? p.filter((x) => x !== d) : [...p, d])
  const options = data?.dims ?? []
  const chosenSupported = chosen.filter((c) => options.some((o) => o.dim_code === c && o.supported))

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="w-full max-w-sm rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-800">Expand {accountCode} by…</h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>
        {isLoading ? (
          <div className="py-6 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="space-y-2">
            {options.map((o) => (
              <label key={o.dim_code}
                     className={cn('flex items-center gap-2 text-sm',
                       o.supported ? 'text-neutral-700' : 'cursor-not-allowed text-neutral-400')}>
                <input type="checkbox" disabled={!o.supported}
                       checked={chosen.includes(o.dim_code)}
                       onChange={() => flip(o.dim_code)} />
                {o.label}
                {!o.supported && <span className="text-[11px]">(no data yet)</span>}
              </label>
            ))}
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2 border-t border-neutral-100 pt-3">
          <button onClick={onClose} className={secondaryBtn}>Cancel</button>
          <button onClick={() => onApply(chosenSupported)}
                  disabled={chosenSupported.length === 0} className={primaryBtn}>
            Expand
          </button>
        </div>
      </div>
    </div>
  )
}

function DimExpansion({ accountCode, accountName, period, dims, onDrill }: {
  accountCode: string; accountName: string; period: string; dims: string[]
  onDrill: (d: Drill) => void
}) {
  const dimsParam = dims.join(',')
  const { data, isLoading } = useQuery({
    queryKey: ['ab-expand', accountCode, period, dimsParam],
    queryFn: () => financeApi.get<ExpandResp>(
      `/gl/account-balance/${accountCode}/expand?period=${period}&dims=${dimsParam}`),
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
      {rows.map((row, ri) => {
        const label = row.keys.map((k) =>
          k.code ? `${k.code}${k.name ? ' · ' + k.name : ''}` : '(none)').join('  |  ')
        const dv = row.keys.map((k) => `${k.dim_code}:${k.id ?? 'none'}`).join(',')
        return (
          <tr key={ri} className="border-t border-neutral-100 bg-neutral-50/60 text-xs">
            <td />
            <td colSpan={2} className="px-3 py-1.5 pl-8 text-neutral-600">{label}</td>
            <td colSpan={4} className="px-3 py-1.5 text-right font-mono">{money(row.amount)}</td>
            <td className="px-3 py-1.5 text-right">
              <button className={linkBtn}
                      onClick={() => onDrill({
                        accountCode, dimsValues: dv,
                        title: `Vouchers — ${accountCode} ${accountName} · ${label} · ${period}`,
                      })}>
                Vouchers
              </button>
            </td>
          </tr>
        )
      })}
    </>
  )
}
