// When WMS figures begin — served by inventory-api (see WmsHistoryNote).
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export interface WmsHistory {
  go_live_month: string
  /** YYYY-MM-DD — first day WMS has figures for. */
  history_start: string
  note: string
}

/** For pages whose own response does not carry wms_history. */
export function useWmsHistory() {
  return useQuery<WmsHistory>({
    queryKey: ['wms-history'],
    queryFn: () => api.get<WmsHistory>('/settings/wms-history'),
    staleTime: 60 * 60_000,
  })
}
