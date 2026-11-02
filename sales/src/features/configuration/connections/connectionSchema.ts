import { z } from 'zod'
import type { FieldSpec } from '@/platform-ui'
import { AFTER_READ, DAYS, FOLDERS, KINDS, MODES, type ConnectionRead } from './connectionsApi'

// Mirrors the API's DTO and the connection rules (sales-api domain/connections.py).
const optional = (max?: number) => {
  const s = z.string().trim()
  return (max ? s.max(max, `At most ${max} characters`) : s).nullable().optional()
}
const choice = <T extends readonly [string, ...string[]]>(values: T) => z.enum(values, { error: 'Choose one' })
const clock = z.string().trim().refine((v) => v === '' || /^([01]\d|2[0-3]):[0-5]\d$/.test(v), 'A time like 06:00')
  .transform((v) => (v ? `${v}:00` : null)).nullable().optional()
const SECRET = /^[A-Z][A-Z0-9_]*$/
const EXCEL_NUMBER = /^[+-]?\d+(\.\d+)?[eE][+-]?\d+$/
const KEY_LINE = /^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com) [A-Za-z0-9+/]+={0,3}( .*)?$/
// The server's own texts (sales-api app/schemas/common.py); a value is never repeated.
export const HOST_KEY_MESSAGE = "host_key must be the server's public key line (e.g. 'ssh-ed25519 AAAA…'). Leave it empty and use Test connection → Pin this key. Passwords and tokens never go in the CSV; put them in the server environment under secret_name."
export const SECRET_NAME_MESSAGE = 'secret_name must be an environment variable name like SALES_SECRET_RADIANT (capitals, digits, underscore)'
export const EXCEL_MESSAGE = 'this looks like Excel changed a number; format the column as Text'

const fields = {
  kind: choice(KINDS),
  host: optional(255),
  port: z.number({ error: 'Enter a port' }).int().min(1, 'From 1').max(65535, 'At most 65535').nullable().optional(),
  username: optional(255).refine((v) => !v || !EXCEL_NUMBER.test(v.trim()), EXCEL_MESSAGE),
  inbound_folder: optional(), outbound_folder: optional(), processed_folder: optional(), error_folder: optional(),
  test_inbound_folder: optional(), test_outbound_folder: optional(),
  after_read: choice(AFTER_READ),
  stable_seconds: z.number({ error: 'Enter seconds' }).int('Whole seconds').min(0, 'Cannot be negative'),
  mode: choice(MODES),
  secret_name: z.string().trim().max(100).refine((v) => v === '' || SECRET.test(v), SECRET_NAME_MESSAGE).nullable().optional(),
  host_key: z.string().trim().refine((v) => v === '' || KEY_LINE.test(v), HOST_KEY_MESSAGE).nullable().optional(),
  api_version: z.string().trim().refine((v) => v === '' || /^\d{4}-\d{2}$/.test(v), 'A version like 2026-04').nullable().optional(),
  page_size: z.number().int().min(1, 'From 1').max(250, 'At most 250').nullable().optional(),
  order_number_cutoff: z.number().int().min(0).nullable().optional(),
  partner_code: optional(50),
  // The schedule (user 2026-11-02): times as HH:MM (Toronto), days as a list — sent as "06:00:00" and "Mon,Fri".
  schedule_minutes: z.number({ error: 'Enter minutes' }).int('Whole minutes').min(1, 'At least 1').max(1440, 'At most 1440'),
  schedule_paused: z.boolean(),
  active_from: clock, active_to: clock,
  active_days: z.array(z.enum(DAYS)).transform((d) => (d.length ? DAYS.filter((x) => d.includes(x)).join(',') : null)),
  active: z.boolean(),
}

type Values = { kind?: string; after_read?: string; processed_folder?: string | null; host_key?: string | null; host?: string | null; partner_code?: string | null; active_from?: string | null; active_to?: string | null } & Record<string, unknown>
const SFTP_REQUIRED = ['host', 'username', 'inbound_folder', 'secret_name'] as const

function rules(v: Values, ctx: z.RefinementCtx) {
  if (v.kind === 'SFTP') {
    for (const f of SFTP_REQUIRED) {
      if (!String(v[f] ?? '').trim()) ctx.addIssue({ code: 'custom', path: [f], message: 'Required for an SFTP connection' })
    }
  }
  if (v.kind === 'SHOPIFY') {
    if (String(v.host_key ?? '').trim()) ctx.addIssue({ code: 'custom', path: ['host_key'], message: 'host_key must be empty for a SHOPIFY connection' })
    if (!String(v.host ?? '').trim().toLowerCase().endsWith('.myshopify.com')) {
      ctx.addIssue({ code: 'custom', path: ['host'], message: "The store's <name>.myshopify.com address" })
    }
    for (const f of ['api_version', 'partner_code'] as const) {
      if (!String(v[f] ?? '').trim()) ctx.addIssue({ code: 'custom', path: [f], message: 'Required for a SHOPIFY connection' })
    }
  } else if (String(v.partner_code ?? '').trim()) {
    ctx.addIssue({ code: 'custom', path: ['partner_code'], message: "Only for SHOPIFY; an SFTP channel's partner comes from each file" })
  }
  if (!String(v.active_from ?? '').trim() !== !String(v.active_to ?? '').trim()) {
    const missing = String(v.active_from ?? '').trim() ? 'active_to' : 'active_from'
    ctx.addIssue({ code: 'custom', path: [missing], message: 'Give both the start and the end, or neither' })
  }
  if (v.after_read === 'MOVE' && !String(v.processed_folder ?? '').trim()) {
    ctx.addIssue({ code: 'custom', path: ['processed_folder'], message: 'Required when after-read is MOVE' })
  }
}

export const connectionCreateSchema = z.object({ name: z.string().trim().min(1, 'Required').max(100), ...fields }).superRefine(rules)
export const connectionUpdateSchema = z.object(fields).superRefine(rules)

const options = (values: readonly string[]) => values.map((v) => ({ value: v, label: v }))
const folderLabel: Record<(typeof FOLDERS)[number], string> = {
  inbound_folder: 'Inbound folder', outbound_folder: 'Outbound folder', processed_folder: 'Processed folder',
  error_folder: 'Error folder', test_inbound_folder: 'Test inbound folder', test_outbound_folder: 'Test outbound folder',
}

export const connectionFields: FieldSpec[] = [
  { name: 'name', label: 'Name', required: true, hint: 'Cannot be changed after the connection is created.' },
  { name: 'kind', label: 'Kind', type: 'select', options: options(KINDS), required: true },
  { name: 'host', label: 'Host', hint: 'Must also be listed in the server setting SALES_SFTP_ALLOWED_HOSTS.' },
  { name: 'port', label: 'Port', type: 'number' },
  { name: 'username', label: 'User name' },
  ...FOLDERS.map((f) => ({ name: f, label: folderLabel[f] })),
  { name: 'after_read', label: 'After read', type: 'select', options: options(AFTER_READ), required: true,
    hint: 'MOVE and DELETE happen only on a LIVE connection with partner writes switched on; otherwise the file is left.' },
  { name: 'stable_seconds', label: 'Wait before reading (seconds)', type: 'number', required: true,
    hint: 'A file is read once its modified time is at least this old.' },
  { name: 'mode', label: 'Mode', type: 'select', options: options(MODES), required: true },
  { name: 'secret_name', label: 'Secret name', hint: 'The server environment variable that holds the password. The password itself is never entered here.' },
  { name: 'host_key', label: 'Pinned host key', type: 'textarea', hint: 'SFTP only. Set by "Pin this key" after a connection test.' },
  { name: 'api_version', label: 'Shopify API version', placeholder: '2026-04', hint: 'Shopify only.' },
  { name: 'page_size', label: 'Orders per page', type: 'number', hint: 'Shopify only; at most 250 (default 50).' },
  { name: 'order_number_cutoff', label: 'Order number cut-off', type: 'number', hint: 'Shopify only: orders numbered below it are historical and never sent (FR-OB-17).' },
  { name: 'partner_code', label: 'Partner', hint: "Shopify only: whose orders the store's are." },
  { name: 'schedule_minutes', label: 'Schedule: every (minutes)', type: 'number', required: true,
    hint: "How often this connection's job runs: it reads the connection and takes what it stored through (850s, 997s, 945s, 944s)." },
  { name: 'schedule_paused', label: 'Schedule paused', type: 'checkbox' },
  { name: 'active_from', label: 'Active from (HH:MM, Toronto)', placeholder: '06:00', hint: 'Empty: all day. An end before the start crosses midnight.' },
  { name: 'active_to', label: 'Active until (HH:MM, Toronto)', placeholder: '22:00' },
  { name: 'active_days', label: 'Active days', type: 'multiselect', options: DAYS.map((d) => ({ value: d, label: d })),
    hint: 'None ticked: every day.' },
  { name: 'active', label: 'Active', type: 'checkbox' },
]

export const connectionEmptyValues: Record<string, unknown> = {
  name: '', kind: 'SFTP', host: '', port: 22, username: '', inbound_folder: '', outbound_folder: '', processed_folder: '',
  error_folder: '', test_inbound_folder: '', test_outbound_folder: '', after_read: 'LEAVE', stable_seconds: 60,
  mode: 'TEST', secret_name: '', host_key: '', api_version: '', page_size: null, order_number_cutoff: null, partner_code: '',
  schedule_minutes: 5, schedule_paused: false, active_from: '', active_to: '', active_days: [], active: true,
}

export function connectionToFormValues(c: ConnectionRead): Record<string, unknown> {
  return {
    ...Object.fromEntries(connectionFields.map((f) => [f.name, c[f.name as keyof ConnectionRead]])),
    active_from: c.active_from?.slice(0, 5) ?? '', active_to: c.active_to?.slice(0, 5) ?? '',
    active_days: (c.active_days ?? '').split(',').filter(Boolean),
  }
}
