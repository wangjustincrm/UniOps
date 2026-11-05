/**
 * JV Detail — every voucher LINE (not voucher) of the NC book, each auxiliary
 * in its own column, with a CSV export laid out the same way so finance can
 * pivot on department / income-expense item / cost centre etc. (user,
 * 2026-10-29). A row opens the shared JvDetailModal.
 *
 * The standard list parts: SortTh headers (server-sorted — the API pages the
 * list), ListPager, and a header row locked at the top of the scrolling box
 * (the table is wide, so it scrolls both ways inside the box).
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Download, Loader2, Search, X } from 'lucide-react'
import { SortTh, sortParams, useSort } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi, financeDownload } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { ListPager } from '@/components/ListPager'
import { JvDetailModal } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

const PAGE_SIZE = 50
const SUBSYSTEMS = [
  ['GL', 'General Ledger'], ['AP', 'Accounts Payable'], ['AR', 'Accounts Receivable'],
  ['FA', 'Fixed Assets'], ['CM', 'Cash Management'], ['IA', 'Inventory Accounting'],
  ['EGL', 'Exchange Gain/Loss'], ['PLCF', 'Gain/Loss Carry-Forward'], ['OT', 'OT'],
] as const

interface JvLineRow {
  jv_id: string; jv_number: string; voucher_word: string
  voucher_date: string; fiscal_period: string; nc_num: number | null
  line_no: number; account_code: string | null; account_name: string | null
  summary: string | null; currency: string
  orig_debit: string; orig_credit: string; local_debit: string; local_credit: string
  /** dim_code -> "code/name" (the code alone when no name is known). */
  aux: Record<string, string>
}
interface JvLinesResp {
  total: number
  dim_columns: { code: string; label: string }[]
  items: JvLineRow[]
}

function money(v: string) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : ''
}
function thisMonth() { return new Date().toISOString().slice(0, 7) }

export default function JvLinesPage() {
  const { user } = useAuthStore()
  const [periodFrom, setPeriodFrom] = useState(thisMonth())
  const [periodTo, setPeriodTo] = useState(thisMonth())
  const [subsystem, setSubsystem] = useState('')
  const [account, setAccount] = useState('')
  const [currency, setCurrency] = useState('')
  const [auxCode, setAuxCode] = useState('')
  const [auxValue, setAuxValue] = useState('')
  const [qInput, setQInput] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // Keys are the API's sort whitelist; an auxiliary column sorts as `aux:<code>`.
  const [sort, onSort] = useSort<string>(null, () => setPage(0))

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })
  const { data: opts } = useQuery({
    queryKey: ['jv-filter-options'],
    queryFn: () => financeApi.get<{ aux_codes: string[] }>('/journal-vouchers/filter-options'),
  })
  const { data: auxValues } = useQuery({
    queryKey: ['jv-aux-values', auxCode],
    enabled: !!auxCode,
    queryFn: () => financeApi.get<{ items: string[] }>(
      `/journal-vouchers/aux-values?dim_code=${encodeURIComponent(auxCode)}`),
  })

  // The same filters drive the page and the export, so the file is exactly
  // what the screen shows, all pages of it.
  const filters = useMemo(() => {
    const p = new URLSearchParams()
    if (periodFrom) p.set('period_from', periodFrom)
    if (periodTo) p.set('period_to', periodTo)
    if (subsystem) p.set('source_subsystem', subsystem)
    if (account) p.set('account_code', account)
    if (currency) p.set('currency', currency)
    if (auxCode) {
      p.set('aux_code', auxCode)
      if (auxValue) p.set('aux_value', auxValue)
    }
    if (q) p.set('q', q)
    // The export follows the screen's order too.
    const { sort: sortKey, dir } = sortParams(sort)
    if (sortKey && dir) { p.set('sort', sortKey); p.set('dir', dir) }
    return p
  }, [periodFrom, periodTo, subsystem, account, currency, auxCode, auxValue, q, sort])

  const list = useQuery({
    queryKey: ['jv-lines', filters.toString(), page],
    queryFn: () => {
      const p = new URLSearchParams(filters)
      p.set('limit', String(PAGE_SIZE)); p.set('offset', String(page * PAGE_SIZE))
      return financeApi.get<JvLinesResp>(`/journal-vouchers/lines?${p}`)
    },
  })
  const items = list.data?.items ?? []
  const total = list.data?.total ?? 0
  const dimCols = list.data?.dim_columns ?? []
  const dimLabel = (code: string) => dimCols.find((c) => c.code === code)?.label ?? code
  const reset = () => setPage(0)

  const exportCsv = async () => {
    setExporting(true); setErr(null)
    try {
      const name = `jv-detail-${periodFrom || 'start'}_${periodTo || 'end'}.csv`
      await financeDownload(`/journal-vouchers/lines/export?${filters}`, name)
    } catch (e) { setErr((e as Error).message) } finally { setExporting(false) }
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/jv-lines"
      title="JV Detail"
      subtitle="Every NC voucher line, one column per auxiliary — filter and export to CSV"
      headerActions={(
        <button onClick={exportCsv} disabled={exporting || total === 0} className={secondaryBtn}
                title="All lines matching the filters, auxiliaries one column each">
          {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          Export CSV
        </button>
      )}
    >
      <div className="mx-auto max-w-[110rem]">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1">
            <input type="month" value={periodFrom} aria-label="Period from"
                   onChange={(e) => { setPeriodFrom(e.target.value); reset() }}
                   className={cn(inputCls, 'w-36')} />
            <span className="text-sm text-neutral-400">–</span>
            <input type="month" value={periodTo} aria-label="Period to"
                   onChange={(e) => { setPeriodTo(e.target.value); reset() }}
                   className={cn(inputCls, 'w-36')} />
          </div>
          <select value={subsystem} onChange={(e) => { setSubsystem(e.target.value); reset() }} className={inputCls}>
            <option value="">All systems</option>
            {SUBSYSTEMS.map(([c, n]) => <option key={c} value={c}>{n}</option>)}
          </select>
          <input value={account} placeholder="Account code" aria-label="Account code"
                 onChange={(e) => { setAccount(e.target.value.trim()); reset() }}
                 className={cn(inputCls, 'w-36')} />
          <input value={currency} placeholder="Currency" aria-label="Currency"
                 onChange={(e) => { setCurrency(e.target.value.trim().toUpperCase()); reset() }}
                 className={cn(inputCls, 'w-24')} />
          <select value={auxCode} aria-label="Auxiliary"
                  onChange={(e) => { setAuxCode(e.target.value); setAuxValue(''); reset() }}
                  className={inputCls}>
            <option value="">Any auxiliary</option>
            {(opts?.aux_codes ?? []).map((c) => <option key={c} value={c}>{dimLabel(c)}</option>)}
          </select>
          <input list="jv-lines-aux-values" value={auxValue} disabled={!auxCode}
                 placeholder="Auxiliary code" aria-label="Auxiliary code"
                 onChange={(e) => { setAuxValue(e.target.value); reset() }}
                 className={cn(inputCls, 'w-40 disabled:bg-neutral-100')} />
          <datalist id="jv-lines-aux-values">
            {(auxValues?.items ?? []).map((v) => <option key={v} value={v} />)}
          </datalist>
          <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); reset() }}>
            <input value={qInput} onChange={(e) => setQInput(e.target.value)}
                   placeholder="Voucher no. / line summary…" className={cn(inputCls, 'w-60')} />
            <button type="submit" className={secondaryBtn}><Search className="h-4 w-4" /></button>
          </form>
          {(subsystem || account || currency || auxCode || q) && (
            <button type="button"
                    onClick={() => { setSubsystem(''); setAccount(''); setCurrency(''); setAuxCode(''); setAuxValue(''); setQ(''); setQInput(''); reset() }}
                    className="flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-700">
              <X className="h-3.5 w-3.5" /> Clear
            </button>
          )}
        </div>

        {err && <div className="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}

        {list.isFetching && !list.data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <>
            <div className="max-h-[calc(100vh-15rem)] min-h-[16rem] overflow-auto rounded-lg border border-neutral-200">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10 bg-neutral-50 text-left text-xs text-neutral-500 shadow-[0_1px_0_#e5e5e5]">
                  <tr>
                    <SortTh label="Date" sortKey="voucher_date" sort={sort} onSort={onSort} className="px-3 py-2 whitespace-nowrap" />
                    <SortTh label="Voucher No." sortKey="jv_number" sort={sort} onSort={onSort} className="px-3 py-2 whitespace-nowrap" />
                    <SortTh label="#" sortKey="line_no" sort={sort} onSort={onSort} className="px-3 py-2 whitespace-nowrap" title="Sort by line number" />
                    <SortTh label="Account" sortKey="account_code" sort={sort} onSort={onSort} className="px-3 py-2 whitespace-nowrap" />
                    <SortTh label="Summary" sortKey="summary" sort={sort} onSort={onSort} className="px-3 py-2 min-w-[16rem]" />
                    <SortTh label="Currency" sortKey="currency" sort={sort} onSort={onSort} className="px-3 py-2 whitespace-nowrap" />
                    <SortTh label="Debit (CAD)" sortKey="local_debit" sort={sort} onSort={onSort} align="right" className="px-3 py-2 whitespace-nowrap" />
                    <SortTh label="Credit (CAD)" sortKey="local_credit" sort={sort} onSort={onSort} align="right" className="px-3 py-2 whitespace-nowrap" />
                    {dimCols.map((c) => (
                      <SortTh key={c.code} label={c.label} sortKey={`aux:${c.code}`} sort={sort} onSort={onSort}
                              className="px-3 py-2 whitespace-nowrap" />
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 && (
                    <tr><td colSpan={8 + dimCols.length} className="px-3 py-6 text-center text-neutral-400">No voucher lines match the filters.</td></tr>
                  )}
                  {items.map((l, i) => (
                    <tr key={`${l.jv_id}-${l.line_no}`} onClick={() => setDetailId(l.jv_id)}
                        title="Open the voucher"
                        className={cn('cursor-pointer border-t border-neutral-100 align-top hover:bg-primary-50/40', i % 2 && 'bg-neutral-50/40')}>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-600 whitespace-nowrap">{l.voucher_date}</td>
                      <td className="px-3 py-2 font-mono text-xs whitespace-nowrap">{l.jv_number}</td>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-400">{l.line_no}</td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        <span className="font-mono text-xs">{l.account_code ?? '—'}</span>
                        {l.account_name && <span className="ml-1 text-xs text-neutral-600">{l.account_name}</span>}
                      </td>
                      <td className="px-3 py-2 text-neutral-700 break-words">{l.summary || '—'}</td>
                      <td className="px-3 py-2 text-xs text-neutral-600">{l.currency}</td>
                      <td className="px-3 py-2 text-right font-mono whitespace-nowrap">{money(l.local_debit)}</td>
                      <td className="px-3 py-2 text-right font-mono whitespace-nowrap">{money(l.local_credit)}</td>
                      {dimCols.map((c) => (
                        <td key={c.code} className="px-3 py-2 text-xs text-neutral-700 whitespace-nowrap">{l.aux[c.code] ?? ''}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <ListPager page={page + 1} pageSize={PAGE_SIZE} total={total} noun={total === 1 ? 'line' : 'lines'}
                       onPage={(n) => setPage(n - 1)} />
          </>
        )}
      </div>

      {detailId && (
        <JvDetailModal jvId={detailId} canAct={perms?.can_act ?? false} onClose={() => setDetailId(null)} />
      )}
    </PortalChromeLayout>
  )
}
