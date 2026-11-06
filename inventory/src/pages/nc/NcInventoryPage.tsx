// Inventory → NC Inventory (design §6, §7.2): NC's three ledgers — stock (IC),
// cost (IA), general ledger (GL) — as of any date.
//
// Date, material class and tab live in the URL so a view is linkable and the
// tab shell restores it. The date is a plain "YYYY-MM-DD" on Toronto's clock
// and is sent to inventory-api exactly as picked: it never goes through
// new Date() (that would read it as UTC midnight — the previous day here).
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertTriangle, BookOpen, Coins, Package, Search, X } from 'lucide-react'
import { Badge } from '@uniops/shell'
import { cn } from '@/lib/utils'
import { formatDateTimeToronto, relativeAge, todayToronto } from '@/lib/time'
import { MATERIAL_CLASSES, ncApi, type NcMeta } from './ncApi'
import { ErrorBox } from './NcUi'
import { useDebounced } from './useDebounced'
import { StockTab } from './StockTab'
import { CostTab } from './CostTab'
import { LedgerTab } from './LedgerTab'

const TABS = [
  { key: 'stock', label: 'Stock (IC)', icon: Package,
    hint: 'On-hand quantity by warehouse, material and batch — from NC inventory movements' },
  { key: 'cost', label: 'Cost (IA)', icon: Coins,
    hint: 'Quantity and value per material — from the NC inventory accounting ledger' },
  { key: 'ledger', label: 'Ledger (GL)', icon: BookOpen,
    hint: 'Inventory accounts by material — from NC general ledger vouchers' },
] as const

type TabKey = typeof TABS[number]['key']

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

const selectCls =
  'h-9 rounded-lg border border-neutral-300 bg-white px-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500'

function IaBadge({ ia }: { ia: NcMeta['ia'] }) {
  if (ia.status === 'closed') return <Badge variant="success">IA period closed</Badge>
  if (ia.status === 'open_costed') return <Badge variant="warning">IA period open — costs calculated</Badge>
  return (
    <Badge variant="danger">
      IA period open — {ia.uncosted_rows.toLocaleString('en-US')} outbound rows not yet costed; IA amounts incomplete
    </Badge>
  )
}

function MetaStrip({ date }: { date: string }) {
  const q = useQuery({
    queryKey: ['nc', 'meta', date],
    queryFn: () => ncApi.meta(date),
    // Keeps "synced 6 min ago" honest on a screen left open.
    refetchInterval: 60_000,
  })
  if (q.isLoading) return <p className="text-xs text-neutral-400">Loading NC status…</p>
  if (q.isError) return <ErrorBox error={q.error} fallback="Could not load NC status." />
  const m = q.data!
  const age = relativeAge(m.sync.last_success_at)
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-neutral-500">
        <span>
          As of {m.date} (Toronto) · NC clock cutoff {m.nc_cutoff} ({m.nc_time_zone})
        </span>
        <IaBadge ia={m.ia} />
        <span title={m.sync.last_success_at ? formatDateTimeToronto(m.sync.last_success_at) : undefined}>
          {age ? `NC data synced ${age}` : 'NC data never synced'}
        </span>
      </div>
      {m.sync.tieout_ok === false && (
        <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
          <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            NC data does not tie out to NC ({m.sync.tieout_issue_count.toLocaleString('en-US')} issues) — see{' '}
            <Link to="/settings" className="font-medium underline">Settings</Link>
          </span>
        </p>
      )}
    </div>
  )
}

export default function NcInventoryPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const today = todayToronto()

  const rawDate = searchParams.get('date')
  const date = rawDate && DATE_ONLY.test(rawDate) && rawDate <= today ? rawDate : today
  const rawTab = searchParams.get('tab')
  const tab: TabKey = TABS.some((t) => t.key === rawTab) ? (rawTab as TabKey) : 'stock'
  const rawClass = searchParams.get('class') ?? ''
  const classCode = MATERIAL_CLASSES.some((c) => c.code === rawClass) ? rawClass : ''

  const [searchInput, setSearchInput] = useState('')
  const search = useDebounced(searchInput.trim())

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    // replace, not push: filter changes should not fill the back stack.
    setSearchParams(next, { replace: true })
  }

  const filters = { date, classCode, search }

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-neutral-900">NC Inventory</h1>
          <p className="text-xs text-neutral-500">{TABS.find((t) => t.key === tab)?.hint}</p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-neutral-600">
            Date
            <input
              type="date"
              value={date}
              max={today}
              onChange={(e) => {
                const v = e.target.value
                // Keep the URL clean on "today" so a bookmark keeps meaning today.
                if (v && DATE_ONLY.test(v) && v <= today) setParam('date', v === today ? '' : v)
              }}
              className={selectCls}
            />
          </label>
          <label className="flex items-center gap-2 text-xs text-neutral-600">
            Material class
            <select value={classCode} onChange={(e) => setParam('class', e.target.value)} className={selectCls}>
              <option value="">All</option>
              {MATERIAL_CLASSES.map((c) => (
                <option key={c.code} value={c.code}>{c.code} {c.label}</option>
              ))}
            </select>
          </label>
          <div className="flex h-9 w-64 items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3">
            <Search aria-hidden className="h-4 w-4 shrink-0 text-neutral-400" />
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder={tab === 'ledger' ? 'Material, name or account…' : tab === 'stock'
                ? 'Material, name or batch…' : 'Material or name…'}
              aria-label="Search"
              maxLength={100}
              className="w-full text-sm focus:outline-none"
            />
            {searchInput && (
              <button type="button" onClick={() => setSearchInput('')} aria-label="Clear search"
                className="shrink-0 text-neutral-400 hover:text-neutral-700">
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>
      </header>

      <MetaStrip date={date} />

      <div role="tablist" aria-label="NC ledgers" className="flex gap-1 border-b border-neutral-200">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setParam('tab', key === 'stock' ? '' : key)}
            className={cn(
              'flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors',
              tab === key
                ? 'border-primary-600 text-primary-700'
                : 'border-transparent text-neutral-500 hover:border-neutral-300 hover:text-neutral-700',
            )}
          >
            <Icon aria-hidden className="h-4 w-4" />
            {label}
          </button>
        ))}
      </div>

      {tab === 'stock' && <StockTab {...filters} />}
      {tab === 'cost' && <CostTab {...filters} />}
      {tab === 'ledger' && (
        <LedgerTab
          {...filters}
          includeUnposted={searchParams.get('unposted') !== '0'}
          onIncludeUnposted={(v) => setParam('unposted', v ? '' : '0')}
        />
      )}
    </div>
  )
}
