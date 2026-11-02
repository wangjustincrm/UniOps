import type { Filter } from '@/platform-ui'
import { api } from '@/lib/api'

// Mirrors sales-api app/schemas/health.py (FR-OB-52 / 52a / 52b).
export type HealthResult = 'PASSED' | 'WARNING' | 'FAILED'

/** The list behind a card or check: the page and, for a list, its resource and filters. */
export interface ListLink {
  page: string
  resource?: string
  filters?: Filter[]
}

export interface CheckRead {
  key: string
  label: string
  result: HealthResult
  detail: string
  count: number | null
  link: ListLink | null
}

export interface GateEventRead {
  event_type: string
  label: string
  rule_id: number | null
  recipients: number
  tested_at: string | null
  ok: boolean
  problem: string | null
}

/** One scheduled job: a connection's, or the system job. */
export interface JobRead {
  scope: string
  label: string
  connection_id: number | null
  interval_minutes: number
  schedule: string
  paused: boolean
  last_run_at: string | null
  last_run_status: string | null
  last_run_summary: string | null
  next_run_at: string | null
  /** More than 3 intervals past its next run while it should run. */
  overdue: boolean
}

export interface HealthRead {
  result: HealthResult
  checked_at: string
  checks: CheckRead[]
  cards: Record<string, number>
  shadow_mode: boolean
  partner_writes_enabled: boolean
  external_email_enabled: boolean
  scheduler_enabled: boolean
  gate: GateEventRead[]
  gate_ok: boolean
  jobs: JobRead[]
  scheduler_paused: boolean
  system_minutes: number
}

export interface CardRead {
  key: string
  label: string
  count: number
  link: ListLink
  parts: CardRead[]
}

export interface DashboardRead {
  cards: CardRead[]
  open_tasks: { type: string; count: number }[]
  needs_review: number
  waiting_delivery: number
  runs: { id: number; trigger: string; status: string; started_at: string; finished_at: string | null; error: string | null }[]
}

export interface InboxTarget {
  page: string
  resource: string | null
  id: number | null
}

export const fetchHealth = () => api.get<HealthRead>('/operations/health')
export const fetchDashboard = () => api.get<DashboardRead>('/operations/dashboard')
export const resolveInbox = (key: string) => api.get<InboxTarget>(`/inbox/${encodeURIComponent(key)}`)

export interface SchedulerSettings { paused: boolean; system_minutes: number }
export const saveScheduler = (body: SchedulerSettings) => api.put<SchedulerSettings>('/config/scheduler', body)
