import { z } from 'zod'
import type { FieldSpec } from '@/platform-ui'
import { CHANNELS, INVOICE_METHODS, SENDING_MODES, SETTLEMENT_MODES, type PartnerRead } from './partnersApi'

// The form's checks mirror the API's DTO (lengths, closed sets, counts ≥ 0)
// and FR-OB-33; the server checks them again and names the field it refuses.
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const required = (max: number) => z.string().trim().min(1, 'Required').max(max, `At most ${max} characters`)
const optional = (max?: number) => {
  const s = z.string().trim()
  return (max ? s.max(max, `At most ${max} characters`) : s).nullable().optional()
}
const email = z.string().trim().max(255, 'At most 255 characters')
  .refine((v) => v === '' || EMAIL.test(v), 'Not a valid email address').nullable().optional()
const choice = <T extends readonly [string, ...string[]]>(values: T) => z.enum(values, { error: 'Choose one' })
const count = z.number({ error: 'Enter a whole number' }).int('Enter a whole number').min(0, 'Cannot be negative')

const fields = {
  name: required(255),
  abbrev: optional(50),
  channel: choice(CHANNELS),
  settlement_mode: choice(SETTLEMENT_MODES),
  sending_mode: choice(SENDING_MODES),
  delay_minutes: count,
  invoice_method: choice(INVOICE_METHODS),
  invoice_email: email,
  invoice_cc: optional(),
  payment_terms: optional(100),
  dc_routing_required: z.boolean(),
  min_shelf_life_days: count.nullable().optional(),
  label_code: optional(50),
  active: z.boolean(),
}

// FR-OB-33: invoicing by email needs the address.
const emailInvoicing = (v: { invoice_method?: string; invoice_email?: string | null }, ctx: z.RefinementCtx) => {
  if (v.invoice_method === 'EMAIL' && !v.invoice_email?.trim()) {
    ctx.addIssue({ code: 'custom', path: ['invoice_email'], message: 'Required when the invoice method is EMAIL' })
  }
}

export const partnerCreateSchema = z.object({ code: required(50), ...fields }).superRefine(emailInvoicing)
export const partnerUpdateSchema = z.object(fields).superRefine(emailInvoicing)

const options = (values: readonly string[]) => values.map((v) => ({ value: v, label: v }))

export const partnerFields: FieldSpec[] = [
  { name: 'code', label: 'Code', required: true, hint: 'Cannot be changed after the partner is created.' },
  { name: 'name', label: 'Name', required: true },
  { name: 'abbrev', label: 'Abbreviation' },
  { name: 'channel', label: 'Channel', type: 'select', options: options(CHANNELS), required: true },
  { name: 'settlement_mode', label: 'Settlement mode', type: 'select', options: options(SETTLEMENT_MODES), required: true,
    hint: 'PREPAID: payment before a 940 is sent. INVOICE: billed after delivery.' },
  { name: 'sending_mode', label: 'Sending mode', type: 'select', options: options(SENDING_MODES), required: true },
  { name: 'delay_minutes', label: 'Delay (minutes)', type: 'number', required: true },
  { name: 'invoice_method', label: 'Invoice method', type: 'select', options: options(INVOICE_METHODS), required: true },
  { name: 'invoice_email', label: 'Invoice email', type: 'email', hint: 'Required when the invoice method is EMAIL.' },
  { name: 'invoice_cc', label: 'Invoice CC' },
  { name: 'payment_terms', label: 'Payment terms' },
  { name: 'dc_routing_required', label: 'DC routing required', type: 'checkbox',
    hint: 'An order with no DC match goes to review.' },
  { name: 'min_shelf_life_days', label: 'Minimum shelf life (days)', type: 'number', hint: 'Empty: no check.' },
  { name: 'label_code', label: 'Label code' },
  { name: 'active', label: 'Active', type: 'checkbox' },
]

export const partnerEmptyValues: Record<string, unknown> = {
  code: '', name: '', abbrev: '', channel: '', settlement_mode: '', sending_mode: '', delay_minutes: 0,
  invoice_method: '', invoice_email: '', invoice_cc: '', payment_terms: '', dc_routing_required: false,
  min_shelf_life_days: null, label_code: '', active: true,
}

/** The form's values for a partner: exactly the fields the form shows. */
export function partnerToFormValues(p: PartnerRead): Record<string, unknown> {
  return Object.fromEntries(partnerFields.map((f) => [f.name, p[f.name as keyof PartnerRead]]))
}
