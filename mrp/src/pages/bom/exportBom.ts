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
  'Accumulated Qty (per 1 unit top)': number | null
  'Secondary Qty': number | null
  'Secondary Unit': string
  'Missing BOM': string
  'Cycle Detected': string
}

function flatten(node: ExplodeNode, rows: ExplodeRow[]): void {
  rows.push({
    Level: node.level,
    'Material Code': node.material_code,
    Name: node.name ?? '',
    'Indented Name': `${'  '.repeat(node.level)}${node.name ?? node.material_code}`,
    'BOM Type': displayBomType(node.material_code, node.bom_type) ?? '',
    Version: node.version ?? '',
    'Approved Candidates': node.version_candidates_count,
    'Qty Per (this level)': node.level === 0 ? null : n(node.qty_per),
    Unit: node.uom ?? '',
    'Accumulated Qty (per 1 unit top)': n(node.qty_accumulated),
    'Secondary Qty': n(node.qty_per_secondary),
    'Secondary Unit': node.uom_secondary ?? '',
    'Missing BOM': node.missing_bom ? 'Yes' : '',
    'Cycle Detected': node.cycle_detected ? 'Yes' : '',
  })
  for (const child of node.children) flatten(child, rows)
}

export async function exportExplodeTree(root: ExplodeNode, asOfDate: string): Promise<void> {
  const XLSX = await import('xlsx')
  const rows: ExplodeRow[] = []
  flatten(root, rows)
  const sheet = XLSX.utils.json_to_sheet(rows)
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
  const book = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(book, sheet, 'Where Used')
  XLSX.writeFile(book, `bom-where-used-${component}-${asOfDate}.xlsx`)
}
