import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, RefreshCw, Pencil, Trash2, X } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { cn, formatDate, formatDateTime, todayISO } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

/**
 * Exchange Rates — Finance's one history of FX rates (finance-api /fx).
 *
 * Bank of Canada daily averages are fetched every business day after BoC
 * publishes (~16:30 ET). A document takes the rate of its own date; a weekend
 * or holiday takes the previous business day. Push to NC uses the invoice
 * issue date. A finance manager can override a day (kept as "manual", never
 * overwritten by the fetch) — e.g. a currency BoC does not publish.
 *
 * Dates are plain YYYY-MM-DD strings end to end — never through new Date(),
 * which shifts them a day in UTC-4.
 */

interface Rate {
  id: string; from_currency: string; to_currency: string; rate: string
  effective_date: string; source: 'boc' | 'manual'; note: string | null; updated_at: string | null
}
interface RunOut {
  started_at: string; finished_at: string | null; trigger: string
  date_from: string; date_to: string; ok: boolean | null; rows_upserted: number; message: string | null
}
interface Status {
  last_run: RunOut | null; last_ok_run: RunOut | null
  currencies: { currency: string; latest_date: string; rows: number }[]
  fetch_currencies: string[]
}

const inputCls = 'h-9 rounded-lg border border-line-strong bg-surface px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const PAGE_SIZE = 100

/** Today at the plant (company time zone), YYYY-MM-DD. */
function todayLocal(): string {
  return todayISO()
}
function fmtTs(iso: string | null): string {
  return iso ? formatDateTime(iso) : '—'
}

export default function ExchangeRatesPage() {
  const qc = useQueryClient()
  const [currency, setCurrency] = useState('USD')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [page, setPage] = useState(0)
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [fetchFrom, setFetchFrom] = useState(todayLocal())
  const [fetching, setFetching] = useState(false)
  const [edit, setEdit] = useState<{ from_currency: string; effective_date: string; rate: string; note: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [probe, setProbe] = useState({ currency: 'USD', on: todayLocal() })

  const { data: status } = useQuery({ queryKey: ['fx-status'], queryFn: () => financeApi.get<Status>('/fx/status') })
  const { data: perms } = useQuery({
    queryKey: ['coa-permissions'],
    queryFn: () => financeApi.get<{ can_manage: boolean }>('/coa/permissions'),
  })
  const canManage = !!perms?.can_manage
  const { data, isFetching } = useQuery({
    queryKey: ['fx-rates', currency, dateFrom, dateTo, page],
    queryFn: () => {
      const qs = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) })
      if (currency) qs.set('currency', currency)
      if (dateFrom) qs.set('date_from', dateFrom)
      if (dateTo) qs.set('date_to', dateTo)
      return financeApi.get<{ total: number; items: Rate[] }>(`/fx/rates?${qs.toString()}`)
    },
  })
  const { data: probeHit, isFetching: probing } = useQuery({
    queryKey: ['fx-rate', probe.currency, probe.on],
    queryFn: () => financeApi.get<{ rate: string | null; effective_date: string | null; source: string | null }>(
      `/fx/rate?currency=${encodeURIComponent(probe.currency)}&on=${probe.on}`),
    enabled: !!probe.currency && /^\d{4}-\d{2}-\d{2}$/.test(probe.on),
  })
  const rows = data?.items ?? []
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const currencies = Array.from(new Set([...(status?.fetch_currencies ?? []),
                                         ...(status?.currencies ?? []).map((c) => c.currency)])).sort()

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['fx-rates'] })
    qc.invalidateQueries({ queryKey: ['fx-status'] })
    qc.invalidateQueries({ queryKey: ['fx-rate'] })
  }

  const runFetch = async () => {
    if (fetching) return
    setFetching(true); setBanner(null)
    try {
      const r = await financeApi.post<RunOut>('/fx/fetch', { date_from: fetchFrom })
      setBanner(r.ok
        ? { kind: 'ok', text: `Fetched from Bank of Canada: ${r.rows_upserted} rate(s) ${formatDate(r.date_from)} → ${formatDate(r.date_to)}.` }
        : { kind: 'err', text: `Fetch finished with problems: ${r.message ?? 'unknown'}` })
      refresh()
    } catch (e) {
      setBanner({ kind: 'err', text: (e as Error).message })
    } finally { setFetching(false) }
  }

  const saveOverride = async () => {
    if (!edit || saving) return
    setSaving(true); setBanner(null)
    try {
      await financeApi.put('/fx/rates', edit)
      setBanner({ kind: 'ok', text: `Saved ${edit.from_currency} ${formatDate(edit.effective_date)} = ${edit.rate} (manual).` })
      setEdit(null)
      refresh()
    } catch (e) {
      setBanner({ kind: 'err', text: (e as Error).message })
    } finally { setSaving(false) }
  }

  const removeOverride = async (r: Rate) => {
    if (!window.confirm(`Remove the manual ${r.from_currency} rate of ${formatDate(r.effective_date)}? The next fetch brings the Bank of Canada rate back.`)) return
    try {
      await financeApi.delete(`/fx/rates/${r.id}`)
      refresh()
    } catch (e) {
      setBanner({ kind: 'err', text: (e as Error).message })
    }
  }

  const last = status?.last_run
  return (
    <PortalChromeLayout activeKey="portal:/finance/fx-rates" title="Exchange Rates"
                        subtitle="Bank of Canada daily rates — a document takes its own date's rate; a weekend or holiday takes the previous business day">
      <div className="mx-auto max-w-6xl space-y-4">
        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-lg border border-line bg-surface p-3 text-sm">
            <div className="text-xs font-medium uppercase text-fg-muted">Last fetch</div>
            {last ? (
              <div className="mt-1">
                <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium',
                  last.ok ? 'bg-success-soft text-success-fg' : 'bg-danger-soft text-danger-fg')}>
                  {last.ok ? 'OK' : 'Problem'}</span>
                <span className="ml-2 text-fg-secondary">{fmtTs(last.started_at)} · {last.trigger}</span>
                {last.message && <div className="mt-1 text-xs text-danger-fg">{last.message}</div>}
              </div>
            ) : <div className="mt-1 text-fg-muted">Never</div>}
          </div>
          <div className="rounded-lg border border-line bg-surface p-3 text-sm">
            <div className="text-xs font-medium uppercase text-fg-muted">Latest rate per currency</div>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
              {(status?.currencies ?? []).map((c) => (
                <span key={c.currency} className="text-fg-secondary"><b>{c.currency}</b> {formatDate(c.latest_date)}</span>
              ))}
              {(status?.currencies ?? []).length === 0 && <span className="text-fg-muted">No rates yet</span>}
            </div>
          </div>
          <div className="rounded-lg border border-line bg-surface p-3 text-sm">
            <div className="text-xs font-medium uppercase text-fg-muted">Rate for a document dated</div>
            <div className="mt-1 flex items-center gap-2">
              <select value={probe.currency} onChange={(e) => setProbe({ ...probe, currency: e.target.value })}
                      className={cn(inputCls, 'h-8 w-24')}>
                {currencies.map((c) => <option key={c}>{c}</option>)}
              </select>
              <input type="date" value={probe.on} onChange={(e) => setProbe({ ...probe, on: e.target.value })}
                     className={cn(inputCls, 'h-8 w-40')} />
            </div>
            <div className="mt-1 font-mono text-fg">
              {probing ? '…' : probeHit?.rate
                ? <>{probeHit.rate} <span className="text-xs text-fg-muted">({formatDate(probeHit.effective_date)}, {probeHit.source})</span></>
                : <span className="text-danger-fg">No rate — Push to NC would refuse</span>}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <select value={currency} onChange={(e) => { setCurrency(e.target.value); setPage(0) }} className={cn(inputCls, 'w-32')}>
            <option value="">All currencies</option>
            {currencies.map((c) => <option key={c}>{c}</option>)}
          </select>
          <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(0) }} className={cn(inputCls, 'w-40')} />
          <span className="text-fg-muted">→</span>
          <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(0) }} className={cn(inputCls, 'w-40')} />
          <div className="ml-auto flex items-center gap-2">
            <span className="text-sm text-fg-secondary">Fetch from</span>
            <input type="date" value={fetchFrom} onChange={(e) => setFetchFrom(e.target.value)} className={cn(inputCls, 'w-40')} />
            <button onClick={runFetch} disabled={fetching || !fetchFrom}
                    title="Bring in Bank of Canada rates from this date to today"
                    className="flex items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-fg-secondary hover:bg-surface-subtle disabled:opacity-50">
              {fetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              Fetch from Bank of Canada
            </button>
            {canManage && (
              <button onClick={() => setEdit({ from_currency: currency || 'USD', effective_date: todayLocal(), rate: '', note: '' })}
                      className="flex items-center gap-1.5 rounded-lg bg-primary-700 px-3 py-2 text-sm font-medium text-white hover:bg-primary-800">
                <Pencil className="h-4 w-4" /> Set a rate
              </button>
            )}
          </div>
        </div>

        {banner && (
          <div className={cn('rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-danger-soft text-danger-fg' : 'bg-success-soft text-success-fg')}>{banner.text}</div>
        )}

        {edit && (
          <div className="rounded-lg border border-line bg-surface p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm font-medium text-fg">Set a rate by hand (kept as manual; the daily fetch will not overwrite it)</div>
              <button onClick={() => setEdit(null)} className="text-fg-muted hover:text-fg-secondary"><X className="h-4 w-4" /></button>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-xs text-fg-muted">Currency
                <input value={edit.from_currency} onChange={(e) => setEdit({ ...edit, from_currency: e.target.value.toUpperCase() })}
                       className={cn(inputCls, 'mt-1 block w-24')} /></label>
              <label className="text-xs text-fg-muted">Date
                <input type="date" value={edit.effective_date} onChange={(e) => setEdit({ ...edit, effective_date: e.target.value })}
                       className={cn(inputCls, 'mt-1 block w-40')} /></label>
              <label className="text-xs text-fg-muted">CAD per 1 unit
                <input value={edit.rate} onChange={(e) => setEdit({ ...edit, rate: e.target.value })} inputMode="decimal"
                       className={cn(inputCls, 'mt-1 block w-32 font-mono')} /></label>
              <label className="flex-1 text-xs text-fg-muted">Note (required)
                <input value={edit.note} onChange={(e) => setEdit({ ...edit, note: e.target.value })}
                       placeholder="Why this rate" className={cn(inputCls, 'mt-1 block w-full')} /></label>
              <button onClick={saveOverride}
                      disabled={saving || !edit.rate || !(Number(edit.rate) > 0) || !edit.note.trim() || !edit.effective_date}
                      className="rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50">
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Save'}
              </button>
            </div>
          </div>
        )}

        <div className="overflow-hidden rounded-lg border border-line">
          <table className="w-full text-sm">
            <thead className="bg-surface-subtle text-left text-xs text-fg-muted">
              <tr>
                <th className="px-3 py-2 w-32">Date</th>
                <th className="px-3 py-2 w-28">Currency</th>
                <th className="px-3 py-2 w-36 text-right">CAD per unit</th>
                <th className="px-3 py-2 w-40">Source</th>
                <th className="px-3 py-2">Note</th>
                <th className="px-3 py-2 w-16" />
              </tr>
            </thead>
            <tbody>
              {isFetching && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-fg-muted"><Loader2 className="mx-auto h-5 w-5 animate-spin" /></td></tr>
              )}
              {!isFetching && rows.length === 0 && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-fg-muted">No rates. Use “Fetch from Bank of Canada”.</td></tr>
              )}
              {!isFetching && rows.map((r, i) => (
                <tr key={r.id} className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40')}>
                  <td className="px-3 py-2 font-mono text-xs">{formatDate(r.effective_date)}</td>
                  <td className="px-3 py-2">{r.from_currency} → {r.to_currency}</td>
                  <td className="px-3 py-2 text-right font-mono">{Number(r.rate).toFixed(4)}</td>
                  <td className="px-3 py-2">
                    <span className={cn('whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium',
                      r.source === 'manual' ? 'bg-warning-soft text-warning-fg' : 'bg-info-soft text-info-fg')}>
                      {r.source === 'manual' ? 'Manual' : 'Bank of Canada'}</span>
                  </td>
                  <td className="px-3 py-2 text-xs text-fg-secondary">{r.note ?? ''}</td>
                  <td className="px-3 py-2 text-right">
                    {canManage && (
                      <span className="flex justify-end gap-2">
                        <button title="Set this day by hand" onClick={() => setEdit({ from_currency: r.from_currency, effective_date: r.effective_date, rate: r.rate, note: r.note ?? '' })}
                                className="text-fg-muted hover:text-fg-secondary"><Pencil className="h-3.5 w-3.5" /></button>
                        {r.source === 'manual' && (
                          <button title="Remove the manual rate" onClick={() => removeOverride(r)}
                                  className="text-fg-muted hover:text-danger-fg"><Trash2 className="h-3.5 w-3.5" /></button>
                        )}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between text-sm text-fg-muted">
          <span>{total.toLocaleString()} rate{total === 1 ? '' : 's'}</span>
          <div className="flex items-center gap-2">
            <button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0}
                    className="rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-sm text-fg-secondary hover:bg-surface-subtle disabled:opacity-50">Prev</button>
            <span>Page {page + 1} / {pageCount}</span>
            <button onClick={() => setPage(Math.min(pageCount - 1, page + 1))} disabled={page >= pageCount - 1}
                    className="rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-sm text-fg-secondary hover:bg-surface-subtle disabled:opacity-50">Next</button>
          </div>
        </div>
      </div>
    </PortalChromeLayout>
  )
}
