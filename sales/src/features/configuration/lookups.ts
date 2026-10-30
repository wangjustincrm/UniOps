import type { ComboOption, Filter } from '@/platform-ui'
import { itemsClient } from './items/itemsApi'
import { partnersClient } from './partners/partnersApi'

// Options for reference fields (Autocomplete): what the user typed goes to
// the list endpoint's quick search; active records only, 20 at most.

const ACTIVE: Filter = { field: 'active', op: 'eq', value: 'true' }

export async function partnerOptions(query: string, filters: Filter[] = []): Promise<ComboOption[]> {
  const page = await partnersClient.list({ page: 1, pageSize: 20, search: query, filters: [ACTIVE, ...filters], sort: [{ field: 'code' }] })
  return page.items.map((p) => ({ value: p.code, label: p.code, hint: p.name }))
}

/** Partners of the MANUAL channel (manual orders). */
export const manualPartnerOptions = (query: string) => partnerOptions(query, [{ field: 'channel', op: 'eq', value: 'MANUAL' }])

export async function itemOptions(query: string): Promise<ComboOption[]> {
  const page = await itemsClient.list({ page: 1, pageSize: 20, search: query, filters: [ACTIVE], sort: [{ field: 'item_code' }] })
  return page.items.map((i) => ({ value: i.item_code, label: i.item_code, hint: i.description ?? undefined }))
}
