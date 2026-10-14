// Inventory → Reconciliation (design §7.3, §8): WMS × NC stock (IC) × NC cost
// (IA) × NC GL on one date, every difference attributed to a named cause
// where one can be computed; what remains above tolerance is "unexplained".
//
// Every control lives in the URL so a view is linkable and the tab shell
// restores it. Absent params mean the defaults (date = last month end,
// status = Unexplained + Explained, unposted vouchers included). The date is
// a plain "YYYY-MM-DD" on Toronto's clock, never passed through new Date().
import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { Download, Info, Loader2, Search, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatMoney, formatQty } from '@/lib/number'
import { previousMonthEnd, todayToronto } from '@/lib/time'
import { MATERIAL_CLASSES } from '@/pages/nc/ncApi'
import { EmptyRow, ErrorBox, LoadingRows, Pager, SortTh } from '@/pages/nc/NcUi'
import { useDebounced } from '@/pages/nc/useDebounced'
import { usePageFor } from '@/pages/nc/usePageFor'
import { reconApi, STATUSES, type ReconSort, type ReconStatus } from './reconApi'
import { GlExtraTable, ReconRowView } from './ReconRows'
import { exportReconciliation } from './exportRecon'
import { ScrollTable, stickyHeadCls } from '@/components/ScrollTable'

const PAGE_SIZE = 100
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/
const DEFAULT_STATUS: ReconStatus[] = ['unexplained', 'explained']
const SORTS: { key: ReconSort; label: string }[] = [
  { key: 'material', label: 'Material' },
  // The API orders difference sorts by the UNEXPLAINED remainder, largest
  // first (recon.py sort_note) — not by the raw difference.
  { key: 'ic_wms', label: 'Largest unexplained IC−WMS' },
  { key: 'ia_ic', label: 'Largest unexplained IA−IC' },
  { key: 'gl_ia', label: 'Largest unexplained GL−IA' },
]

const selectCls =
  'h-9 rounded-lg border border-neutral-300 bg-white px-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500'

function Chip({ active, onClick, children, title }: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
  title?: string
}) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick} title={title}
      className={cn('rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors',
        active ? 'border-primary-500 bg-primary-50 text-primary-700' : 'border-neutral-300 bg-white text-neutral-500 hover:bg-neutral-50')}>
      {children}
    </button>
  )
}

function Tile({ label, value, sub, title }: { label: string; value: string; sub?: string; title?: string }) {
  return (
    <div className="min-w-[9rem] rounded-lg border border-neutral-200 bg-white px-3 py-2" title={title}>
      <p className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">{label}</p>
      <p className="mt-0.5 font-mono text-sm text-neutral-800">{value}</p>
      {sub && <p className="font-mono text-[11px] text-neutral-500">{sub}</p>}
    </div>
  )
}

const STATUS_TILE: Record<ReconStatus, string> = {
  ok: 'border-success-200 text-success-700',
  explained: 'border-primary-200 text-primary-700',
  unexplained: 'border-danger-200 text-danger-700',
}

function listParam(raw: string | null): string[] {
  return raw ? raw.split(',').filter(Boolean) : []
}

export default function ReconciliationPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const today = todayToronto()
  const defaultDate = previousMonthEnd(today)

  const rawDate = searchParams.get('date')
  const date = rawDate && DATE_ONLY.test(rawDate) && rawDate <= today ? rawDate : defaultDate
  const rawClass = searchParams.get('class') ?? ''
  const classCode = MATERIAL_CLASSES.some((c) => c.code === rawClass) ? rawClass : ''
  const statusParam = searchParams.get('status')
  const parsedStatus = listParam(statusParam).filter((s): s is ReconStatus => STATUSES.some((x) => x.key === s))
  const status: ReconStatus[] = statusParam === null || parsedStatus.length === 0 ? DEFAULT_STATUS : parsedStatus
  const category = listParam(searchParams.get('cat'))
  const expandBatches = searchParams.get('batches') === '1'
  const includeUnposted = searchParams.get('unposted') !== '0'
  const rawSort = searchParams.get('sort')
  const sort: ReconSort = SORTS.some((s) => s.key === rawSort) ? (rawSort as ReconSort) : 'material'
  const urlSearch = searchParams.get('q') ?? ''

  const [searchInput, setSearchInput] = useState(urlSearch)
  const search = useDebounced(searchInput.trim())
  // Mirror the debounced search into the URL (navigation is a side effect,
  // so it runs after render, not during it).
  useEffect(() => {
    if (search === urlSearch) return
    const next = new URLSearchParams(searchParams)
    if (search) next.set('q', search)
    else next.delete('q')
    setSearchParams(next, { replace: true })
  }, [search, urlSearch, searchParams, setSearchParams])

  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<unknown>(null)

  function setParams(changes: Record<string, string>) {
    const next = new URLSearchParams(searchParams)
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    // replace, not push: filter changes should not fill the back stack.
    setSearchParams(next, { replace: true })
  }

  function setStatus(list: ReconStatus[]) {
    const same = list.length === DEFAULT_STATUS.length && DEFAULT_STATUS.every((s) => list.includes(s))
    setParams({ status: same ? '' : list.join(',') })
  }

  function toggleStatus(s: ReconStatus) {
    const next = status.includes(s) ? status.filter((x) => x !== s) : [...status, s]
    if (next.length === 0) return // at least one status stays selected
    setStatus(STATUSES.map((x) => x.key).filter((k) => next.includes(k)))
  }

  function toggleCategory(code: string) {
    const next = category.includes(code) ? category.filter((x) => x !== code) : [...category, code]
    setParams({ cat: next.join(',') })
  }

  const filters = {
    date, expand_batches: expandBatches, status, category,
    class_code: classCode || undefined, search: search || undefined,
    include_unposted: includeUnposted, sort,
  }
  const filterKey = JSON.stringify(filters)
  const [page, setPage] = usePageFor(filterKey)

  const q = useQuery({
    queryKey: ['recon', filters, page],
    queryFn: () => reconApi.get({ ...filters, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  })
  const data = q.data
  const showAmt = data?.amounts_visible ?? false
  const cols = showAmt ? 17 : 14

  function toggleRow(code: string) {
    setExpanded((cur) => {
      const next = new Set(cur)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  }

  async function onExport() {
    setExporting(true)
    setExportError(null)
    try {
      const summary = [
        `status: ${status.join(', ')}`,
        category.length ? `causes: ${category.join(', ')}` : '',
        classCode ? `class: ${classCode}` : '',
        search ? `search: "${search}"` : '',
        expandBatches ? 'batches expanded' : '',
      ].filter(Boolean).join(' · ')
      await exportReconciliation(filters, summary)
    } catch (e) {
      setExportError(e)
    } finally {
      setExporting(false)
    }
  }

  const t = data?.totals

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-neutral-900">Reconciliation</h1>
          <p className="text-xs text-neutral-500">
            WMS vs NC stock (IC) vs NC cost (IA) vs NC general ledger (GL), with each difference attributed to a cause
          </p>
        </div>
        <button type="button" onClick={onExport} disabled={exporting || !data}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:opacity-50">
          {exporting ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Download aria-hidden className="h-4 w-4" />}
          {exporting ? 'Exporting…' : 'Export'}
        </button>
      </header>

      <div className="flex flex-col gap-2.5 rounded-lg border border-neutral-200 bg-white px-3 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-neutral-600">
            Date
            <input type="date" value={date} max={today}
              onChange={(e) => {
                const v = e.target.value
                // Keep the URL clean on the default so a bookmark keeps meaning "last month end".
                if (v && DATE_ONLY.test(v) && v <= today) setParams({ date: v === defaultDate ? '' : v })
              }}
              className={selectCls} />
          </label>
          <label className="flex items-center gap-2 text-xs text-neutral-600">
            Material class
            <select value={classCode} onChange={(e) => setParams({ class: e.target.value })} className={selectCls}>
              <option value="">All</option>
              {MATERIAL_CLASSES.map((c) => <option key={c.code} value={c.code}>{c.code} {c.label}</option>)}
            </select>
          </label>
          <div className="flex h-9 w-64 items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3">
            <Search aria-hidden className="h-4 w-4 shrink-0 text-neutral-400" />
            <input value={searchInput} onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Material or name…" aria-label="Search" maxLength={100}
              className="w-full text-sm focus:outline-none" />
            {searchInput && (
              <button type="button" onClick={() => setSearchInput('')} aria-label="Clear search"
                className="shrink-0 text-neutral-400 hover:text-neutral-700">
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
          <label className="flex items-center gap-2 text-xs text-neutral-600">
            Sort
            <select value={sort} onChange={(e) => setParams({ sort: e.target.value === 'material' ? '' : e.target.value })}
              className={selectCls}>
              {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Status">
            <span className="text-xs text-neutral-500">Status</span>
            {STATUSES.map((s) => (
              <Chip key={s.key} active={status.includes(s.key)} onClick={() => toggleStatus(s.key)}>{s.label}</Chip>
            ))}
          </div>
          <label className="flex items-center gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={expandBatches} onChange={(e) => setParams({ batches: e.target.checked ? '1' : '' })}
              className="h-4 w-4 rounded border-neutral-300" />
            Expand batches
          </label>
          <label className="flex items-center gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={includeUnposted} onChange={(e) => setParams({ unposted: e.target.checked ? '' : '0' })}
              className="h-4 w-4 rounded border-neutral-300" />
            Include unposted GL vouchers
          </label>
        </div>

        {data && (
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Causes">
            <span className="text-xs text-neutral-500">Causes</span>
            {data.categories.map((c) => (
              <Chip key={c.code} active={category.includes(c.code)} onClick={() => toggleCategory(c.code)} title={c.label}>
                {c.label}
              </Chip>
            ))}
            {category.length > 0 && (
              <button type="button" onClick={() => setParams({ cat: '' })}
                className="text-xs text-neutral-500 underline hover:text-neutral-800">Clear</button>
            )}
          </div>
        )}
      </div>

      {data && (
        <div className="flex flex-col gap-1.5">
          <p className="text-xs text-neutral-500">
            As of {data.date} · NC clock {data.nc_time_zone} · tolerance qty {data.tolerance.qty} / amount {data.tolerance.amount}
            {!data.include_unposted && ' · posted GL vouchers only'}
          </p>
          {!data.wms_live && (
            <p className="flex items-start gap-1.5 rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700">
              <Info aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              WMS went live on 2025-02-03 — no WMS figures before that date.
            </p>
          )}
        </div>
      )}

      {t && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-2">
            <Tile label="WMS" value={formatQty(t.wms)} />
            <Tile label="NC stock (WMS warehouses)" value={formatQty(t.ic_wms)} />
            <Tile label="NC stock (external)" value={formatQty(t.ic_external)} />
            <Tile label="NC stock (production line-side)" value={formatQty(t.ic_production)} />
            <Tile label="NC stock total" value={formatQty(t.ic_total)}
              title={`Includes ${formatQty(t.ic_transient)} in transient / other / unclassified warehouses`} />
            <Tile label="IA qty" value={formatQty(t.ia_qty)} sub={showAmt ? formatMoney(t.ia_amount) : undefined} />
            <Tile label="GL qty" value={formatQty(t.gl_qty)} sub={showAmt ? formatMoney(t.gl_amount) : undefined} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {(['ok', 'explained', 'unexplained'] as const).map((s) => {
              const active = status.length === 1 && status[0] === s
              return (
                <button key={s} type="button" onClick={() => setStatus([s])} aria-pressed={active}
                  title={`Show only ${STATUSES.find((x) => x.key === s)?.label} materials`}
                  className={cn('min-w-[8rem] rounded-lg border bg-white px-3 py-2 text-left hover:bg-neutral-50',
                    STATUS_TILE[s], active && 'ring-2 ring-primary-300')}>
                  <p className="text-[11px] font-medium uppercase tracking-wide">{STATUSES.find((x) => x.key === s)?.label}</p>
                  <p className="mt-0.5 font-mono text-sm">{t.by_status[s].toLocaleString('en-US')}</p>
                </button>
              )
            })}
            <span className="text-[11px] text-neutral-400">
              of {t.materials.toLocaleString('en-US')} materials · totals ignore the table filters; quantities are summed across units
            </span>
          </div>
        </div>
      )}

      {exportError != null && <ErrorBox error={exportError} fallback="Export failed." />}
      {q.isError && <ErrorBox error={q.error} fallback="Could not load the reconciliation." />}

      {!(q.isError && !data) && (
        <ScrollTable className={cn(q.isFetching && data && 'opacity-70')}>
          <table className="min-w-full text-sm">
            <thead className={stickyHeadCls}>
              <tr>
                <th className="w-8 px-2 py-2" aria-label="Expand" />
                <SortTh label="Material" sortKey="material" current={sort} onSort={(k) => setParams({ sort: k === 'material' ? '' : k })} />
                <th className="px-3 py-2 text-right">WMS</th>
                <th className="px-3 py-2 text-right" title="NC stock in the warehouses WMS manages">NC stock in WMS wh.</th>
                <th className="px-3 py-2 text-right" title="NC stock in external and production line-side warehouses">NC stock elsewhere</th>
                <th className="px-3 py-2 text-right" title="All NC warehouses, including transient">NC total</th>
                <th className="px-3 py-2 text-right">IA qty</th>
                <th className="px-3 py-2 text-right">GL qty</th>
                <SortTh label="IC−WMS" sortTitle="Sort by largest unexplained IC−WMS" sortKey="ic_wms" current={sort} align="right" descending
                  onSort={(k) => setParams({ sort: k })} />
                <SortTh label="IA−IC" sortTitle="Sort by largest unexplained IA−IC" sortKey="ia_ic" current={sort} align="right" descending
                  onSort={(k) => setParams({ sort: k })} />
                {showAmt ? <th className="px-3 py-2 text-right">GL−IA qty</th> : (
                  <SortTh label="GL−IA qty" sortTitle="Sort by largest unexplained GL−IA" sortKey="gl_ia" current={sort} align="right" descending
                    onSort={(k) => setParams({ sort: k })} />
                )}
                {showAmt && <th className="px-3 py-2 text-right">IA amount</th>}
                {showAmt && <th className="px-3 py-2 text-right">GL amount</th>}
                {showAmt && (
                  <SortTh label="GL−IA amount" sortTitle="Sort by largest unexplained GL−IA" sortKey="gl_ia" current={sort} align="right" descending
                    onSort={(k) => setParams({ sort: k })} />
                )}
                <th className="px-3 py-2 text-left">Status</th>
                <th className="px-3 py-2 text-left">Causes</th>
                <th className="px-3 py-2" aria-label="Trace" />
              </tr>
            </thead>
            <tbody>
              {q.isLoading && <LoadingRows cols={cols} />}
              {data && data.items.length === 0 && (
                <EmptyRow cols={cols}>
                  {search ? `Nothing matches "${search}".` : 'No materials match these filters on this date.'}
                </EmptyRow>
              )}
              {data?.items.map((r) => (
                <ReconRowView key={r.material_code} r={r} showAmt={showAmt} tol={data.tolerance} date={data.date} cols={cols}
                  open={expanded.has(r.material_code)} onToggle={() => toggleRow(r.material_code)}
                  onShowBatches={() => setParams({ batches: '1' })} />
              ))}
            </tbody>
          </table>
        </ScrollTable>
      )}

      <Pager page={page} pageSize={PAGE_SIZE} total={data?.total ?? 0} busy={q.isFetching} onPage={setPage} />

      {data && (
        <div className="grid gap-4 md:grid-cols-2">
          <GlExtraTable title="GL impairment (1471)" rows={data.gl_impairment} showAmt={showAmt}
            hint="Impairment provision — not attributable to a material row." />
          <GlExtraTable title="GL lines without a material" rows={data.gl_no_material} showAmt={showAmt}
            hint="Inventory-account lines that carry no material auxiliary." />
        </div>
      )}
    </div>
  )
}
