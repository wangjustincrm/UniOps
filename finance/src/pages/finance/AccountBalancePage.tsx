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
import { cn, currentMonthISO } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccountVouchersModal } from './AccountVouchersModal'
import { JvDetailModal } from './JvDetailModal'

import { Checkbox, Input, TBody, TFoot, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
const inputCls = 'h-9 rounded-lg border border-line-strong bg-surface px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-primary-700 px-3 py-2 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm font-medium text-fg-secondary hover:bg-surface-subtle disabled:opacity-50'
const linkBtn = 'text-xs font-medium text-primary-700 hover:underline'

interface AbRow {
  account_code: string; account_name: string; account_type: string | null
  is_postable: boolean; level: number
  opening: string; period_debit: string; period_credit: string; closing: string
}
interface AbResp {
  period: string; rows: AbRow[]
  totals: { period_debit: string; period_credit: string; closing: string }
  balanced: boolean
}
interface DimOption { dim_code: string; label: string; supported: boolean }
interface ExpandKey { dim_code: string; id: string | null; code: string | null; name: string | null }
interface ExpandResp {
  account_code: string; period: string; dims: string[]
  rows: { keys: ExpandKey[]; opening: string; period_debit: string; period_credit: string; closing: string }[]
}

function money(v: string) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
function thisMonth() { return currentMonthISO() }   // the plant's month, not UTC

interface Drill { accountCode: string; dimsValues?: string | null; title: string }

export default function AccountBalancePage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [period, setPeriod] = useState(thisMonth())
  // NC's 科目余额表 has the same toggle: its ledger counts a voucher once NC
  // tallies it, and the report can fold in the ones only entered. Off by
  // default so the page keeps showing the ledger.
  const [includeUnposted, setIncludeUnposted] = useState(true)
  const [expanded, setExpanded] = useState<Record<string, string[]>>({})
  const [picker, setPicker] = useState<string | null>(null)   // account_code being configured
  const [drill, setDrill] = useState<Drill | null>(null)
  const [jvId, setJvId] = useState<string | null>(null)

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })

  const { data, isFetching } = useQuery({
    queryKey: ['account-balance', period, includeUnposted],
    queryFn: () => financeApi.get<AbResp>(
      `/gl/account-balance?period=${period}&include_unposted=${includeUnposted}`),
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
      subtitle={includeUnposted
        ? 'Opening / period movement / closing per account, including vouchers not yet posted (CAD)'
        : 'Opening / period movement / closing per account over posted journal vouchers (CAD)'}
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center gap-2">
          <Input type="month" value={period}
                 onChange={(e) => { setPeriod(e.target.value); setExpanded({}) }}
                 className={cn(inputCls, 'w-40')} />
          <label className="flex items-center gap-1.5 text-sm text-fg-secondary"
                 title="Fold in vouchers NC has not tallied yet, and UniOps JVs not yet posted">
            <Checkbox checked={includeUnposted}
                   onChange={(e) => { setIncludeUnposted(e.target.checked); setExpanded({}) }} />
            Include unposted
          </label>
          {data && (
            <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
              data.balanced ? 'bg-success-soft text-success-fg' : 'bg-danger-soft text-danger-fg')}>
              <Scale className="h-3 w-3" /> {data.balanced ? 'Balanced' : 'Out of balance'}
            </span>
          )}
        </div>

        {isFetching && !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
        ) : data && (
          <div className="overflow-hidden rounded-lg border border-line">
            <Table density="compact">
              <THead>
                <Tr>
                  <Th className="w-8" />
                  <Th className="w-24">Code</Th>
                  <Th>Account</Th>
                  <Th align="right" className="w-28 ">Opening</Th>
                  <Th align="right" className="w-28 ">Period Dr</Th>
                  <Th align="right" className="w-28 ">Period Cr</Th>
                  <Th align="right" className="w-28 ">Closing</Th>
                  <Th className="w-24" />
                </Tr>
              </THead>
              <TBody>
                {data.rows.length === 0 && (
                  <Tr><Td align="center" colSpan={8} className="text-fg-muted">
                    No {includeUnposted ? '' : 'posted '}activity up to {data.period}.</Td></Tr>
                )}
                {data.rows.map((r, i) => (
                  <Fragment key={r.account_code}>
                    <Tr className={cn('border-t border-line-subtle hover:bg-primary-50/40', i % 2 && 'bg-surface-subtle/40')}>
                      <Td>
                        <Button variant="subtle" size="xs" onClick={() => toggle(r.account_code)}
                               
                                title="Expand by auxiliary dimensions">
                          {expanded[r.account_code] ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </Button>
                      </Td>
                      <Td className={cn('px-3 py-2 font-mono text-xs', !r.is_postable && 'font-semibold')}>{r.account_code}</Td>
                      <Td className={cn('px-3 py-2', !r.is_postable && 'font-semibold')}
                          style={{ paddingLeft: `${12 + r.level * 18}px` }}>{r.account_name}</Td>
                      <Td align="right" className="font-mono text-fg-muted">{money(r.opening)}</Td>
                      <Td align="right" className="font-mono">{money(r.period_debit)}</Td>
                      <Td align="right" className="font-mono">{money(r.period_credit)}</Td>
                      <Td align="right" className="font-mono font-semibold">{money(r.closing)}</Td>
                      <Td align="right">
                        <button className={linkBtn}
                                onClick={() => setDrill({ accountCode: r.account_code, title: `Vouchers — ${r.account_code} ${r.account_name} · ${period}` })}>
                          Vouchers
                        </button>
                      </Td>
                    </Tr>
                    {expanded[r.account_code] && (
                      <DimExpansion accountCode={r.account_code} accountName={r.account_name}
                                    period={period} dims={expanded[r.account_code]}
                                    includeUnposted={includeUnposted} onDrill={setDrill} />
                    )}
                  </Fragment>
                ))}
              </TBody>
              <TFoot>
                <Tr className="border-t-2 bg-surface-subtle font-semibold">
                  <Td colSpan={4}>Totals</Td>
                  <Td align="right" className="font-mono">{money(data.totals.period_debit)}</Td>
                  <Td align="right" className="font-mono">{money(data.totals.period_credit)}</Td>
                  <Td align="right" className="font-mono">{money(data.totals.closing)}</Td>
                  <Td />
                </Tr>
              </TFoot>
            </Table>
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
                              includeUnposted={includeUnposted}
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
      <div className="w-full max-w-sm rounded-xl bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold text-fg">Expand {accountCode} by…</h2>
          <Button aria-label="Close" variant="subtle" size="icon-xs" onClick={onClose}>
            <X className="h-5 w-5" />
          </Button>
        </div>
        {isLoading ? (
          <div className="py-6 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
        ) : (
          <div className="space-y-2">
            {options.map((o) => (
              <label key={o.dim_code}
                     className={cn('flex items-center gap-2 text-sm',
                       o.supported ? 'text-fg-secondary' : 'cursor-not-allowed text-fg-muted')}>
                <Checkbox disabled={!o.supported}
                       checked={chosen.includes(o.dim_code)}
                       onChange={() => flip(o.dim_code)} />
                {o.label}
                {!o.supported && <span className="text-2xs">(no data yet)</span>}
              </label>
            ))}
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2 border-t border-line-subtle pt-3">
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

function DimExpansion({ accountCode, accountName, period, dims, includeUnposted, onDrill }: {
  accountCode: string; accountName: string; period: string; dims: string[]
  includeUnposted: boolean; onDrill: (d: Drill) => void
}) {
  const dimsParam = dims.join(',')
  const { data, isLoading } = useQuery({
    queryKey: ['ab-expand', accountCode, period, dimsParam, includeUnposted],
    queryFn: () => financeApi.get<ExpandResp>(
      `/gl/account-balance/${accountCode}/expand?period=${period}&dims=${dimsParam}`
      + `&include_unposted=${includeUnposted}`),
  })
  if (isLoading) {
    return (
      <Tr className="bg-surface-subtle/60">
        <Td align="center" colSpan={8}><Loader2 className="mx-auto h-4 w-4 animate-spin text-fg-muted" /></Td>
      </Tr>
    )
  }
  const rows = data?.rows ?? []
  if (rows.length === 0) {
    return (
      <Tr className="bg-surface-subtle/60">
        <Td colSpan={8} className="pl-12 text-xs text-fg-muted">No movement this period.</Td>
      </Tr>
    )
  }
  return (
    <>
      {rows.map((row, ri) => {
        const label = row.keys.map((k) =>
          k.code ? `${k.code}${k.name ? ' · ' + k.name : ''}`
                 : k.id ? '(unknown)' : '(none)').join('  |  ')
        const dv = row.keys.map((k) => `${k.dim_code}:${k.id ?? 'none'}`).join(',')
        return (
          <Tr key={ri} className="bg-surface-subtle/60 text-xs">
            <Td />
            <Td colSpan={2} className="pl-8 text-fg-secondary">{label}</Td>
            <Td align="right" className="font-mono">{money(row.opening)}</Td>
            <Td align="right" className="font-mono">{money(row.period_debit)}</Td>
            <Td align="right" className="font-mono">{money(row.period_credit)}</Td>
            <Td align="right" className="font-mono">
              {Number(row.closing) === 0 ? <span className="text-fg-muted">Balanced</span> : money(row.closing)}
            </Td>
            <Td align="right">
              <button className={linkBtn}
                      onClick={() => onDrill({
                        accountCode, dimsValues: dv,
                        title: `Vouchers — ${accountCode} ${accountName} · ${label} · ${period}`,
                      })}>
                Vouchers
              </button>
            </Td>
          </Tr>
        )
      })}
    </>
  )
}
