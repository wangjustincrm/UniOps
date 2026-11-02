import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'

// Mirrors sales-api app/schemas/notifications.py (FR-OB-56).
export const CHANNELS = ['TASK', 'EMAIL', 'BOTH'] as const
export type Channel = (typeof CHANNELS)[number]

export interface NotificationRuleRead {
  id: number
  event_type: string
  event_label: string
  kind: 'action' | 'notify'
  gate: boolean
  channel: Channel
  users: string[]
  emails: string[]
  active: boolean
  tested_at: string | null
  test_result: string | null
  version: number
  created_at: string
  updated_at: string | null
}
export interface NotificationRuleCreate { event_type: string; channel: Channel; users: string[]; emails: string[]; active: boolean }
export type NotificationRuleUpdate = Omit<NotificationRuleCreate, 'event_type'>

export interface EventTypeRead { key: string; label: string; kind: 'action' | 'notify'; gate: boolean; rule_id: number | null }
export interface UserOption { id: string; name: string; email: string }

export interface NotificationRead {
  id: number
  at: string
  event_type: string
  event_label: string
  problem_key: string | null
  channel: 'TASK' | 'EMAIL'
  recipients: string[]
  subject: string
  outbox_id: number | null
  test: boolean
  delivery: string | null
  delivery_error: string | null
}

export const RULES_PATH = '/config/notification-rules'
export const rulesClient = createResourceClient<NotificationRuleRead, NotificationRuleCreate, NotificationRuleUpdate>(api, RULES_PATH)
export const historyClient = createResourceClient<NotificationRead, never>(api, '/notifications')
export const fetchEvents = () => api.get<EventTypeRead[]>('/config/notification-events')
export const fetchUsers = (q = '') => api.get<UserOption[]>(`/config/notification-users?q=${encodeURIComponent(q)}`)
export const sendTest = (id: number) => api.post<{ rule: NotificationRuleRead; result: string }>(`${RULES_PATH}/${id}/test`)
export const fetchRuleAudit = (id: number) => api.get<import('../AuditHistory').AuditEntry[]>(`${RULES_PATH}/${id}/audit`)
