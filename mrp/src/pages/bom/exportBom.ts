// Client-side Excel export for BOM Explorer (design spec §6.6: "导出 Excel
// （含缩进层级与累计用量）"). mdm-api has no export endpoint for the
// explosion tree or where-used results (only explode/where-used/sync-state/
// sync — see bomApi.ts), unlike Sales Forecast's export which downloads a
// server-built .xlsx (forecastApi.exportGrid). Built with the `xlsx`
// package (SheetJS community edition, MIT) — the one third-party library
// this page adds; no other frontend in this repo generates Excel files
// client-side (all others proxy a backend export endpoint), but no backend
// endpoint exists here to proxy, and a bare CSV wouldn't satisfy "indent
// level" as a real column planners can group/filter by in Excel.
//
// `xlsx` (~1.3MB) is loaded via a dynamic `import()` inside each export
// function below, NOT a static top-level import (M9, final-phase review):
// this file is statically reachable from routes.tsx (BomExplorerPage ->
// exportBom.ts), so a top-level `import * as XLSX from 'xlsx'` put the
// whole library in the main chunk for every user on every page, not just
// the ones who click "Export" on the BOM Explorer.
import { displayBomType } from './bomType'
import type { ExplodeNode, WhereUsedResult } from './bomApi'

function n(raw: string | null): number | null {
  if (raw === null) return null
  const v = Number(raw)
  return Number.isFinite(v) ? v : null
}

interface ExplodeRow {
  Level: number
  'Material Code': string
  Name: string
  'Indented Name': string
  'BOM Type': string
  Version: string
  'Approved Candidates': number
  'Qty Per (this level)': number | null
  Unit: string
  // NC's own batch-scale numbers, so an exported sheet can be reconciled
  // against the NC BOM screen the same way the on-screen tree can (see
  // bomQty.ts / mdm-api bom_explode.py's "NC batch-scale fields").
  'NC Batch Qty': number | null
  'NC Parent Batch Size': number | null
  'NC Own Batch Size': number | null
  'Accumulated Qty': number | null
  'Missing BOM': string
  'Cycle Detected': string
}

// Columns holding a BOM ratio, which spans a huge dynamic range (a 3000 kg
// batch using 0.012 kg of an ingredient normalizes to 0.000004). Excel's
// General format renders that as `4E-06`; this fixed format shows it as a
// plain decimal, and trims trailing zeros on ordinary values, so the sheet
// reads the same way the screen does. Applied by cell below — SheetJS's
// json_to_sheet writes values only, never a number format.
const QTY_COLUMNS: ReadonlyArray<keyof ExplodeRow> = [
  'Qty Per (this level)', 'NC Batch Qty', 'NC Parent Batch Size', 'NC Own Batch Size', 'Accumulated Qty',
]
const QTY_NUMBER_FORMAT = '0.##########'

function scaleAccum(raw: string | null, basis: number): number | null {
  const v = n(raw)
  return v === null ? null : v * basis
}

function flatten(node: ExplodeNode, rows: ExplodeRow[], basis: number): void {
  rows.push({
    Level: node.level,
    'Material Code': node.material_code,
    Name: node.name ?? '',
    'Indented Name': `${'  '.repeat(node.level)}${node.name ?? node.material_code}`,
    // Same display classification the on-screen badge uses (name-based
    // pasteurization + CS->powdering), so the export doesn't disagree with
    // what the planner saw.
    'BOM Type': displayBomType(node.material_code, node.bom_type, node.name) ?? '',
    Version: node.version ?? '',
    'Approved Candidates': node.version_candidates_count,
    'Qty Per (this level)': node.level === 0 ? null : n(node.qty_per),
    Unit: node.uom ?? '',
    'NC Batch Qty': n(node.qty_per_batch),
    'NC Parent Batch Size': n(node.parent_batch_output_qty),
    'NC Own Batch Size': n(node.batch_output_qty),
    'Accumulated Qty': scaleAccum(node.qty_accumulated, basis),
    'Missing BOM': node.missing_bom ? 'Yes' : '',
    'Cycle Detected': node.cycle_detected ? 'Yes' : '',
  })
  for (const child of node.children) flatten(child, rows, basis)
}

/** Tag every numeric quantity cell with `QTY_NUMBER_FORMAT`, so Excel shows
 *  a legitimately tiny ratio as `0.000004` instead of `4E-06`. Header row is
 *  row 0, so data starts at row 1; a null/absent cell is simply skipped. */
function applyQtyNumberFormat(
  XLSX: typeof import('xlsx'), sheet: import('xlsx').WorkSheet,
  columns: ReadonlyArray<string>, headers: ReadonlyArray<string>, rowCount: number,
): void {
  for (const name of columns) {
    const col = headers.indexOf(name)
    if (col < 0) continue
    for (let row = 1; row <= rowCount; row++) {
      const cell = sheet[XLSX.utils.encode_cell({ c: col, r: row })]
      if (cell && cell.t === 'n') cell.z = QTY_NUMBER_FORMAT
    }
  }
}

export async function exportExplodeTree(root: ExplodeNode, asOfDate: string, basis: number): Promise<void> {
  const XLSX = await import('xlsx')
  const rows: ExplodeRow[] = []
  flatten(root, rows, basis)
  const sheet = XLSX.utils.json_to_sheet(rows)
  if (rows.length > 0) {
    applyQtyNumberFormat(XLSX, sheet, QTY_COLUMNS as string[], Object.keys(rows[0]), rows.length)
  }
  const book = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(book, sheet, 'BOM Explosion')
  XLSX.writeFile(book, `bom-explode-${root.material_code}-${asOfDate}.xlsx`)
}

interface WhereUsedRow {
  'Top Product': string
  Path: string
  Levels: number
  'Accumulated Qty (per 1 unit top)': number | null
  'Cycle Detected': string
}

export async function exportWhereUsed(
  component: string, asOfDate: string, results: WhereUsedResult[],
): Promise<void> {
  const XLSX = await import('xlsx')
  const rows: WhereUsedRow[] = results.map((r) => ({
    'Top Product': r.top_product,
    Path: r.path.join(' → '),
    Levels: r.levels,
    'Accumulated Qty (per 1 unit top)': n(r.qty_accumulated),
    'Cycle Detected': r.cycle_detected ? 'Yes' : '',
  }))
  const sheet = XLSX.utils.json_to_sheet(rows)
  if (rows.length > 0) {
    applyQtyNumberFormat(
      XLSX, sheet, ['Accumulated Qty (per 1 unit top)'], Object.keys(rows[0]), rows.length,
    )
  }
  const book = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(book, sheet, 'Where Used')
  XLSX.writeFile(book, `bom-where-used-${component}-${asOfDate}.xlsx`)
}
