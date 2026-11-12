import { z } from 'zod'
import type { FieldValues } from 'react-hook-form'
import type { FieldSpec } from '@/platform-ui'
import { AFTER_READ, DAYS, KINDS, READ_FOLDERS, WRITE_FOLDERS, scheduleSentence, type ConnectionRead } from './connectionsApi'

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
  after_read: choice(AFTER_READ),
  stable_seconds: z.number({ error: 'Enter seconds' }).int('Whole seconds').min(0, 'Cannot be negative'),
  can_read: z.boolean(),
  can_write: z.boolean(),
  secret_name: z.string().trim().max(100).refine((v) => v === '' || SECRET.test(v), SECRET_NAME_MESSAGE).nullable().optional(),
  host_key: z.string().trim().refine((v) => v === '' || KEY_LINE.test(v), HOST_KEY_MESSAGE).nullable().optional(),
  api_version: z.string().trim().refine((v) => v === '' || /^\d{4}-\d{2}$/.test(v), 'A version like 2026-04').nullable().optional(),
  page_size: z.number().int().min(1, 'From 1').max(250, 'At most 250').nullable().optional(),
  order_number_cutoff: z.number().int().min(0).nullable().optional(),
  partner_code: optional(50),
  active: z.boolean(),
}
// The schedule (user 2026-11-02): times as HH:MM (Toronto), days as a list — sent as "06:00:00" and "Mon,Fri".
const schedule = {
  schedule_minutes: z.number({ error: 'Enter minutes' }).int('Whole minutes').min(1, 'At least 1').max(1440, 'At most 1440'),
  schedule_paused: z.boolean(),
  active_from: clock, active_to: clock,
  active_days: z.array(z.enum(DAYS)).transform((d) => (d.length ? DAYS.filter((x) => d.includes(x)).join(',') : null)),
}

type Values = { kind?: string; after_read?: string; processed_folder?: string | null; host_key?: string | null; host?: string | null; partner_code?: string | null; active_from?: string | null; active_to?: string | null; can_read?: boolean; can_write?: boolean } & Record<string, unknown>
const SFTP_REQUIRED = ['host', 'username', 'secret_name'] as const
const folderOf = (v: unknown) => '/' + String(v ?? '').trim().replace(/^\/+|\/+$/g, '')

function scheduleRules(v: Values, ctx: z.RefinementCtx) {
  if (!String(v.active_from ?? '').trim() !== !String(v.active_to ?? '').trim()) {
    const missing = String(v.active_from ?? '').trim() ? 'active_to' : 'active_from'
    ctx.addIssue({ code: 'custom', path: [missing], message: 'Give both the start and the end, or neither' })
  }
}

function rules(v: Values, ctx: z.RefinementCtx) {
  if (v.kind === 'SFTP') {
    for (const f of SFTP_REQUIRED) {
      if (!String(v[f] ?? '').trim()) ctx.addIssue({ code: 'custom', path: [f], message: 'Required for an SFTP connection' })
    }
    if (v.can_read && !String(v.inbound_folder ?? '').trim()) ctx.addIssue({ code: 'custom', path: ['inbound_folder'], message: 'Needed to read' })
    if (v.can_write && !String(v.outbound_folder ?? '').trim()) ctx.addIssue({ code: 'custom', path: ['outbound_folder'], message: 'Needed to write' })
    if (String(v.outbound_folder ?? '').trim()) {
      for (const f of READ_FOLDERS) {
        if (String(v[f] ?? '').trim() && folderOf(v[f]) === folderOf(v.outbound_folder)) {
          ctx.addIssue({ code: 'custom', path: [f], message: 'Must not be the write folder' })
        }
      }
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
  scheduleRules(v, ctx)
  if (v.after_read === 'MOVE' && !String(v.processed_folder ?? '').trim()) {
    ctx.addIssue({ code: 'custom', path: ['processed_folder'], message: 'Needed when the file is moved after reading' })
  }
}

export const connectionCreateSchema = z.object({ name: z.string().trim().min(1, 'Required').max(100), ...fields, ...schedule }).superRefine(rules)
export const connectionUpdateSchema = z.object(fields).superRefine(rules)
/** The Schedule tab's form. */
export const connectionScheduleSchema = z.object(schedule).superRefine(scheduleRules)

const folderLabel: Record<(typeof READ_FOLDERS)[number] | (typeof WRITE_FOLDERS)[number], string> = {
  inbound_folder: 'Read folder', processed_folder: 'Archive folder', error_folder: 'Error folder', outbound_folder: 'Write folder',
}
const folderHint: Record<string, string> = {
  inbound_folder: 'Where Sales reads the files the partner leaves.',
  processed_folder: 'Where a read file is moved when "After reading" is Move.',
  error_folder: 'Where a file that cannot be read is moved.',
  outbound_folder: 'Where Sales puts the files it sends (940, 943, 810).',
}

const sftp = (v: FieldValues) => v.kind === 'SFTP'
const shopify = (v: FieldValues) => v.kind === 'SHOPIFY'
const every = [5, 10, 15, 30, 60].map((n) => ({ value: String(n), label: `${n} min` }))

/** A connection's schedule: edited on its Schedule tab, and in the New form (`connectionFields`). */
const scheduleSpecs: FieldSpec[] = [
  { name: 'schedule_minutes', label: 'Run every', type: 'presets', options: every, required: true,
    hint: 'Each run reads the connection and takes what it stored through (850s, 997s, 945s, 944s). Custom: minutes.' },
  { name: 'active_days', label: 'Days', type: 'days', hint: 'None chosen: every day.' },
  { name: 'active_from', label: 'From (Toronto time)', placeholder: '06:00', hint: 'Empty: all day. An end before the start crosses midnight.' },
  { name: 'active_to', label: 'To (Toronto time)', placeholder: '22:00' },
  { name: 'schedule_paused', label: 'Pause the schedule', type: 'checkbox' },
  { name: 'schedule_sentence', label: 'In words', type: 'note', note: scheduleSentence },
]
export const scheduleFields: FieldSpec[] = scheduleSpecs.map((f) => ({ ...f, section: 'Schedule' }))

export const connectionFields: FieldSpec[] = [
  { name: 'name', label: 'Name', required: true, section: 'Basics', hint: 'Cannot be changed after the connection is created.' },
  { name: 'kind', label: 'Type', type: 'segmented', required: true, section: 'Basics', options: [{ value: 'SFTP', label: 'SFTP' }, { value: 'SHOPIFY', label: 'Shopify' }] },
  { name: 'active', label: 'Active', type: 'checkbox', section: 'Basics' },

  { name: 'can_read', label: 'Read', type: 'checkbox', section: 'Reading',
    hint: (v) => (v.kind === 'SHOPIFY' ? 'Sales pulls the store\'s orders.' : 'Sales lists and reads the read folder.') },
  ...READ_FOLDERS.map((f): FieldSpec => ({ name: f, label: folderLabel[f], section: 'Reading', showWhen: sftp, hint: folderHint[f] })),
  { name: 'after_read', label: 'After reading a file', type: 'segmented', required: true, section: 'Reading', showWhen: sftp, alwaysSent: true,
    options: [{ value: 'LEAVE', label: 'Leave the file' }, { value: 'MOVE', label: 'Move it' }, { value: 'DELETE', label: 'Delete it' }],
    hint: 'Move puts it in the archive folder. Moving and deleting change the partner\'s server: they happen only with Write on; otherwise the file is left.' },
  { name: 'stable_seconds', label: 'Wait before reading (seconds)', type: 'number', required: true, section: 'Reading', showWhen: sftp, alwaysSent: true, advanced: true,
    hint: 'A file is read once its modified time is at least this old.' },

  { name: 'can_write', label: 'Write', type: 'checkbox', section: 'Writing',
    hint: (v) => (v.kind === 'SHOPIFY'
      ? 'Sales sends fulfilments and tracking to the store and closes orders. Turned on only when the go-live notifications are set (Health).'
      : 'Sales puts its files in the write folder and moves or deletes what it read. Turned on only when the go-live notifications are set (Health); the server must also allow writing to this address.') },
  ...WRITE_FOLDERS.map((f): FieldSpec => ({ name: f, label: folderLabel[f], section: 'Writing', showWhen: sftp, hint: folderHint[f] })),

  { name: 'api_version', label: 'API version', placeholder: '2026-04', section: 'Shopify', showWhen: shopify },
  { name: 'partner_code', label: 'Partner', section: 'Shopify', showWhen: shopify, hint: "Whose orders the store's are." },
  { name: 'page_size', label: 'Orders per page', type: 'number', section: 'Shopify', showWhen: shopify, hint: 'At most 250 (default 50).' },
  { name: 'order_number_cutoff', label: 'Order number cut-off', type: 'number', section: 'Shopify', showWhen: shopify,
    hint: 'Orders numbered below it are historical and never sent.' },

  // Login comes after what the connection does: it is set once.
  { name: 'host', label: 'Address', section: 'Login', hint: "The server's address (a Shopify store: <name>.myshopify.com). An SFTP server must also be on the server's allowed list; ask your administrator." },
  // The server keeps a value for these on either kind: hidden, they go as they are today.
  { name: 'port', label: 'Port', type: 'number', section: 'Login', showWhen: sftp, alwaysSent: true },
  { name: 'username', label: 'User name', section: 'Login', showWhen: sftp },
  { name: 'secret_name', label: 'Password name on the server', section: 'Login',
    hint: 'The name your administrator gave the password on the server. The password itself is never typed here.' },
  { name: 'host_key', label: "Server's key", type: 'textarea', section: 'Login', showWhen: sftp, advanced: true,
    hint: 'Filled in by "Pin this key" after a test on the Overview.' },

  ...scheduleFields,
]

/** The Settings tab: everything but the schedule, which has its own tab. */
export const connectionSettingsFields = connectionFields.filter((f) => !scheduleFields.includes(f))

/** The server's field names, in the form's words (its refusals and CSV rows name them). */
export const connectionFieldLabels: Record<string, string> = Object.fromEntries(
  connectionFields.filter((f) => ['host_key', 'secret_name'].includes(f.name)).map((f) => [f.name, f.label]))

export const connectionEmptyValues: Record<string, unknown> = {
  name: '', kind: 'SFTP', host: '', port: 22, username: '', inbound_folder: '', outbound_folder: '', processed_folder: '',
  error_folder: '', after_read: 'LEAVE', stable_seconds: 60,
  can_read: true, can_write: false, secret_name: '', host_key: '', api_version: '', page_size: null, order_number_cutoff: null, partner_code: '',
  schedule_minutes: 5, schedule_paused: false, active_from: '', active_to: '', active_days: [], active: true,
}

export function connectionToFormValues(c: ConnectionRead): Record<string, unknown> {
  return {
    ...Object.fromEntries(connectionFields.filter((f) => f.type !== 'note').map((f) => [f.name, c[f.name as keyof ConnectionRead]])),
    active_from: c.active_from?.slice(0, 5) ?? '', active_to: c.active_to?.slice(0, 5) ?? '',
    active_days: (c.active_days ?? '').split(',').filter(Boolean),
  }
}
