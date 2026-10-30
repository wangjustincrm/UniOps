import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'
import type { AuditEntry } from '../AuditHistory'

export interface PartnerCodeRead {
  id: number
  partner_code: string
  buyer_part: string
  item_code: string
  version: number
  created_at: string
  created_by_name: string
  updated_at: string | null
  updated_by_name: string | null
}
export type PartnerCodeCreate = Pick<PartnerCodeRead, 'partner_code' | 'buyer_part' | 'item_code'>
export type PartnerCodeUpdate = Pick<PartnerCodeRead, 'item_code'>

export const PARTNER_CODES_PATH = '/config/partner-codes'
export const partnerCodesClient = createResourceClient<PartnerCodeRead, PartnerCodeCreate, PartnerCodeUpdate>(api, PARTNER_CODES_PATH)
export const fetchPartnerCodeAudit = (id: number) => api.get<AuditEntry[]>(`${PARTNER_CODES_PATH}/${id}/audit`)
export const downloadPartnerCodes = (path: string) => api.download(path, 'partner-codes.csv')
