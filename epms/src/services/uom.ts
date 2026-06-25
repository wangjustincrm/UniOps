import { mdmApi } from '@/lib/api'

export type UomDimension = 'count' | 'mass' | 'volume' | 'length' | 'area' | 'time' | 'other'

export interface ApiUom {
  id: string
  code: string
  name: string
  dimension: UomDimension
  is_active: boolean
}

export interface UomListResponse {
  items: ApiUom[]
  total: number
}

export const uomService = {
  list: (activeOnly = true) =>
    mdmApi.get<UomListResponse>('/uom', { active_only: activeOnly }),
}
