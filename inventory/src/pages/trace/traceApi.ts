// inventory-api: Trace (inventory-api/app/api/v1/trace.py, logic in
// app/services/trace.py; design §7.4). Gated inventory.view; quantities only.
//
// Numbers are Decimal strings (format with lib/number.ts). Times are NAIVE
// Toronto wall-clock strings ("2025-03-01 14:05:00", or "2025-03-01" for an
// NC line that only has a date) — shown as text, never passed through
// new Date(), which would read them in the browser's own zone.
import { api } from '@/lib/api'
import { formatQty, isNegative, isZero } from '@/lib/number'
import { qs } from '@/pages/nc/ncApi'

export type TraceKind = 'pair' | 'nc_only' | 'wms_only'

interface TraceLineBase {
  id: string
  /** "YYYY-MM-DD HH:MM:SS" Toronto, or "YYYY-MM-DD" when date_only. */
  time: string | null
  date_only: boolean
  category: string
  category_label: string
  hidden_by_default: boolean
  qty: string
  /** Running balance of THIS side after this line. */
  balance: string
  doc: string | null
  pair: number | null
  tier: 1 | 2 | null
}

export interface TraceNcLine extends TraceLineBase {
  side: 'nc'
  bill: string | null
  source: string | null
  first: string | null
  /** The WMS number NC carries (vheaduserdef13). */
  wms_ref: string | null
  trantype: string | null
  trantype_name: string | null
  warehouse: string | null
  other_warehouse: string | null
  batch: string | null
  unsigned: boolean
  nc_time: string | null
}

export interface TraceWmsLine extends TraceLineBase {
  side: 'wms'
  doc_type: string | null
  txn_type: string | null
  refs: string[]
  lot: string | null
  qc_batch: string | null
  supplier_batch: string | null
  reason: string | null
  by: string | null
}

export type TraceLine = TraceNcLine | TraceWmsLine

export interface TraceRow {
  kind: TraceKind
  tier: 1 | 2 | null
  /** Tier-1 document key the group was joined on. */
  key: string | null
  time: string | null
  category: string
  nc: TraceNcLine[]
  wms: TraceWmsLine[]
  nc_qty: string
  wms_qty: string
  /** nc_qty − wms_qty */
  diff: string
}

export interface TraceSummaryLine {
  category: string
  label: string
  hidden_by_default: boolean
  nc: string
  wms: string
  diff: string
}

export interface TraceDivergence {
  side: 'nc' | 'wms'
  id: string
  pair: number | null
  time: string | null
  category: string
  diverged_from_start: boolean
  summary: string
  /** Largest unmatched DOCUMENT since the balances last agreed. */
  main: {
    side: 'nc' | 'wms'
    doc: string | null
    qty: string
    lines: number
    time: string | null
    category: string
    summary: string
  } | null
}

export interface TraceResponse {
  material: { code: string; name: string | null; uom: string | null; in_nc: boolean }
  batch: string | null
  batch_group: string | null
  batch_scope: { nc_batches: string[] | null; wms_lots: string[] | null }
  no_batch_since: string | null
  date: string
  /** WMS go-live — the window's first day. */
  start: string
  nc_time_zone: string
  scope_note: string
  opening: { nc: string; wms: string }
  closing: { nc: string; wms: string; diff: string }
  counts: {
    nc_lines: number
    wms_lines: number
    paired_by_document: number
    paired_by_quantity: number
    nc_only: number
    wms_only: number
  }
  divergence: TraceDivergence | null
  summary: TraceSummaryLine[]
  rows: TraceRow[]
}

/** Material picker option (GET /trace/materials). Quantities are today's. */
export interface TraceMaterialOption {
  code: string
  name: string | null
  uom: string | null
  in_nc: boolean
  nc_qty: string | null
  wms_qty: string | null
  /** Date-only string: NC stopped recording this material by batch. */
  no_batch_since: string | null
}

/** Batch picker option (GET /trace/batches): one batch GROUP. `batch` is the
 *  value to trace by; `label` is what to show. */
export interface TraceBatchOption {
  batch: string
  label: string
  nc_batches: string[]
  supplier_batches: string[]
  wms_lots: number
  match: 'both' | 'wms_only' | 'nc_only'
  nc_qty: string
  wms_qty: string
  last_movement: string | null
}

export const traceApi = {
  get: (material: string, batch: string | null, date: string) =>
    api.get<TraceResponse>(`/trace${qs({ material, batch, date })}`),
  materials: (q: string, limit = 50) =>
    api.get<{ items: TraceMaterialOption[] }>(`/trace/materials${qs({ q: q.trim(), limit })}`),
  batches: (material: string, q: string) =>
    api.get<{ material: string; total: number; items: TraceBatchOption[] }>(
      `/trace/batches${qs({ material, q: q.trim() })}`),
}

/** "2025-03-01 14:05:00.123" → "2025-03-01 14:05"; a date-only string stays
 *  as it is. String slicing only. */
export function formatTraceTime(t: string | null | undefined, dateOnly = false): string {
  if (!t) return '—'
  if (dateOnly || t.length <= 10) return t.slice(0, 10)
  return t.slice(0, 16)
}

export function matchLabel(r: Pick<TraceRow, 'kind' | 'tier' | 'key'>): string {
  if (r.kind === 'nc_only') return 'NC only'
  if (r.kind === 'wms_only') return 'WMS only'
  if (r.tier === 1) return `doc ${r.key ?? ''}`.trim()
  return 'qty+time'
}

/** "+12.5" / "-3" / "0" on the decimal digits. */
export function signedQty(v: string | null | undefined): string {
  if (v == null || v === '') return '—'
  if (isZero(v)) return '0'
  return isNegative(v) ? formatQty(v) : `+${formatQty(v)}`
}

/** A row made only of internal moves / state changes (hidden by default). */
export function rowHidden(r: TraceRow): boolean {
  const lines: TraceLine[] = [...r.nc, ...r.wms]
  return lines.length > 0 && lines.every((l) => l.hidden_by_default)
}
