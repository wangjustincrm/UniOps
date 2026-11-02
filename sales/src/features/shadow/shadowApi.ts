import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'

// Mirrors sales-api app/schemas/shadow.py (hand-written, see partnersApi.ts).
export const SHADOW_STATUSES = ['Match', 'Differs', 'Expected difference', 'No Sales order', 'Sales only', 'Unreadable'] as const

export interface ShadowMatchRead {
  id: number
  kind: string
  status: (typeof SHADOW_STATUSES)[number]
  reference_document_id: number | null
  order_id: number | null
  reference_number: string | null
  partner_code: string | null
  differences: number
  expected_reason: string | null
  note: string | null
  compared_at: string
  version: number
  updated_by_name: string | null
}

export interface ShadowRow { zoho: string | null; sales: string | null; same: boolean }
export interface ShadowCompare extends ShadowMatchRead { rows: ShadowRow[]; reference_name: string | null; ignored: string[] }

export const shadowClient = createResourceClient<ShadowMatchRead, never>(api, '/shadow-matches')
export const fetchShadowSummary = () => api.get<Record<string, number>>('/shadow-matches/summary')
export const fetchShadowCompare = (id: number) => api.get<ShadowCompare>(`/shadow-matches/${id}`)
export const markExpected = (id: number, reason: string, version: number) =>
  api.post<ShadowMatchRead>(`/shadow-matches/${id}/expected?version=${version}`, { reason })
