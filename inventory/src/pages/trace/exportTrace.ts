// Trace → xlsx: Summary (opening, each category, closing) + Movements (one
// row per NC / WMS line, in the page's order) + Info.
//
// Match and highlight state are written as TEXT columns, never colours — the
// sheet gets filtered and forwarded, and colours do not survive either.
// Internal moves / state changes are always exported, flagged in a column,
// whatever the page's "show" toggle says.
//
// As in exportRecon.ts, this is the one place quantities go through Number():
// Excel stores floats anyway. `xlsx` is imported dynamically.
import { formatTraceTime, matchLabel, type TraceLine, type TraceResponse } from './traceApi'

function n(raw: string | null | undefined): number | null {
  if (raw == null || raw === '') return null
  const v = Number(raw)
  return Number.isFinite(v) ? v : null
}

export function lineDetail(ln: TraceLine): string {
  if (ln.side === 'nc') {
    return [
      ln.trantype_name ? `${ln.trantype_name}${ln.trantype ? ` (${ln.trantype})` : ''}` : ln.trantype,
      ln.warehouse ? `wh ${ln.warehouse}${ln.other_warehouse ? ` ↔ ${ln.other_warehouse}` : ''}` : null,
      ln.batch ? `batch ${ln.batch}` : null,
      ln.unsigned ? 'unsigned' : null,
      ln.wms_ref ? `WMS ref ${ln.wms_ref}` : null,
      ln.source && ln.source !== ln.bill ? `source ${ln.source}` : null,
      ln.first && ln.first !== ln.source && ln.first !== ln.bill ? `first ${ln.first}` : null,
    ].filter(Boolean).join('; ')
  }
  return [
    [ln.txn_type, ln.doc_type].filter(Boolean).join(' / ') || null,
    ln.lot ? `lot ${ln.lot}` : null,
    ln.qc_batch ? `QC batch ${ln.qc_batch}` : null,
    ln.supplier_batch ? `supplier batch ${ln.supplier_batch}` : null,
    ln.refs.length ? `refs ${ln.refs.join(', ')}` : null,
    ln.reason ? `reason ${ln.reason}` : null,
    ln.by ? `by ${ln.by}` : null,
  ].filter(Boolean).join('; ')
}

export async function exportTrace(t: TraceResponse): Promise<void> {
  const XLSX = await import('xlsx')

  const summary: Record<string, string | number | null>[] = [
    { Category: `Opening (before ${t.start})`, 'Hidden by default': '', NC: n(t.opening.nc), WMS: n(t.opening.wms),
      Difference: (n(t.opening.nc) ?? 0) - (n(t.opening.wms) ?? 0) },
    ...t.summary.map((s) => ({
      Category: s.label, 'Hidden by default': s.hidden_by_default ? 'Yes' : '',
      NC: n(s.nc), WMS: n(s.wms), Difference: n(s.diff),
    })),
    { Category: `Closing (${t.date})`, 'Hidden by default': '', NC: n(t.closing.nc), WMS: n(t.closing.wms),
      Difference: n(t.closing.diff) },
  ]
  const movements: Record<string, string | number | null>[] = []
  t.rows.forEach((r, i) => {
    const lines: TraceLine[] = [...r.nc, ...r.wms]
    for (const ln of lines) {
      movements.push({
        Row: i + 1,
        'Row time': formatTraceTime(r.time),
        Category: ln.category_label,
        Match: matchLabel(r),
        'Row NC qty': n(r.nc_qty),
        'Row WMS qty': n(r.wms_qty),
        'Row difference (NC−WMS)': n(r.diff),
        Side: ln.side === 'nc' ? 'NC' : 'WMS',
        Time: formatTraceTime(ln.time, ln.date_only),
        Document: ln.doc ?? '',
        Qty: n(ln.qty),
        'Side balance': n(ln.balance),
        Detail: lineDetail(ln),
        'Manual opening import': ln.category === 'manual_opening' ? 'Yes' : '',
        'Hidden by default': ln.hidden_by_default ? 'Yes' : '',
      })
    }
  })

  const div = t.divergence
  const info: (string | number)[][] = [
    ['Material', t.material.code],
    ['Name', t.material.name ?? ''],
    ['UoM', t.material.uom ?? ''],
    ['Batch', t.batch ?? '(whole material)'],
    ['Batch group', t.batch_group ?? ''],
    ['NC batches in scope', t.batch_scope.nc_batches?.join(', ') ?? ''],
    ['WMS lots in scope', t.batch_scope.wms_lots?.join(', ') ?? ''],
    ['Window', `${t.start} (WMS go-live) to ${t.date}`],
    ['Scope', t.scope_note],
    ['NC clock', t.nc_time_zone],
    ['NC stopped batches on', t.no_batch_since ?? ''],
    ['Paired by document', t.counts.paired_by_document],
    ['Paired by quantity', t.counts.paired_by_quantity],
    ['NC only', t.counts.nc_only],
    ['WMS only', t.counts.wms_only],
    ['First divergence', div ? div.summary : 'None'],
    ['Largest unmatched since then', div?.main ? div.main.summary : ''],
  ]

  const book = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(book, XLSX.utils.json_to_sheet(summary), 'Summary')
  XLSX.utils.book_append_sheet(book, XLSX.utils.json_to_sheet(movements), 'Movements')
  XLSX.utils.book_append_sheet(book, XLSX.utils.aoa_to_sheet(info), 'Info')
  const safe = (s: string) => s.replace(/[^A-Za-z0-9._-]+/g, '_')
  XLSX.writeFile(book, `inventory-trace-${safe(t.material.code)}${t.batch ? `-${safe(t.batch)}` : ''}-${t.date}.xlsx`)
}
