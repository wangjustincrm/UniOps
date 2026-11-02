import type { ComboOption, Filter } from '@/platform-ui'
import { itemsClient } from './items/itemsApi'
import { partnersClient } from './partners/partnersApi'
import { warehousesClient } from './warehouses/warehousesApi'

// Options for reference fields (Autocomplete): what the user typed goes to
// the list endpoint's quick search; active records only, 20 at most.

const ACTIVE: Filter = { field: 'active', op: 'eq', value: 'true' }

// Opened with nothing typed, a field shows the first page (an empty search is not sent).
const search = (q: string) => q.trim() || undefined

export async function partnerOptions(query: string, filters: Filter[] = []): Promise<ComboOption[]> {
  const page = await partnersClient.list({ page: 1, pageSize: 20, search: search(query), filters: [ACTIVE, ...filters], sort: [{ field: 'code' }] })
  return page.items.map((p) => ({ value: p.code, label: p.code, hint: p.name }))
}

/** Customers of manual orders: partners of the MANUAL channel, by name (code as the hint). */
export async function manualPartnerOptions(query: string): Promise<ComboOption[]> {
  const page = await partnersClient.list({ page: 1, pageSize: 20, search: search(query),
    filters: [ACTIVE, { field: 'channel', op: 'eq', value: 'MANUAL' }], sort: [{ field: 'name' }] })
  return page.items.map((p) => ({ value: p.code, label: p.name, hint: p.code }))
}

export async function itemOptions(query: string): Promise<ComboOption[]> {
  const page = await itemsClient.list({ page: 1, pageSize: 20, search: search(query), filters: [ACTIVE], sort: [{ field: 'item_code' }] })
  return page.items.map((i) => ({ value: i.item_code, label: i.item_code, hint: i.description ?? undefined }))
}

/** An item's units for a choice: "EA (1)", "CASE (6 EA)" — EA first. */
export async function itemUnits(code: string): Promise<{ value: string; label: string }[]> {
  const page = await itemsClient.list({ page: 1, pageSize: 20, search: code, filters: [ACTIVE] })
  const item = page.items.find((i) => i.item_code === code)
  const units = [...(item?.units ?? [])].sort((a, b) => Number(a.factor_ea) - Number(b.factor_ea))
  return units.map((u) => {
    const n = Number(u.factor_ea)
    return { value: u.unit, label: u.unit === 'EA' ? 'EA (1)' : `${u.unit} (${Number.isInteger(n) ? n : u.factor_ea} EA)` }
  })
}

/** Active warehouses, by id (transfers). */
export async function warehouseOptions(query: string): Promise<ComboOption[]> {
  const page = await warehousesClient.list({ page: 1, pageSize: 20, search: search(query), filters: [ACTIVE], sort: [{ field: 'code' }] })
  return page.items.map((w) => ({ value: String(w.id), label: w.code, hint: w.name }))
}
