import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'
import type { AuditEntry } from '../AuditHistory'

// Mirrors sales-api app/schemas/connections.py (hand-written, see partnersApi.ts).
export const KINDS = ['SFTP', 'SHOPIFY'] as const
export const AFTER_READ = ['LEAVE', 'MOVE', 'DELETE'] as const
export const MODES = ['LIVE', 'TEST'] as const
export const FOLDERS = ['inbound_folder', 'outbound_folder', 'processed_folder', 'error_folder',
  'test_inbound_folder', 'test_outbound_folder'] as const

export interface ConnectionRead {
  id: number
  name: string
  kind: (typeof KINDS)[number]
  host: string | null
  port: number | null
  username: string | null
  inbound_folder: string | null
  outbound_folder: string | null
  processed_folder: string | null
  error_folder: string | null
  test_inbound_folder: string | null
  test_outbound_folder: string | null
  after_read: (typeof AFTER_READ)[number]
  stable_seconds: number
  mode: (typeof MODES)[number]
  secret_name: string | null
  /** Whether the server has the secret; its value is never sent. */
  secret_present: boolean
  host_key: string | null
  auth_failed_at: string | null
  auth_failed_reason: string | null
  active: boolean
  version: number
  created_at: string
  created_by_name: string
  updated_at: string | null
  updated_by_name: string | null
}

type Editable = Omit<ConnectionRead, 'id' | 'version' | 'secret_present' | 'auth_failed_at' | 'auth_failed_reason'
  | 'created_at' | 'created_by_name' | 'updated_at' | 'updated_by_name'>
export type ConnectionCreate = Editable
export type ConnectionUpdate = Omit<Editable, 'name'>

export interface ConnectionTest {
  host_key: { line: string; fingerprint: string; pinned: boolean; matches: boolean } | null
  folders: { folder: string; ok: boolean; files: number | null; error: string | null }[]
  error: string | null
}

export const CONNECTIONS_PATH = '/config/connections'
export const connectionsClient = createResourceClient<ConnectionRead, ConnectionCreate, ConnectionUpdate>(api, CONNECTIONS_PATH)
export const fetchConnectionAudit = (id: number) => api.get<AuditEntry[]>(`${CONNECTIONS_PATH}/${id}/audit`)
export const testConnection = (id: number) => api.post<ConnectionTest>(`${CONNECTIONS_PATH}/${id}/test`)
export const clearAuthLatch = (id: number) => api.post<ConnectionRead>(`${CONNECTIONS_PATH}/${id}/clear-auth-latch`)
export const pinHostKey = (c: ConnectionRead, line: string) =>
  connectionsClient.update(c.id, { host_key: line }, c.version)
export const downloadConnections = (path: string) => api.download(path, 'connections.csv')
