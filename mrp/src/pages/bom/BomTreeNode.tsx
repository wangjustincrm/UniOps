// One row (recursive) in the BOM Explorer's explosion tree — design spec
// §6.6 page 6. Indentation + connector lines (Unicode box-drawing, same
// idea as the ASCII mockup's `+-`/`|`) computed from `ancestorContinues`:
// for each ancestor level, whether that ancestor still has more siblings
// below it (so its vertical bar must continue past this row) or not (blank
// instead). This is the standard "is this the last child at each level"
// recursion every terminal file-tree view uses.
import { useState } from 'react'
import { ChevronRight, ChevronDown, AlertTriangle, RefreshCw, Ban } from 'lucide-react'
import { StatusBadge } from '@/components/StatusBadge'
import { cn } from '@/lib/utils'
import { displayBomType } from './bomType'
import { formatQty, formatNcBatchQty } from './bomQty'
import type { ExplodeNode } from './bomApi'

export function BomTreeRow({
  node, path, ancestorContinues, isLast, expandedPaths, onToggle, topLabel, basis,
}: {
  node: ExplodeNode
  path: string
  /** For each ancestor level (root's children = index 0), whether that
   *  ancestor has a later sibling still to render (draws `│`) or not
   *  (draws blank space instead). */
  ancestorContinues: boolean[]
  isLast: boolean
  expandedPaths: Set<string>
  onToggle: (path: string) => void
  /** Root product code, for the accumulated-quantity column header context. */
  topLabel: string
  /** Accumulated quantities are reported by the API per 1 unit of the top
   *  product; this multiplies them for display so the planner can read
   *  "per N finished units" (default 1000, editable in the toolbar). Does
   *  NOT scale `qty_per` (that's per 1 unit of the immediate parent). */
  basis: number
}) {
  const [versionOpen, setVersionOpen] = useState(false)
  const hasChildren = node.children.length > 0
  const expanded = expandedPaths.has(path)
  const bomType = displayBomType(node.material_code, node.bom_type, node.name)
  const isRoot = path === '0'
  const accumScaled = node.qty_accumulated === null ? null : String(Number(node.qty_accumulated) * basis)
  // NC's own numbers for this line, so the row can be checked against the
  // NC BOM screen without re-deriving the normalized ratio (see bomQty.ts).
  const ncBatch = formatNcBatchQty(node.qty_per_batch, node.parent_batch_output_qty)

  const prefix = ancestorContinues.map((cont, i) => (
    <span key={i} className="inline-block w-4 shrink-0 text-neutral-300">{cont ? '│' : ' '}</span>
  ))

  return (
    <div>
      <div
        className={cn(
          'flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-neutral-100 px-2 py-1.5 text-sm last:border-0',
          node.missing_bom && 'bg-danger-50/50',
          node.cycle_detected && 'bg-warning-50/50',
          !node.missing_bom && !node.cycle_detected && 'odd:bg-white even:bg-neutral-50/40',
        )}
      >
        {/* Tree column: connector prefix + expand toggle + code/name */}
        <div className="flex min-w-[280px] flex-1 items-center font-mono text-xs">
          {!isRoot && prefix}
          {!isRoot && (
            <span className="inline-block w-4 shrink-0 text-neutral-300">{isLast ? '└' : '├'}</span>
          )}
          <button
            type="button"
            onClick={() => hasChildren && onToggle(path)}
            disabled={!hasChildren}
            aria-expanded={hasChildren ? expanded : undefined}
            aria-label={hasChildren ? (expanded ? `Collapse ${node.material_code}` : `Expand ${node.material_code}`) : undefined}
            className={cn(
              'flex h-5 w-5 shrink-0 items-center justify-center rounded',
              hasChildren ? 'text-neutral-500 hover:bg-neutral-200' : 'text-transparent',
            )}
          >
            {hasChildren && (expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />)}
          </button>
          <span className="shrink-0 font-semibold text-primary-700">{node.material_code}</span>
          <span className="ml-1.5 truncate font-sans text-neutral-700">{node.name ?? '—'}</span>
        </div>

        {/* bom_type badge */}
        <div className="w-28 shrink-0">
          {bomType ? <StatusBadge status={bomType} /> : <span className="text-xs text-neutral-300">—</span>}
        </div>

        {/* Version — click to expand what selection detail is available */}
        <div className="w-40 shrink-0 text-xs">
          {node.version ? (
            <button
              type="button"
              onClick={() => setVersionOpen((v) => !v)}
              className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900"
              aria-expanded={versionOpen}
            >
              {versionOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              v{node.version} ({node.version_candidates_count} approved)
            </button>
          ) : (
            <span className="text-neutral-300">—</span>
          )}
        </div>

        {/* This level's qty_per */}
        <div className="w-28 shrink-0 text-right text-xs tabular-nums text-neutral-700">
          {isRoot ? '—' : `${formatQty(node.qty_per)} ${node.uom ?? ''}`}
        </div>

        {/* NC's own batch-scale numbers for this line — `NITEMNUM / batch
            size`, exactly as they read on the NC BOM screen. */}
        <div
          className="w-44 shrink-0 text-right text-xs tabular-nums text-neutral-500"
          title={
            node.parent_batch_output_qty
              ? `As shown on the NC BOM screen: ${formatQty(node.qty_per_batch)} ${node.uom ?? ''} per one ${formatQty(node.parent_batch_output_qty)} batch of the parent BOM`
              : undefined
          }
        >
          {ncBatch ?? <span className="text-neutral-300">—</span>}
        </div>

        {/* Accumulated qty — the number planners actually need, scaled to the
            chosen basis (per N units of the top product) */}
        <div className="w-32 shrink-0 text-right text-xs font-semibold tabular-nums text-neutral-900" title={`Per ${basis} units of ${topLabel}`}>
          {formatQty(accumScaled)} {node.uom ?? ''}
        </div>
      </div>

      {/* Version candidate detail — honest about what the API actually
          returns (a count, not a full candidate list/effective-window
          breakdown — see bomApi.ts header comment) rather than fabricating
          detail the backend doesn't provide. */}
      {versionOpen && node.version && (
        <div className="ml-8 border-b border-neutral-100 bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
          <p>Selected version <strong>v{node.version}</strong> for the query date.</p>
          {node.batch_output_qty !== null && (
            <p className="mt-0.5">
              NC batch size for this BOM: <strong>{formatQty(node.batch_output_qty)} {node.uom ?? ''}</strong> — its
              component lines are stated per this batch on the NC BOM screen, which is the denominator shown in the
              NC batch qty column of the rows below.
            </p>
          )}
          {node.version_candidates_count > 1 ? (
            <p className="mt-0.5">
              {node.version_candidates_count} approved version{node.version_candidates_count === 1 ? '' : 's'} exist for{' '}
              <span className="font-mono">{node.material_code}</span>; the highest-numbered one whose effective window
              covers the selected date was chosen. Individual candidate version numbers and effective windows are not
              yet exposed by the API.
            </p>
          ) : (
            <p className="mt-0.5">Only one approved version exists for this material.</p>
          )}
        </div>
      )}

      {/* Missing BOM / cycle / node-limit — warning styling PLUS readable
          text, never colour alone. */}
      {node.missing_bom && (
        <RowNotice icon={<AlertTriangle className="h-3.5 w-3.5" />} tone="danger">
          Expected BOM not found in NC for <span className="font-mono">{node.material_code}</span> (e.g. a rework
          variant) — this branch cannot be explored further.
        </RowNotice>
      )}
      {node.cycle_detected && (
        <RowNotice icon={<RefreshCw className="h-3.5 w-3.5" />} tone="warning">
          Circular reference: <span className="font-mono">{node.material_code}</span> already appears higher up this
          same branch — expansion stopped here to avoid an infinite loop.
        </RowNotice>
      )}
      {node.node_limit_reached && (
        <RowNotice icon={<Ban className="h-3.5 w-3.5" />} tone="warning">
          Node limit reached — this branch was truncated so the tree stays a manageable size. Quantities shown are
          still correct; children were simply never explored.
        </RowNotice>
      )}

      {hasChildren && expanded && node.children.map((child, i) => (
        <BomTreeRow
          key={`${path}.${i}`}
          node={child}
          path={`${path}.${i}`}
          ancestorContinues={isRoot ? [] : [...ancestorContinues, !isLast]}
          isLast={i === node.children.length - 1}
          expandedPaths={expandedPaths}
          onToggle={onToggle}
          topLabel={topLabel}
          basis={basis}
        />
      ))}
    </div>
  )
}

function RowNotice({ icon, tone, children }: { icon: React.ReactNode; tone: 'danger' | 'warning'; children: React.ReactNode }) {
  return (
    <p
      role="alert"
      className={cn(
        'ml-8 flex items-start gap-1.5 border-b border-neutral-100 px-3 py-1.5 text-xs',
        tone === 'danger' ? 'bg-danger-50 text-danger-700' : 'bg-warning-50 text-warning-800',
      )}
    >
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span>{children}</span>
    </p>
  )
}
