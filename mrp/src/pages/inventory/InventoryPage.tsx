// Inventory — one page, three tabs: Lots, Aging, Materials.
//
// One nav entry rather than three, because the three answer the same question
// at different resolutions ("what have we got") and a planner moves between
// them mid-thought. The material-class filter therefore lives HERE and is
// passed down: a filter that resets on every tab switch makes the tabs feel
// like unrelated pages and quietly changes what the numbers mean.
//
// The active tab is in the URL (`?tab=aging`) so a tab is linkable and the
// multi-tab shell restores the right one instead of always reopening on Lots.
import { useSearchParams } from 'react-router-dom'
import { Boxes, CalendarClock, Layers } from 'lucide-react'
import { cn } from '@/lib/utils'
import { MATERIAL_CLASSES } from './inventoryApi'
import { LotsTab } from './LotsTab'
import { AgingTab } from './AgingTab'
import { MaterialsTab } from './MaterialsTab'

const TABS = [
  { key: 'lots', label: 'Lots', icon: Layers,
    hint: 'Search individual lots by material, lot number or supplier batch' },
  { key: 'aging', label: 'Aging', icon: CalendarClock,
    hint: 'Shelf life at 180 / 60 / 30 days, and what has already expired' },
  { key: 'materials', label: 'Materials', icon: Boxes,
    hint: 'Per material: on hand, available, and what is on order' },
] as const

type TabKey = typeof TABS[number]['key']

export default function InventoryPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const raw = searchParams.get('tab')
  const tab: TabKey = TABS.some((t) => t.key === raw) ? (raw as TabKey) : 'lots'
  // Default to raw ingredients: it is the class with shelf life that anybody
  // acts on. Packaging does not expire and finished goods are somebody else's
  // screen — but both stay one click away rather than being hidden.
  const erpClass = searchParams.get('class') ?? '0102'

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    // replace, not push: switching tabs should not fill the browser's back
    // stack with states nobody wants to walk back through.
    setSearchParams(next, { replace: true })
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-neutral-900">Inventory</h1>
          <p className="text-xs text-neutral-500">
            {TABS.find((t) => t.key === tab)?.hint}
          </p>
        </div>

        <label className="flex items-center gap-2 text-xs text-neutral-600">
          Material class
          <select
            value={erpClass}
            onChange={(e) => setParam('class', e.target.value)}
            className="h-9 rounded-lg border border-neutral-300 bg-white px-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="">All classes</option>
            {MATERIAL_CLASSES.map((c) => (
              <option key={c.code} value={c.code}>{c.code} · {c.label}</option>
            ))}
          </select>
        </label>
      </header>

      {/* Raw milk is excluded server-side from everything on this page. Said
          once, here, rather than on each tab: a planner who knows the plant
          buys millions of kilos of milk and sees none of it would otherwise
          reasonably conclude the page is broken. */}
      <p className="text-[11px] text-neutral-400">
        Raw milk (class 0101) is not counted as stock or as on order — it is
        delivered straight into production and its receipts are recorded in the ERP.
      </p>

      <div role="tablist" aria-label="Inventory views" className="flex gap-1 border-b border-neutral-200">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setParam('tab', key)}
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

      {tab === 'lots' && <LotsTab erpClassCode={erpClass} />}
      {tab === 'aging' && <AgingTab erpClassCode={erpClass} />}
      {tab === 'materials' && <MaterialsTab erpClassCode={erpClass} />}
    </div>
  )
}
