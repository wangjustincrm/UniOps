import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'
import type { AuditEntry } from '../AuditHistory'

// Mirrors sales-api app/schemas/warehouses.py (hand-written, see partnersApi.ts).
export const WAREHOUSE_ROLES = ['SHIPPER', 'WAREHOUSE', 'TRANSFER_SOURCE'] as const
export interface WarehouseRead {
  id: number
  code: string
  name: string
  role: (typeof WAREHOUSE_ROLES)[number]
  edi_id_qualifier: string | null
  edi_id: string | null
  address1: string | null
  city: string | null
  province: string | null
  zip: string | null
  country: string | null
  address_lines: string | null
  active: boolean
  version: number
  created_at: string
  created_by_name: string
  updated_at: string | null
  updated_by_name: string | null
}
type Editable = 'name' | 'role' | 'edi_id_qualifier' | 'edi_id' | 'address1' | 'city' | 'province' | 'zip' | 'country' | 'address_lines' | 'active'
export type WarehouseCreate = Pick<WarehouseRead, 'code' | Editable>
export type WarehouseUpdate = Pick<WarehouseRead, Editable>

export const WAREHOUSES_PATH = '/config/warehouses'
export const warehousesClient = createResourceClient<WarehouseRead, WarehouseCreate, WarehouseUpdate>(api, WAREHOUSES_PATH)
export const fetchWarehouseAudit = (id: number) => api.get<AuditEntry[]>(`${WAREHOUSES_PATH}/${id}/audit`)
export const downloadWarehouses = (path: string) => api.download(path, 'warehouses.csv')
