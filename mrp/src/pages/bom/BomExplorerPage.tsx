// BOM Explorer — design spec §6.6 page 6, task 12. Multi-level BOM
// explosion viewer (mdm-api's GET /boms/explode, the exact same engine
// Phase 1C's MRP requirements calc will reuse — see bomApi.ts/bom_explode.py)
// plus a where-used reverse-lookup mode and an NC Sync control. BOM data
// itself is never editable here — NC is the sole authority; this page only
// displays, explodes, exports, and (permission-gated) triggers a refresh.
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronsDown, ChevronsUp, FileDown, Network, Search, Loader2 } from 'lucide-react'
import { Button, Input, FormField } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ToastStack } from '@/components/Toast'
import { useToasts } from '@/hooks/useToasts'
import { usePermissions } from '@/hooks/usePermissions'
import { MaterialPicker } from '@/pages/consignment/MaterialPicker'
import type { MaterialOption } from '@/lib/materials'
import { bomApi, type ExplodeNode, type WhereUsedResult } from './bomApi'
import { BomTreeRow } from './BomTreeNode'
import { collectFlags, type FlaggedNode } from './bomFlags'
import { SyncSection } from './SyncSection'
import { exportExplodeTree, exportWhereUsed } from './exportBom'

type Mode = 'explode' | 'where-used'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function todayIso(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function formatQty(raw: string): string {
  const n = Number(raw)
  if (!Number.isFinite(n)) return raw
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 }).format(n)
}

function allPaths(node: ExplodeNode, path = '0'): string[] {
  const out = [path]
  node.children.forEach((c, i) => out.push(...allPaths(c, `${path}.${i}`)))
  return out
}

const FLAG_TEXT: Record<FlaggedNode['reason'], string> = {
  missing_bom: 'BOM missing in NC (e.g. a rework variant) — cannot explode further',
  cycle_detected: 'circular reference detected — expansion stopped',
  node_limit_reached: 'node limit reached — branch truncated',
}

export default function BomExplorerPage() {
  const toasts = useToasts()
  const permsQuery = usePermissions()
  const canSync = !!(permsQuery.data?.permissions['mdm.bom.write'] || permsQuery.data?.permissions['data_maintenance'])

  const [mode, setMode] = useState<Mode>('explode')
  const [asOfDate, setAsOfDate] = useState(todayIso())

  // ── Explode mode ─────────────────────────────────────────────────────────
  const [productCode, setProductCode] = useState('')
  const [productLabel, setProductLabel] = useState('')
  // Accumulated quantities are shown per this many finished units (default
  // 1000 — planners think in per-batch/per-tonne terms, not per single unit).
  // Editable; every accumulated value rescales live. Clamped to >= 1.
  const [basis, setBasis] = useState(1000)
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set(['0']))
  const [syncedRoot, setSyncedRoot] = useState<ExplodeNode | null>(null)

  const explodeQuery = useQuery({
    queryKey: ['bom-explode', productCode, asOfDate],
    queryFn: () => bomApi.explode(productCode, asOfDate),
    enabled: mode === 'explode' && !!productCode,
  })

  // Default expansion (root + its direct children) reset whenever a fresh
  // tree lands (new product or date) — render-phase state sync against the
  // last-seen data reference, same pattern ForecastPage.tsx uses for its
  // liveCells/baseline sync (documented there: avoids the cascading-render
  // risk the stricter react-hooks lint rule flags for setState-in-effect).
  if (explodeQuery.data && explodeQuery.data !== syncedRoot) {
    setSyncedRoot(explodeQuery.data)
    const defaults = new Set<string>(['0'])
    explodeQuery.data.children.forEach((_, i) => defaults.add(`0.${i}`))
    setExpandedPaths(defaults)
  }

  function toggleExpand(path: string) {
    setExpandedPaths((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  function handleExpandAll() {
    if (explodeQuery.data) setExpandedPaths(new Set(allPaths(explodeQuery.data)))
  }
  function handleCollapseAll() {
    setExpandedPaths(new Set(['0']))
  }

  const flags = useMemo(
    () => (explodeQuery.data ? collectFlags(explodeQuery.data) : []),
    [explodeQuery.data],
  )

  // ── Where-used mode ──────────────────────────────────────────────────────
  const [componentCode, setComponentCode] = useState('')
  const [componentLabel, setComponentLabel] = useState('')
  const [queriedComponent, setQueriedComponent] = useState<string | null>(null)

  const whereUsedQuery = useQuery({
    queryKey: ['bom-where-used', queriedComponent, asOfDate],
    queryFn: () => bomApi.whereUsed(queriedComponent as string, asOfDate),
    enabled: mode === 'where-used' && !!queriedComponent,
  })

  function handleFindWhereUsed() {
    if (componentCode) setQueriedComponent(componentCode)
  }

  // ── Export ───────────────────────────────────────────────────────────────
  // async: exportExplodeTree/exportWhereUsed dynamically import('xlsx') on
  // first use (M9, final-phase review) rather than paying its ~1.3MB in the
  // main bundle for every page load.
  async function handleExport() {
    if (mode === 'explode' && explodeQuery.data) {
      await exportExplodeTree(explodeQuery.data, asOfDate, basis)
      toasts.success('Export downloaded.')
    } else if (mode === 'where-used' && queriedComponent && whereUsedQuery.data) {
      await exportWhereUsed(queriedComponent, asOfDate, whereUsedQuery.data)
      toasts.success('Export downloaded.')
    }
  }
  const exportDisabled = mode === 'explode' ? !explodeQuery.data : !whereUsedQuery.data

  return (
    <div className="flex flex-col gap-4">
      {/* Header — title + always-visible sync freshness / Sync button */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="flex items-center gap-2 text-lg font-semibold text-neutral-900">
          <Network className="h-5 w-5 text-primary-600" /> BOM Explorer
        </h1>
        <SyncSection canSync={canSync} toasts={toasts} />
      </div>

      {/* Mode toggle */}
      <div className="flex gap-1 rounded-lg bg-neutral-100 p-1 w-fit">
        <button
          type="button"
          onClick={() => setMode('explode')}
          className={cn(
            'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
            mode === 'explode' ? 'bg-white text-primary-700 shadow-sm' : 'text-neutral-500 hover:text-neutral-700',
          )}
        >
          Explode
        </button>
        <button
          type="button"
          onClick={() => setMode('where-used')}
          className={cn(
            'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
            mode === 'where-used' ? 'bg-white text-primary-700 shadow-sm' : 'text-neutral-500 hover:text-neutral-700',
          )}
        >
          Where-used
        </button>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-end justify-between gap-3 rounded-lg border border-neutral-200 bg-white p-3">
        <div className="flex flex-wrap items-end gap-3">
          {mode === 'explode' ? (
            <FormField label="Product" htmlFor="bom-product">
              <div className="w-64">
                <MaterialPicker
                  value={productLabel}
                  onSelect={(m: MaterialOption) => { setProductCode(m.code); setProductLabel(m.name ? `${m.code} — ${m.name}` : m.code) }}
                  onClear={() => { setProductCode(''); setProductLabel('') }}
                  placeholder="Search products…"
                />
              </div>
            </FormField>
          ) : (
            <FormField label="Component" htmlFor="bom-component">
              <div className="w-64">
                <MaterialPicker
                  value={componentLabel}
                  onSelect={(m: MaterialOption) => { setComponentCode(m.code); setComponentLabel(m.name ? `${m.code} — ${m.name}` : m.code) }}
                  onClear={() => { setComponentCode(''); setComponentLabel(''); setQueriedComponent(null) }}
                  placeholder="Search components…"
                  finishedGoodsOnly={false}
                />
              </div>
            </FormField>
          )}

          <FormField label="As of" htmlFor="bom-as-of">
            <Input id="bom-as-of" type="date" value={asOfDate} onChange={(e) => setAsOfDate(e.target.value)} className="w-40" />
          </FormField>

          {mode === 'explode' && (
            <FormField label="Accum. per (units)" htmlFor="bom-basis">
              <Input
                id="bom-basis"
                type="number"
                min="1"
                step="1"
                inputMode="numeric"
                value={basis}
                onChange={(e) => {
                  const n = Math.floor(Number(e.target.value))
                  setBasis(Number.isFinite(n) && n >= 1 ? n : 1)
                }}
                className="w-28"
              />
            </FormField>
          )}

          {mode === 'where-used' && (
            <Button type="button" size="sm" onClick={handleFindWhereUsed} disabled={!componentCode}>
              <Search className="h-3.5 w-3.5" /> Find where used
            </Button>
          )}
        </div>

        <div className="flex items-center gap-2">
          {mode === 'explode' && (
            <>
              <Button type="button" variant="secondary" size="sm" onClick={handleExpandAll} disabled={!explodeQuery.data}>
                <ChevronsDown className="h-3.5 w-3.5" /> Expand all
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={handleCollapseAll} disabled={!explodeQuery.data}>
                <ChevronsUp className="h-3.5 w-3.5" /> Collapse all
              </Button>
            </>
          )}
          <Button type="button" variant="secondary" size="sm" onClick={handleExport} disabled={exportDisabled}>
            <FileDown className="h-3.5 w-3.5" /> Export Excel
          </Button>
        </div>
      </div>

      {/* ── Explode mode body ──────────────────────────────────────────── */}
      {mode === 'explode' && (
        <>
          {!productCode && (
            <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-neutral-300 py-16 text-center">
              <p className="text-sm text-neutral-500">Select a product to explode its BOM.</p>
            </div>
          )}

          {explodeQuery.isError && (
            <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
              {errMsg(explodeQuery.error, 'Could not load the BOM explosion.')}
            </p>
          )}

          {explodeQuery.isLoading && productCode && (
            <p role="status" className="flex items-center justify-center gap-2 py-12 text-sm text-neutral-400">
              <Loader2 className="h-4 w-4 animate-spin" /> Exploding BOM…
            </p>
          )}

          {explodeQuery.data && (
            <>
              <div className="flex items-center gap-3 rounded-t-lg border border-b-0 border-neutral-200 bg-neutral-50 px-2 py-2 text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
                <div className="min-w-[280px] flex-1">Component</div>
                <div className="w-28">Type</div>
                <div className="w-40">Version</div>
                <div className="w-28 text-right">Qty / unit</div>
                <div className="w-32 text-right">Accum. per {basis} {explodeQuery.data.material_code}</div>
              </div>
              <div className="overflow-x-auto rounded-b-lg border border-neutral-200">
                <div className="min-w-[900px]">
                  <BomTreeRow
                    node={explodeQuery.data}
                    path="0"
                    ancestorContinues={[]}
                    isLast={true}
                    expandedPaths={expandedPaths}
                    onToggle={toggleExpand}
                    topLabel={explodeQuery.data.material_code}
                    basis={basis}
                  />
                </div>
              </div>

              {flags.length > 0 && (
                <div className="max-h-32 overflow-y-auto rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-800">
                  {flags.map((f, i) => (
                    <p key={i}>! <span className="font-mono">{f.material_code}</span>: {FLAG_TEXT[f.reason]}</p>
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}

      {/* ── Where-used mode body ───────────────────────────────────────── */}
      {mode === 'where-used' && (
        <>
          {!queriedComponent && (
            <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-neutral-300 py-16 text-center">
              <p className="text-sm text-neutral-500">Enter a component to see which finished goods use it.</p>
            </div>
          )}

          {whereUsedQuery.isError && (
            <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
              {errMsg(whereUsedQuery.error, 'Could not load where-used results.')}
            </p>
          )}

          {whereUsedQuery.isLoading && (
            <p role="status" className="flex items-center justify-center gap-2 py-12 text-sm text-neutral-400">
              <Loader2 className="h-4 w-4 animate-spin" /> Searching…
            </p>
          )}

          {whereUsedQuery.data && (
            <WhereUsedResults results={whereUsedQuery.data} />
          )}
        </>
      )}

      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
    </div>
  )
}

function WhereUsedResults({ results }: { results: WhereUsedResult[] }) {
  if (results.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-neutral-300 py-12 text-center">
        <p className="text-sm text-neutral-500">No usage found for this component as of the selected date.</p>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      {results.map((r, i) => (
        <div key={i} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm">
          <div className="flex flex-wrap items-center gap-1 font-mono text-xs text-neutral-700">
            {r.path.map((code, j) => (
              <span key={j} className="flex items-center gap-1">
                {j > 0 && <span className="text-neutral-300">→</span>}
                <span className={cn(j === r.path.length - 1 && 'font-semibold text-primary-700')}>{code}</span>
              </span>
            ))}
          </div>
          <div className="flex items-center gap-3 text-xs text-neutral-500">
            <span>{r.levels} level{r.levels === 1 ? '' : 's'} up</span>
            <span className="font-semibold text-neutral-900">{formatQty(r.qty_accumulated)} per 1 unit of {r.top_product}</span>
            {r.cycle_detected && (
              <span className="rounded-full bg-warning-100 px-2 py-0.5 font-medium text-warning-800">cycle detected</span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
