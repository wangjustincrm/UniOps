// StatusBadge — domain status -> visual mapping stays in the app (see
// @uniops/shell/ui/badge.tsx comment). Two status domains share this one
// component (design spec §6.6: "bom_type 徽章用统一 StatusBadge"):
//   - forecast version lifecycle (draft -> confirmed -> superseded)
//   - BOM Explorer's bom_type (milling / drymix / packaging / raw /
//     packaging-material) — the values are disjoint from forecast statuses,
//     so both domains live in one lookup table with no ambiguity.
// `raw`/`packaging-material` are never returned in `ExplodeNode.bom_type`
// itself (mdm-api only sets that field on nodes that own an approved BOM —
// see bom_explode.py; a leaf's `bom_type` is always null on the wire).
// pages/bom/bomType.ts derives them client-side from the material code
// prefix for display, mirroring mdm-api's own `_bom_type()`/
// `expected_bom_type()` prefix table (CS/CW/CF/S) plus the two additional
// leaf-only buckets (CR->raw, CP->packaging-material) that table doesn't
// need server-side (those codes never own a BOM, so the backend never
// classifies them) but the design spec's 5-value badge legend calls for.
import { cn } from '@/lib/utils'

export type ForecastVersionStatus = 'draft' | 'confirmed' | 'superseded'
// `powdering` replaces the old `milling` label for CS-prefix semi-products,
// and `past` (pasteurization) is derived for materials whose name contains
// "Pasteurized" — both are display-layer classifications applied in
// pages/bom/bomType.ts (the wire bom_type from mdm-api is still milling/
// unknown respectively). `milling` is kept so any un-remapped value still
// renders sanely.
export type BomTypeStatus =
  | 'milling' | 'powdering' | 'drymix' | 'packaging' | 'past' | 'raw' | 'packaging-material'

const LABEL: Record<ForecastVersionStatus | BomTypeStatus, string> = {
  draft: 'Draft',
  confirmed: 'Confirmed',
  superseded: 'Superseded',
  milling: 'Milling',
  powdering: 'Powdering',
  drymix: 'Dry-mix',
  packaging: 'Packaging',
  past: 'Pasteurization',
  raw: 'Raw',
  'packaging-material': 'Packaging Material',
}

const STYLE: Record<ForecastVersionStatus | BomTypeStatus, string> = {
  draft: 'bg-warning-soft text-warning-fg ring-warning-line',
  confirmed: 'bg-success-soft text-success-fg ring-success-line',
  superseded: 'bg-surface-sunken text-fg-muted ring-line',
  milling: 'bg-cat4-soft text-cat4-fg ring-cat4-line',
  powdering: 'bg-cat4-soft text-cat4-fg ring-cat4-line',
  drymix: 'bg-cat2-soft text-cat2-fg ring-cat2-line',
  packaging: 'bg-cat8-soft text-cat8-fg ring-cat8-line',
  past: 'bg-cat5-soft text-cat5-fg ring-cat5-line',
  raw: 'bg-cat1-soft text-cat1-fg ring-cat1-line',
  'packaging-material': 'bg-cat7-soft text-cat7-fg ring-cat7-line',
}

export function StatusBadge({ status }: { status: string }) {
  const key = status as ForecastVersionStatus | BomTypeStatus
  const known = key in LABEL
  return (
    <span className={cn(
      'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap',
      known ? STYLE[key] : 'bg-surface-sunken text-fg-muted ring-line',
    )}>
      {known ? LABEL[key] : status}
    </span>
  )
}
