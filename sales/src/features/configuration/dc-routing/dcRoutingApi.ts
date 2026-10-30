import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'
import type { AuditEntry } from '../AuditHistory'

export interface DcRoutingRead {
  id: number
  partner_code: string
  ship_to_gln: string
  dc_number: string
  notes: string | null
  active: boolean
  version: number
  created_at: string
  created_by_name: string
  updated_at: string | null
  updated_by_name: string | null
}
export type DcRoutingCreate = Pick<DcRoutingRead, 'partner_code' | 'ship_to_gln' | 'dc_number' | 'notes' | 'active'>
export type DcRoutingUpdate = Pick<DcRoutingRead, 'dc_number' | 'notes' | 'active'>

export const DC_ROUTING_PATH = '/config/dc-routing'
export const dcRoutingClient = createResourceClient<DcRoutingRead, DcRoutingCreate, DcRoutingUpdate>(api, DC_ROUTING_PATH)
export const fetchDcRoutingAudit = (id: number) => api.get<AuditEntry[]>(`${DC_ROUTING_PATH}/${id}/audit`)
export const downloadDcRouting = (path: string) => api.download(path, 'dc-routing.csv')
