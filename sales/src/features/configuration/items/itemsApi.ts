import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'
import type { AuditEntry } from '../AuditHistory'

// Mirrors sales-api app/schemas/items.py (hand-written, see partnersApi.ts).
export interface UnitRead { unit: string; factor_ea: string; shopify_titles: string[] }

export interface ItemRead {
  id: number
  item_code: string
  description: string | null
  sku: string | null
  upc: string | null
  gtin: string | null
  gross_weight_lb_per_ea: string | null
  warehouse_held: boolean
  units: UnitRead[]
  active: boolean
  version: number
  created_at: string
  created_by_name: string
  updated_at: string | null
  updated_by_name: string | null
}

// The form and the CSV write units as text; the API reads it (EA=1; CASE=6 [Case (6 Cans)]).
type Editable = Omit<ItemRead, 'id' | 'version' | 'units' | 'created_at' | 'created_by_name' | 'updated_at' | 'updated_by_name'> & { units: string }
export type ItemCreate = Editable
export type ItemUpdate = Omit<Editable, 'item_code'>

export const ITEMS_PATH = '/config/items'
export const itemsClient = createResourceClient<ItemRead, ItemCreate, ItemUpdate>(api, ITEMS_PATH)
export const fetchItemAudit = (id: number) => api.get<AuditEntry[]>(`${ITEMS_PATH}/${id}/audit`)
export const downloadItems = (path: string) => api.download(path, 'items.csv')

/** `EA=1; CASE=6 [Case (6 Cans)]` — the factors without trailing zeros, then
 * the unit's Shopify titles in brackets (sales-api domain/items.units_text). */
export function unitsText(units: UnitRead[]): string {
  return units.map((u) => `${u.unit}=${Number(u.factor_ea)}` +
    (u.shopify_titles?.length ? ` [${u.shopify_titles.join(', ')}]` : '')).join('; ')
}
