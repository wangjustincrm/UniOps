import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'

// Mirrors sales-api app/schemas/partners.py (hand-written: the OpenAPI type
// generation of design §13.15 is not set up yet).
export const CHANNELS = ['SHOPIFY', 'TRUECOMMERCE', 'MANUAL'] as const
export const SETTLEMENT_MODES = ['PREPAID', 'INVOICE'] as const
export const SENDING_MODES = ['AUTOMATIC', 'MANUAL'] as const
export const INVOICE_METHODS = ['810_JSON', 'EMAIL', 'NONE'] as const

export interface PartnerRead {
  id: number
  code: string
  name: string
  abbrev: string | null
  channel: (typeof CHANNELS)[number]
  settlement_mode: (typeof SETTLEMENT_MODES)[number]
  sending_mode: (typeof SENDING_MODES)[number]
  delay_minutes: number
  invoice_method: (typeof INVOICE_METHODS)[number]
  invoice_email: string | null
  invoice_cc: string | null
  payment_terms: string | null
  dc_routing_required: boolean
  min_shelf_life_days: number | null
  label_code: string | null
  carrier_defaults: Record<string, unknown> | null
  active: boolean
  version: number
  created_at: string
  created_by_name: string
  updated_at: string | null
  updated_by_name: string | null
}

type Editable = Omit<PartnerRead, 'id' | 'version' | 'created_at' | 'created_by_name' | 'updated_at' | 'updated_by_name'>
export type PartnerCreate = Editable
export type PartnerUpdate = Omit<Editable, 'code'>

export interface AuditEntry {
  id: number
  at: string
  user_name: string
  action: 'create' | 'update' | 'delete'
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  reason: string | null
}

export const PARTNERS_PATH = '/config/partners'
export const partnersClient = createResourceClient<PartnerRead, PartnerCreate, PartnerUpdate>(api, PARTNERS_PATH)
export const fetchPartnerAudit = (id: number) => api.get<AuditEntry[]>(`${PARTNERS_PATH}/${id}/audit`)
export const downloadPartners = (path: string) => api.download(path, 'partners.csv')
