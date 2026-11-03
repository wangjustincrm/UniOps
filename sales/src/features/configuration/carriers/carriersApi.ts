import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'
import type { AuditEntry } from '../AuditHistory'

// Mirrors sales-api app/schemas/carriers.py (hand-written, see partnersApi.ts).
export interface CarrierRead {
  id: number
  scac: string
  name: string
  /** https://… with {tracking} where the number goes; null = no link */
  tracking_url: string | null
  active: boolean
  version: number
  created_at: string
  created_by_name: string
  updated_at: string | null
  updated_by_name: string | null
}
type Editable = 'name' | 'tracking_url' | 'active'
export type CarrierCreate = Pick<CarrierRead, 'scac' | Editable>
export type CarrierUpdate = Pick<CarrierRead, Editable>

export const CARRIERS_PATH = '/config/carriers'
export const carriersClient = createResourceClient<CarrierRead, CarrierCreate, CarrierUpdate>(api, CARRIERS_PATH)
export const fetchCarrierAudit = (id: number) => api.get<AuditEntry[]>(`${CARRIERS_PATH}/${id}/audit`)
export const downloadCarriers = (path: string) => api.download(path, 'carriers.csv')
