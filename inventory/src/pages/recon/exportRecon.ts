// Reconciliation → xlsx. Fetches every page of the current filters, then
// writes Materials (+ Batches when batches are expanded) + GL other + Info.
//
// Causes are written as a text column ("label: qty / amount; …") and status as
// a word — never as colours: the sheet gets filtered and forwarded, and
// colours do not survive either (design §7.3).
//
// This is the one place numbers go through Number(): Excel stores floats
// anyway. `xlsx` is imported dynamically so it stays out of the main chunk.
import { reconApi, STATUSES, type ReconFilters, type ReconResponse, type ReconRow } from './reconApi'

const EXPORT_PAGE_SIZE = 500

function n(raw: string | null | undefined): number | null {
  if (raw == null || raw === '') return null
  const v = Number(raw)
  return Number.isFinite(v) ? v : null
}

function statusLabel(s: ReconRow['status']): string {
  return STATUSES.find((x) => x.key === s)?.label ?? s
}

export function causesText(r: ReconRow, amountsVisible: boolean): string {
  return r.categories.map((c) => {
    const amt = amountsVisible && c.amount != null ? ` / ${c.amount}` : ''
    return `${c.label}: ${c.qty}${amt}`
  }).join('; ')
}

async function fetchAll(f: Omit<ReconFilters, 'page' | 'page_size'>): Promise<{ first: ReconResponse; rows: ReconRow[] }> {
  const first = await reconApi.get({ ...f, page: 1, page_size: EXPORT_PAGE_SIZE })
  const rows = [...first.items]
  let page = 1
  while (rows.length < first.total) {
    page += 1
    const next = await reconApi.get({ ...f, page, page_size: EXPORT_PAGE_SIZE })
    if (next.items.length === 0) break
    rows.push(...next.items)
  }
  return { first, rows }
}

export async function exportReconciliation(
  f: Omit<ReconFilters, 'page' | 'page_size'>,
  filterSummary: string,
): Promise<void> {
  const [XLSX, { first, rows }] = await Promise.all([import('xlsx'), fetchAll(f)])
  const amt = first.amounts_visible

  const materials = rows.map((r) => {
    const o: Record<string, string | number | null> = {
      Material: r.material_code,
      Name: r.material_name ?? '',
      Class: r.class_code ?? '',
      UoM: r.uom ?? '',
      'WMS UoM': r.wms_uom ?? '',
      'Units differ': r.unit_check === 'mismatch' ? 'Yes' : '',
      'In NC': r.in_nc ? 'Yes' : 'No',
      'NC stopped since': r.no_batch_since ?? '',
      WMS: n(r.wms),
      'NC stock (WMS warehouses)': n(r.ic_wms),
      'NC stock (external)': n(r.ic_external),
      'NC stock (production line-side)': n(r.ic_production),
      'NC stock (transient/other)': n(r.ic_transient),
      'NC stock total': n(r.ic_total),
      'IA qty': n(r.ia_qty),
      'GL qty': n(r.gl_qty),
      'IC−WMS': n(r.diff.ic_wms),
      'IC−WMS unexplained': n(r.unexplained.ic_wms),
      'IA−IC': n(r.diff.ia_ic),
      'IA−IC unexplained': n(r.unexplained.ia_ic),
      'GL−IA qty': n(r.diff.gl_ia_qty),
      'GL−IA qty unexplained': n(r.unexplained.gl_ia_qty),
    }
    if (amt) {
      o['IA amount'] = n(r.ia_amount)
      o['GL amount'] = n(r.gl_amount)
      o['GL−IA amount'] = n(r.diff.gl_ia_amount)
      o['GL−IA amount unexplained'] = n(r.unexplained.gl_ia_amount)
    }
    o.Status = statusLabel(r.status)
    o.Causes = causesText(r, amt)
    return o
  })

  const book = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(book, XLSX.utils.json_to_sheet(materials), 'Materials')

  if (f.expand_batches) {
    const batches: Record<string, string | number | null>[] = []
    for (const r of rows) {
      for (const b of r.batches ?? []) {
        batches.push({
          Material: r.material_code,
          Name: r.material_name ?? '',
          Batch: b.label,
          'NC batches': b.nc_batches.join(', '),
          'WMS lots': b.wms_lots.join(', '),
          'Supplier batches': b.supplier_batches.join(', '),
          Match: b.match === 'both' ? 'Both' : b.match === 'wms_only' ? 'WMS only' : 'NC only',
          WMS: n(b.wms),
          'NC (WMS wh.)': n(b.ic_wms),
          'NC (other wh.)': n(b.ic_other),
          IA: n(b.ia),
          'IC−WMS': n(b.diff_ic_wms),
        })
      }
    }
    XLSX.utils.book_append_sheet(book, XLSX.utils.json_to_sheet(batches), 'Batches')
  }

  const glOther = [
    ...first.gl_impairment.map((g) => ({ section: 'Impairment (1471)', g })),
    ...first.gl_no_material.map((g) => ({ section: 'No material', g })),
  ].map(({ section, g }) => {
    const o: Record<string, string | number | null> = {
      Section: section, Account: g.account_code, 'Account name': g.account_name ?? '', Qty: n(g.qty),
    }
    if (amt) o.Amount = n(g.amount)
    return o
  })
  XLSX.utils.book_append_sheet(book, XLSX.utils.json_to_sheet(glOther), 'GL other')

  const info = [
    ['As of', first.date],
    ['NC clock', first.nc_time_zone],
    ['Include unposted GL vouchers', first.include_unposted ? 'Yes' : 'No'],
    ['Tolerance qty', first.tolerance.qty],
    ['Tolerance amount', first.tolerance.amount],
    ['WMS live on this date', first.wms_live ? 'Yes' : 'No (WMS went live on 2025-02-03)'],
    ['Amounts included', amt ? 'Yes' : 'No (requires inventory.cost.view)'],
    ['Filters', filterSummary],
    ['Materials exported', rows.length],
  ]
  XLSX.utils.book_append_sheet(book, XLSX.utils.aoa_to_sheet(info), 'Info')

  XLSX.writeFile(book, `inventory-reconciliation-${first.date}.xlsx`)
}
