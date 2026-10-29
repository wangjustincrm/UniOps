import { describe, expect, it } from 'vitest'
import { partnerCreateSchema, partnerEmptyValues, partnerFields, partnerUpdateSchema } from './partnerSchema'

const valid = {
  ...partnerEmptyValues, code: 'WMT', name: 'Walmart Canada', channel: 'TRUECOMMERCE', settlement_mode: 'INVOICE',
  sending_mode: 'AUTOMATIC', delay_minutes: 0, invoice_method: '810_JSON', dc_routing_required: true,
}
const issues = (r: { success: boolean; error?: { issues: { path: PropertyKey[]; message: string }[] } }) =>
  Object.fromEntries((r.error?.issues ?? []).map((i) => [i.path.join('.'), i.message]))

describe('partner form schema', () => {
  it('accepts a complete partner (the positive half)', () => {
    expect(partnerCreateSchema.safeParse(valid).success).toBe(true)
  })

  it('mirrors the DTO: required fields, closed sets, lengths, counts', () => {
    const r = partnerCreateSchema.safeParse({
      ...valid, code: '  ', channel: null, delay_minutes: -1, abbrev: 'x'.repeat(51), invoice_email: 'nope',
    })
    expect(issues(r)).toEqual({
      code: 'Required', channel: 'Choose one', delay_minutes: 'Cannot be negative',
      abbrev: 'At most 50 characters', invoice_email: 'Not a valid email address',
    })
    expect(issues(partnerCreateSchema.safeParse({ ...valid, channel: 'FAX' }))).toEqual({ channel: 'Choose one' })
  })

  it('FR-OB-33: invoicing by email needs the address, on create and on edit', () => {
    const bad = { ...valid, invoice_method: 'EMAIL', invoice_email: '' }
    expect(issues(partnerCreateSchema.safeParse(bad))).toEqual({ invoice_email: 'Required when the invoice method is EMAIL' })
    expect(issues(partnerUpdateSchema.safeParse(bad))).toEqual({ invoice_email: 'Required when the invoice method is EMAIL' })
    expect(partnerCreateSchema.safeParse({ ...bad, invoice_email: 'ap@x.example' }).success).toBe(true)
  })

  it('the code is not part of an edit, and every schema field has an input', () => {
    expect(Object.keys(partnerUpdateSchema.parse(valid))).not.toContain('code')
    const names = new Set(partnerFields.map((f) => f.name))
    for (const k of Object.keys(partnerCreateSchema.parse(valid))) expect(names).toContain(k)
  })
})
