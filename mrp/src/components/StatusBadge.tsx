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
export type BomTypeStatus = 'milling' | 'drymix' | 'packaging' | 'raw' | 'packaging-material'

const LABEL: Record<ForecastVersionStatus | BomTypeStatus, string> = {
  draft: 'Draft',
  confirmed: 'Confirmed',
  superseded: 'Superseded',
  milling: 'Milling',
  drymix: 'Dry-mix',
  packaging: 'Packaging',
  raw: 'Raw',
  'packaging-material': 'Packaging Material',
}

const STYLE: Record<ForecastVersionStatus | BomTypeStatus, string> = {
  draft: 'bg-amber-50 text-amber-700 ring-amber-200',
  confirmed: 'bg-success-50 text-success-700 ring-emerald-200',
  superseded: 'bg-neutral-100 text-neutral-500 ring-neutral-200',
  milling: 'bg-violet-50 text-violet-700 ring-violet-200',
  drymix: 'bg-sky-50 text-sky-700 ring-sky-200',
  packaging: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
  raw: 'bg-teal-50 text-teal-700 ring-teal-200',
  'packaging-material': 'bg-orange-50 text-orange-700 ring-orange-200',
}

export function StatusBadge({ status }: { status: string }) {
  const key = status as ForecastVersionStatus | BomTypeStatus
  const known = key in LABEL
  return (
    <span className={cn(
      'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap',
      known ? STYLE[key] : 'bg-neutral-100 text-neutral-500 ring-neutral-200',
    )}>
      {known ? LABEL[key] : status}
    </span>
  )
}
