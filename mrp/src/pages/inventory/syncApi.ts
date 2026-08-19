// How fresh the WMS mirror is — mrp-api's GET /admin/wms-sync/status, plus the
// manual trigger behind the Refresh button.
//
// Everything on the Inventory screen is a read of a SNAPSHOT of the warehouse,
// not of the warehouse. Which means the age of that snapshot is part of every
// number on the page, and until this existed the page had no way to say it:
// the only date it showed was today's, used as the shelf-life reference.
import { api } from '@/lib/api'

import type { WmsSyncStatus } from './freshness'

export const syncApi = {
  wmsStatus: () => api.get<WmsSyncStatus>('/admin/wms-sync/status'),
  runWmsSync: () => api.post<{ lots: number; locations?: number; synced_at: string
                               skipped?: boolean }>('/admin/wms-sync', {}),
}

export type { WmsSyncStatus } from './freshness'
export { freshness, relativeAge } from './freshness'
