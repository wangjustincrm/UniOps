// An address entered by hand (a manual order's ship-to; later a partner's
// default): the provinces, the zod schema — the same rules as the server's
// AddressIn — and the field names (for server errors).
import { z } from 'zod'

export const CA_PROVINCES = [
  ['AB', 'Alberta'], ['BC', 'British Columbia'], ['MB', 'Manitoba'], ['NB', 'New Brunswick'], ['NL', 'Newfoundland and Labrador'],
  ['NS', 'Nova Scotia'], ['NT', 'Northwest Territories'], ['NU', 'Nunavut'], ['ON', 'Ontario'], ['PE', 'Prince Edward Island'],
  ['QC', 'Quebec'], ['SK', 'Saskatchewan'], ['YT', 'Yukon'],
].map(([value, label]) => ({ value, label: `${label} (${value})` }))
export const COUNTRIES = [{ value: 'CA', label: 'Canada' }, { value: 'US', label: 'United States' }]
export const ADDRESS_FIELDS = ['country', 'name', 'attention', 'phone', 'address1', 'address2', 'city', 'province', 'zip'] as const

const CA_POSTAL = /^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z] ?\d[ABCEGHJ-NPRSTV-Z]\d$/i
const text = (max: number) => z.string().trim().max(max)

/** Required: what a warehouse shipment needs — name, address1, city, province, postal code. */
export function addressSchema() {
  return z.object({
    country: z.string().trim().length(2, 'Choose the country'),
    name: text(255).min(1, 'Required'),
    attention: text(255).nullable().optional(),
    phone: text(40).nullable().optional(),
    address1: text(255).min(1, 'Required'),
    address2: text(255).nullable().optional(),
    city: text(100).min(1, 'Required'),
    province: text(10).min(1, 'Required'),
    zip: text(20).min(1, 'Required'),
  }).superRefine((a, ctx) => {
    if (a.country !== 'CA') return
    if (!CA_PROVINCES.some((p) => p.value === a.province.toUpperCase())) {
      ctx.addIssue({ code: 'custom', path: ['province'], message: 'Choose the province' })
    }
    if (a.zip && !CA_POSTAL.test(a.zip)) ctx.addIssue({ code: 'custom', path: ['zip'], message: 'A Canadian postal code: A1A 1A1' })
  })
}

export type AddressValue = Partial<Record<(typeof ADDRESS_FIELDS)[number], string | null>> & { gln?: string | null }

/** Without empty parts (an empty optional field is left out, not sent as ""). */
export function cleanAddress(a: Record<string, unknown> | null | undefined): Record<string, string> {
  return Object.fromEntries(Object.entries(a ?? {}).filter(([, v]) => typeof v === 'string' && v.trim() !== '')
    .map(([k, v]) => [k, (v as string).trim()]))
}
