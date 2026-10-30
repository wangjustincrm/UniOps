import { z } from 'zod'
import type { FieldSpec } from '@/platform-ui'
import { unitsText, type ItemRead } from './itemsApi'

// The server's own texts (sales-api app/schemas/common.py, domain/items.py).
export const EXCEL_MESSAGE = 'this looks like Excel changed a number; format the column as Text'
export const BARCODE_MESSAGE = 'must be 8, 12, 13 or 14 digits (Excel may have dropped a leading zero or changed the number; format the column as Text)'
export const EA_MESSAGE = 'every item needs the unit EA with factor 1'
export const UNITS_HINT = 'write units as UNIT=factor, separated by ; (e.g. EA=1; CASE=6 [Case (6 Cans), Case of 6])'
const UNIT_PART = /^([^=[\]]+)=([^=[\]]+?)\s*(?:\[([^[\]]*)\])?$/
const EXCEL_NUMBER = /^[+-]?\d+(\.\d+)?[eE][+-]?\d+$/
const BARCODE = /^(\d{8}|\d{12,14})$/

const code = z.string().trim().min(1, 'Required').max(50).refine((v) => !EXCEL_NUMBER.test(v), EXCEL_MESSAGE)
const optCode = z.string().trim().max(50).refine((v) => !EXCEL_NUMBER.test(v), EXCEL_MESSAGE).nullable().optional()
const barcode = z.string().trim().refine((v) => v === '' || BARCODE.test(v), BARCODE_MESSAGE).nullable().optional()

type ParsedUnit = { unit: string; factor: number; titles: string[] }

/** `;` or `,` outside the brackets separates units; inside, `,` separates titles. */
function splitUnits(text: string): string[] {
  const parts: string[] = []
  let cur = ''
  let depth = 0
  for (const ch of text) {
    if (ch === '[') depth += 1
    if (ch === ']') depth -= 1
    if ((ch === ';' || ch === ',') && depth === 0) {
      parts.push(cur)
      cur = ''
    } else cur += ch
  }
  return [...parts, cur]
}

const titleKey = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '')

/** `EA=1; CASE=6 [Case (6 Cans)]` → units, or the reason it is not valid
 * (sales-api domain/items.parse_units and check_units). */
export function parseUnits(text: string): { units: ParsedUnit[]; error?: string } {
  const units: ParsedUnit[] = []
  for (const part of splitUnits(text).map((p) => p.trim()).filter(Boolean)) {
    const m = UNIT_PART.exec(part)
    const name = m?.[1].trim()
    const factor = Number(m?.[2].trim())
    if (!m || !name || !Number.isFinite(factor) || factor <= 0) return { units, error: UNITS_HINT }
    const titles = (m[3] ?? '').split(',').map((t) => t.trim()).filter(Boolean)
    units.push({ unit: name.toUpperCase(), factor, titles })
  }
  const names = units.map((u) => u.unit)
  if (new Set(names).size !== names.length) return { units, error: 'a unit appears twice' }
  if (units.find((u) => u.unit === 'EA')?.factor !== 1) return { units, error: EA_MESSAGE }
  const owner = new Map(units.map((u) => [titleKey(u.unit), u.unit]))
  for (const u of units) {
    for (const t of u.titles) {
      const key = titleKey(t)
      if (!key) return { units, error: `Shopify title '${t}' of ${u.unit} has no letters or digits` }
      if ((owner.get(key) ?? u.unit) !== u.unit) return { units, error: `Shopify title '${t}' names both ${owner.get(key)} and ${u.unit}` }
      owner.set(key, u.unit)
    }
  }
  return { units }
}

const unitsField = z.string().superRefine((v, ctx) => {
  const r = parseUnits(v ?? '')
  if (r.error) ctx.addIssue({ code: 'custom', message: r.error })
})

const fields = {
  description: z.string().trim().nullable().optional(),
  sku: optCode,
  upc: barcode,
  gtin: barcode,
  gross_weight_lb_per_ea: z.number().min(0, 'Cannot be negative').nullable().optional(),
  warehouse_held: z.boolean(),
  units: unitsField,
  active: z.boolean(),
}
export const itemCreateSchema = z.object({ item_code: code, ...fields })
export const itemUpdateSchema = z.object(fields)

export const itemFields: FieldSpec[] = [
  { name: 'item_code', label: 'Item code', required: true, hint: 'Cannot be changed after the item is created.' },
  { name: 'description', label: 'Description' },
  { name: 'sku', label: 'SKU', hint: 'Shopify lines match the item by SKU, then by the Shopify barcode as GTIN, then as UPC — never by item code.' },
  { name: 'upc', label: 'UPC' },
  { name: 'gtin', label: 'GTIN' },
  { name: 'gross_weight_lb_per_ea', label: 'Gross weight per EA (lb)', type: 'number' },
  { name: 'warehouse_held', label: 'Held in the warehouse', type: 'checkbox' },
  { name: 'units', label: 'Units', required: true,
    hint: 'UNIT=EA per unit, separated by ; — EA=1 is required (e.g. EA=1; CASE=6; 2PACK=2). After a unit, the Shopify variant titles that mean it may follow in brackets: 2PACK=2 [2-Can, 2-Pack]. A Shopify title naming no unit is EA, unless it looks like a pack ("Box of 12"): then the order waits in Review. A unit removed here is deactivated, never deleted.' },
  { name: 'active', label: 'Active', type: 'checkbox' },
]

export const itemEmptyValues: Record<string, unknown> = {
  item_code: '', description: '', sku: '', upc: '', gtin: '', gross_weight_lb_per_ea: null, warehouse_held: true,
  units: 'EA=1', active: true,
}

export function itemToFormValues(i: ItemRead): Record<string, unknown> {
  return Object.fromEntries(itemFields.map((f) => [f.name, f.name === 'units' ? unitsText(i.units) :
    f.name === 'gross_weight_lb_per_ea' ? (i.gross_weight_lb_per_ea === null ? null : Number(i.gross_weight_lb_per_ea)) :
      i[f.name as keyof ItemRead]]))
}
