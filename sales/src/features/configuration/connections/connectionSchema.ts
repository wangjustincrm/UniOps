import { z } from 'zod'
import type { FieldValues } from 'react-hook-form'
import type { FieldSpec } from '@/platform-ui'
import { AFTER_READ, DAYS, FOLDERS, KINDS, MODES, scheduleSentence, type ConnectionRead } from './connectionsApi'

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
// In plain words; a value is never repeated (the server refuses the same, sales-api app/schemas/common.py).
export const HOST_KEY_MESSAGE = "This must be the server's public key line. Leave it empty, then test the connection on its Overview and pin the key. A password never goes here."
export const SECRET_NAME_MESSAGE = 'Use the name of the password on the server: capitals, digits and underscores. The password itself never goes here.'
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
    if (String(v.host_key ?? '').trim()) ctx.addIssue({ code: 'custom', path: ['host_key'], message: 'A Shopify connection has no server key' })
    if (!String(v.host ?? '').trim().toLowerCase().endsWith('.myshopify.com')) {
      ctx.addIssue({ code: 'custom', path: ['host'], message: "The store's <name>.myshopify.com address" })
    }
    for (const f of ['api_version', 'partner_code'] as const) {
      if (!String(v[f] ?? '').trim()) ctx.addIssue({ code: 'custom', path: [f], message: 'Required for a Shopify connection' })
    }
  } else if (String(v.partner_code ?? '').trim()) {
    ctx.addIssue({ code: 'custom', path: ['partner_code'], message: "Only for Shopify: an SFTP connection's partner comes from each file" })
  }
  if (!String(v.active_from ?? '').trim() !== !String(v.active_to ?? '').trim()) {
    const missing = String(v.active_from ?? '').trim() ? 'active_to' : 'active_from'
    ctx.addIssue({ code: 'custom', path: [missing], message: 'Give both the start and the end, or neither' })
  }
  if (v.after_read === 'MOVE' && !String(v.processed_folder ?? '').trim()) {
    ctx.addIssue({ code: 'custom', path: ['processed_folder'], message: 'Needed when the file is moved after reading' })
  }
}

export const connectionCreateSchema = z.object({ name: z.string().trim().min(1, 'Required').max(100), ...fields }).superRefine(rules)
export const connectionUpdateSchema = z.object(fields).superRefine(rules)

const folderLabel: Record<(typeof FOLDERS)[number], string> = {
  inbound_folder: 'Inbound folder', outbound_folder: 'Outbound folder', processed_folder: 'Processed folder',
  error_folder: 'Error folder', test_inbound_folder: 'Test inbound folder', test_outbound_folder: 'Test outbound folder',
}

const sftp = (v: FieldValues) => v.kind === 'SFTP'
const shopify = (v: FieldValues) => v.kind === 'SHOPIFY'
const every = [5, 10, 15, 30, 60].map((n) => ({ value: String(n), label: `${n} min` }))

export const connectionFields: FieldSpec[] = [
  { name: 'name', label: 'Name', required: true, section: 'Basics', hint: 'Cannot be changed after the connection is created.' },
  { name: 'kind', label: 'Type', type: 'segmented', required: true, section: 'Basics', options: [{ value: 'SFTP', label: 'SFTP' }, { value: 'SHOPIFY', label: 'Shopify' }] },
  { name: 'mode', label: 'Mode', type: 'segmented', required: true, section: 'Basics', options: [{ value: 'TEST', label: 'Test' }, { value: 'LIVE', label: 'Live' }],
    hint: 'Test sends to the test folders and never moves or deletes a file. Live uses the real folders.' },
  { name: 'active', label: 'Active', type: 'checkbox', section: 'Basics' },

  { name: 'host', label: 'Address', section: 'Login', hint: "The server's address (a Shopify store: <name>.myshopify.com). An SFTP server must also be on the server's allowed list; ask your administrator." },
  // The server keeps a value for these on either kind: hidden, they go as they are today.
  { name: 'port', label: 'Port', type: 'number', section: 'Login', showWhen: sftp, alwaysSent: true },
  { name: 'username', label: 'User name', section: 'Login', showWhen: sftp },
  { name: 'secret_name', label: 'Password name on the server', section: 'Login',
    hint: 'The name your administrator gave the password on the server. The password itself is never typed here.' },
  { name: 'host_key', label: "Server's key", type: 'textarea', section: 'Login', showWhen: sftp, advanced: true,
    hint: 'Filled in by "Pin this key" after a test on the Overview.' },

  ...FOLDERS.map((f): FieldSpec => ({ name: f, label: folderLabel[f], section: 'Folders', showWhen: sftp, advanced: !['inbound_folder', 'outbound_folder'].includes(f) })),

  { name: 'after_read', label: 'After reading a file', type: 'segmented', required: true, section: 'Reading', showWhen: sftp, alwaysSent: true,
    options: [{ value: 'LEAVE', label: 'Leave the file' }, { value: 'MOVE', label: 'Move it' }, { value: 'DELETE', label: 'Delete it' }],
    hint: 'Move puts it in the processed folder. Moving and deleting happen only on a Live connection with writing to partners switched on; otherwise the file is left.' },
  { name: 'stable_seconds', label: 'Wait before reading (seconds)', type: 'number', required: true, section: 'Reading', showWhen: sftp, alwaysSent: true, advanced: true,
    hint: 'A file is read once its modified time is at least this old.' },

  { name: 'api_version', label: 'API version', placeholder: '2026-04', section: 'Shopify', showWhen: shopify },
  { name: 'partner_code', label: 'Partner', section: 'Shopify', showWhen: shopify, hint: "Whose orders the store's are." },
  { name: 'page_size', label: 'Orders per page', type: 'number', section: 'Shopify', showWhen: shopify, hint: 'At most 250 (default 50).' },
  { name: 'order_number_cutoff', label: 'Order number cut-off', type: 'number', section: 'Shopify', showWhen: shopify,
    hint: 'Orders numbered below it are historical and never sent.' },

  { name: 'schedule_minutes', label: 'Run every', type: 'presets', options: every, required: true, section: 'Schedule',
    hint: 'Each run reads the connection and takes what it stored through (850s, 997s, 945s, 944s). Custom: minutes.' },
  { name: 'active_days', label: 'Days', type: 'days', section: 'Schedule', hint: 'None chosen: every day.' },
  { name: 'active_from', label: 'From (Toronto time)', placeholder: '06:00', section: 'Schedule', hint: 'Empty: all day. An end before the start crosses midnight.' },
  { name: 'active_to', label: 'To (Toronto time)', placeholder: '22:00', section: 'Schedule' },
  { name: 'schedule_paused', label: 'Pause the schedule', type: 'checkbox', section: 'Schedule' },
  { name: 'schedule_sentence', label: 'In words', type: 'note', section: 'Schedule', note: scheduleSentence },
]

export const connectionEmptyValues: Record<string, unknown> = {
  name: '', kind: 'SFTP', host: '', port: 22, username: '', inbound_folder: '', outbound_folder: '', processed_folder: '',
  error_folder: '', test_inbound_folder: '', test_outbound_folder: '', after_read: 'LEAVE', stable_seconds: 60,
  mode: 'TEST', secret_name: '', host_key: '', api_version: '', page_size: null, order_number_cutoff: null, partner_code: '',
  schedule_minutes: 5, schedule_paused: false, active_from: '', active_to: '', active_days: [], active: true,
}

export function connectionToFormValues(c: ConnectionRead): Record<string, unknown> {
  return {
    ...Object.fromEntries(connectionFields.filter((f) => f.type !== 'note').map((f) => [f.name, c[f.name as keyof ConnectionRead]])),
    active_from: c.active_from?.slice(0, 5) ?? '', active_to: c.active_to?.slice(0, 5) ?? '',
    active_days: (c.active_days ?? '').split(',').filter(Boolean),
  }
}
