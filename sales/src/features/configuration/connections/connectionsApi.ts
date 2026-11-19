import { createResourceClient } from '@/platform-ui'
import { api } from '@/lib/api'
import type { AuditEntry } from '../AuditHistory'

// Mirrors sales-api app/schemas/connections.py (hand-written, see partnersApi.ts).
export const KINDS = ['SFTP', 'SHOPIFY', 'AUTHORIZE_NET', 'PAYPAL'] as const
export const KIND_LABEL: Record<(typeof KINDS)[number], string> = { SFTP: 'SFTP', SHOPIFY: 'Shopify', AUTHORIZE_NET: 'Authorize.Net', PAYPAL: 'PayPal' }

/** A payment gateway's reporting API (Finance: Authorize.Net 2026-11-17, PayPal 2026-11-19): read only, its words
 *  once here; the form, its rules, the checklist and the list read them (a new gateway is a new entry). */
export interface GatewayKind {
  label: string
  /** Production first, then the sandbox (the server allows only these). */
  hosts: readonly [string, string]
  about: string
  reads: string
  /** What the cursor is the start of ("batches settled from"). */
  cursorWhat: string
  login: string
  secret: string
}
export const GATEWAY_KINDS: Partial<Record<(typeof KINDS)[number], GatewayKind>> = {
  AUTHORIZE_NET: {
    label: 'Authorize.Net', hosts: ['api.authorize.net', 'apitest.authorize.net'],
    about: "Authorize.Net: its settled batches and their transactions, for Finance. It only reads. Amounts are taken as CAD (the merchant account's currency).",
    reads: "Sales reads Authorize.Net's settled batches and their transactions.", cursorWhat: 'batches settled from',
    login: 'The API Login ID (Account → API Credentials & Keys). It is not a secret.',
    secret: 'The name of the Transaction Key on the server, e.g. SALES_SECRET_AUTHNET. The key itself is never typed here.',
  },
  PAYPAL: {
    label: 'PayPal', hosts: ['api-m.paypal.com', 'api-m.sandbox.paypal.com'],
    about: "PayPal: the account's transactions with their fees, for Finance. It only reads. The REST app needs Transaction Search turned on.",
    reads: "Sales reads PayPal's transactions and fees (they show a few hours late).", cursorWhat: 'transactions from',
    login: 'The REST app\'s Client ID (developer.paypal.com → Apps & Credentials). It is not a secret.',
    secret: 'The name of the app\'s Secret on the server, e.g. SALES_SECRET_PAYPAL. The Secret itself is never typed here.',
  },
}
export const gatewayOf = (kind: unknown): GatewayKind | null => GATEWAY_KINDS[kind as (typeof KINDS)[number]] ?? null
export const AFTER_READ = ['LEAVE', 'MOVE', 'DELETE'] as const
// An SFTP connection's folders: reading has its folder, its archive (after-read Move) and its error folder;
// writing has one folder (user 2026-11-12: no Test / Live mode, no test folders; a Read and a Write tick).
export const READ_FOLDERS = ['inbound_folder', 'processed_folder', 'error_folder'] as const
export const WRITE_FOLDERS = ['outbound_folder'] as const
export const FOLDERS = [...READ_FOLDERS, ...WRITE_FOLDERS] as const

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
  after_read: (typeof AFTER_READ)[number]
  stable_seconds: number
  /** Sales may read this connection (Shopify: pull orders). */
  can_read: boolean
  /** Sales may write to it: files into the write folder, moves after reading (Shopify: fulfilments, close). */
  can_write: boolean
  secret_name: string | null
  /** Whether the server has the secret; its value is never sent. */
  secret_present: boolean
  host_key: string | null
  auth_failed_at: string | null
  auth_failed_reason: string | null
  api_version: string | null
  /** Shopify: where the next pull starts (inclusive); Authorize.Net: batches settled from then. Written by the system. */
  cursor_updated_at: string | null
  page_size: number | null
  order_number_cutoff: number | null
  partner_code: string | null
  /** Its schedule: every N minutes, unless paused, in the optional window (Toronto time; days "Mon,Fri"). */
  schedule_minutes: number
  schedule_paused: boolean
  active_from: string | null
  active_to: string | null
  active_days: string | null
  /** Written by the scheduler. */
  last_run_at: string | null
  last_run_status: string | null
  last_run_summary: string | null
  last_error: string | null
  next_run_at: string | null
  /** The next run is overdue (the scheduler on, not paused, in its hours). */
  overdue?: boolean
  active: boolean
  version: number
  created_at: string
  created_by_name: string
  updated_at: string | null
  updated_by_name: string | null
}

type Editable = Omit<ConnectionRead, 'id' | 'version' | 'secret_present' | 'auth_failed_at' | 'auth_failed_reason' | 'cursor_updated_at'
  | 'created_at' | 'created_by_name' | 'updated_at' | 'updated_by_name' | 'last_run_at' | 'last_run_status' | 'last_run_summary'
  | 'last_error' | 'next_run_at'>
export type ConnectionCreate = Editable
export type ConnectionUpdate = Omit<Editable, 'name'>

export interface ConnectionTest {
  host_key: { line: string; fingerprint: string; pinned: boolean; matches: boolean } | null
  folders: { folder: string; ok: boolean; files: number | null; error: string | null }[]
  error: string | null
  shop?: { name?: string; domain?: string; myshopify_domain?: string; currency?: string; iana_timezone?: string } | null
  /** Authorize.Net: the host, and that the login was accepted. */
  gateway?: { host?: string; login?: string } | null
}

export const CONNECTIONS_PATH = '/config/connections'
export const connectionsClient = createResourceClient<ConnectionRead, ConnectionCreate, ConnectionUpdate>(api, CONNECTIONS_PATH)
export const fetchConnectionAudit = (id: number) => api.get<AuditEntry[]>(`${CONNECTIONS_PATH}/${id}/audit`)
export const testConnection = (id: number) => api.post<ConnectionTest>(`${CONNECTIONS_PATH}/${id}/test`)
export const clearAuthLatch = (id: number) => api.post<ConnectionRead>(`${CONNECTIONS_PATH}/${id}/clear-auth-latch`)
export const resetCursor = (id: number, cursor: string | null) =>
  api.post<ConnectionRead>(`${CONNECTIONS_PATH}/${id}/reset-cursor`, { cursor_updated_at: cursor })
export const pinHostKey = (c: ConnectionRead, line: string) =>
  connectionsClient.update(c.id, { host_key: line }, c.version)
export const downloadConnections = (path: string) => api.download(path, 'connections.csv')

/** P4: the warehouse SFTP connection for 940s and 997s, and the 997 wait. */
export interface EdiSettings {
  connection_id: number | null; ack_timeout_minutes: number; invoice_connection_id?: number | null
  /** FR-OB-32: company addresses that get every document of each invoice sent. */
  internal_emails?: string[]
  /** X2: who receives every Action Required task (none: no one). */
  operator_user_id?: string | null
  /** Manual orders (set by the product owner): Purolator Express offered; LTL suggested above this weight. */
  express_offered?: boolean
  ltl_from_lb?: number
}
export const fetchEdiSettings = () => api.get<EdiSettings>('/config/edi-settings')
export const saveEdiSettings = (data: EdiSettings) => api.put<EdiSettings>('/config/edi-settings', data)


/** Automatic runs: `paused` is the user's switch (Off), `enabled` whether this server may run them at all. */
export interface SchedulerSettings { paused: boolean; system_minutes: number; enabled: boolean }
export const fetchSchedulerSettings = () => api.get<SchedulerSettings>('/config/scheduler')
/** Only what changes: saving the minutes never touches the switch. */
export const saveSchedulerSettings = (data: { paused?: boolean; system_minutes?: number }) => api.put<SchedulerSettings>('/config/scheduler', data)

/** This connection's job now (sales.file.retry): ignores its schedule, never its single flight. */
export const runConnectionNow = (id: number) => api.post<{ run_id: number }>(`${CONNECTIONS_PATH}/${id}/run-now`)

export const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const

/** "every 5 min, Mon–Fri 06:00–22:00" — the same words as sales-api's domain/schedule.py describe(). */
export function describeSchedule(c: ScheduleFields): string {
  let out = `every ${c.schedule_minutes} min`
  const days = daysText((c.active_days ?? '').split(',').filter(Boolean))
  if (days) out += `, ${days}`
  if (c.active_from && c.active_to) out += ` ${c.active_from.slice(0, 5)}–${c.active_to.slice(0, 5)}`
  return out
}

type ScheduleFields = Pick<ConnectionRead, 'schedule_minutes' | 'active_from' | 'active_to' | 'active_days'>

/** "Mon–Fri", "Mon Fri"; nothing for every day. */
function daysText(days: string[]): string | null {
  if (!days.length || days.length >= 7) return null
  const idx = days.map((d) => DAYS.indexOf(d as (typeof DAYS)[number]))
  const run = days.length > 2 && idx.every((v, i) => i === 0 || v === idx[i - 1] + 1)
  return run ? `${days[0]}–${days[days.length - 1]}` : days.join(' ')
}

/** The list's words: "Every 5 min · Mon–Fri 06:00–22:00", or "Paused". */
export function scheduleLabel(c: ScheduleFields & Pick<ConnectionRead, 'schedule_paused'>): string {
  if (c.schedule_paused) return 'Paused'
  const when = [daysText((c.active_days ?? '').split(',').filter(Boolean)),
    c.active_from && c.active_to ? `${c.active_from.slice(0, 5)}–${c.active_to.slice(0, 5)}` : null].filter(Boolean).join(' ')
  return [`Every ${c.schedule_minutes} min`, when].filter(Boolean).join(' · ')
}

/** The form's sentence, live from its values: "Runs every 5 minutes, Mon–Fri, 06:00–22:00 Toronto". */
export function scheduleSentence(v: { schedule_minutes?: unknown; schedule_paused?: unknown; active_from?: unknown; active_to?: unknown; active_days?: unknown }): string {
  if (v.schedule_paused) return 'Paused: it runs only with Run now.'
  const n = Number(v.schedule_minutes)
  if (!n) return 'Choose how often it runs.'
  const days = Array.isArray(v.active_days) ? DAYS.filter((d) => (v.active_days as string[]).includes(d)) : []
  const from = String(v.active_from ?? '').trim(), to = String(v.active_to ?? '').trim()
  return `Runs every ${n === 1 ? 'minute' : `${n} minutes`}, ${daysText([...days]) ?? 'every day'}, ${from && to ? `${from}–${to} Toronto` : 'all day'}`
}

const TORONTO_PARTS = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/Toronto', weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

/** Whether `at` (ms) is inside the connection's active window — the same rule
 *  as sales-api's domain/schedule.py (Toronto time; an end before the start
 *  crosses midnight and belongs to the day it started). */
export function insideWindow(c: Pick<ConnectionRead, 'active_from' | 'active_to' | 'active_days'>, at: number): boolean {
  const parts = Object.fromEntries(TORONTO_PARTS.formatToParts(at).map((p) => [p.type, p.value]))
  let day = DAYS.indexOf(parts.weekday as (typeof DAYS)[number])
  const t = `${parts.hour}:${parts.minute}`
  const start = c.active_from?.slice(0, 5), end = c.active_to?.slice(0, 5)
  if (start && end && start !== end) {
    if (start < end) {
      if (!(start <= t && t < end)) return false
    } else if (t < end) {
      day = (day + 6) % 7  // the early-morning part belongs to yesterday's window
    } else if (t < start) {
      return false
    }
  }
  const days = (c.active_days ?? '').split(',').filter(Boolean)
  return days.length === 0 || days.includes(DAYS[day])
}
