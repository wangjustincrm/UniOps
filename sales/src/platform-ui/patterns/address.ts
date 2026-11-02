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

// The province(s) a Canadian postal code belongs to, by its first letter — the server's table.
export const POSTAL_PROVINCES: Record<string, string[]> = {
  A: ['NL'], B: ['NS'], C: ['PE'], E: ['NB'], G: ['QC'], H: ['QC'], J: ['QC'], K: ['ON'], L: ['ON'], M: ['ON'], N: ['ON'],
  P: ['ON'], R: ['MB'], S: ['SK'], T: ['AB'], V: ['BC'], X: ['NT', 'NU'], Y: ['YT'],
}
/** Why the postal code is not in the province, or null. */
export function postalMismatch(province: string | null | undefined, zip: string | null | undefined): string | null {
  const p = (province ?? '').trim().toUpperCase()
  const zc = (zip ?? '').trim().toUpperCase()
  const allowed = zc ? POSTAL_PROVINCES[zc[0]] : undefined
  return !p || !allowed || allowed.includes(p) ? null : `This postal code is in ${allowed.join('/')}, not ${p}`
}

// "PO Box 12", "P.O. Box", "Post Office Box", "CP 34" / "C.P. 34", "Case postale", "Box 56" — the server's pattern.
const PO_BOX = /\b(?:p\.?\s*o\.?\s*box|post\s+office\s+box|case\s+postale|c\.?\s*p\.?\s+\d+|box\s*#?\s*\d+)/i
export const isPoBox = (...lines: (string | null | undefined)[]) => lines.some((l) => PO_BOX.test(l ?? ''))

/** Required: what a warehouse shipment needs — name, address1, city, province,
 *  postal code (in that province, in Canada), and the phone with `phone`. A PO
 *  box is not refused: it must be confirmed (`po_box_ok`, never sent). */
export function addressSchema({ phone = false }: { phone?: boolean } = {}) {
  return z.object({
    country: z.string().trim().length(2, 'Choose the country'),
    name: text(255).min(1, 'Required'),
    attention: text(255).nullable().optional(),
    phone: phone ? text(40).min(1, 'Required') : text(40).nullable().optional(),
    address1: text(255).min(1, 'Required'),
    address2: text(255).nullable().optional(),
    city: text(100).min(1, 'Required'),
    province: text(10).min(1, 'Required'),
    zip: text(20).min(1, 'Required'),
    po_box_ok: z.boolean().optional(),
  }).superRefine((a, ctx) => {
    if (isPoBox(a.address1, a.address2) && !a.po_box_ok) {
      ctx.addIssue({ code: 'custom', path: ['po_box_ok'], message: 'Confirm the PO box address' })
    }
    if (a.country !== 'CA') return
    if (!CA_PROVINCES.some((p) => p.value === a.province.toUpperCase())) {
      ctx.addIssue({ code: 'custom', path: ['province'], message: 'Choose the province' })
    }
    if (a.zip && !CA_POSTAL.test(a.zip)) ctx.addIssue({ code: 'custom', path: ['zip'], message: 'A Canadian postal code: A1A 1A1' })
    else if (postalMismatch(a.province, a.zip)) ctx.addIssue({ code: 'custom', path: ['zip'], message: postalMismatch(a.province, a.zip)! })
  })
}

export type AddressValue = Partial<Record<(typeof ADDRESS_FIELDS)[number], string | null>> & { gln?: string | null }

/** Without empty parts (an empty optional field is left out, not sent as ""). */
export function cleanAddress(a: Record<string, unknown> | null | undefined): Record<string, string> {
  return Object.fromEntries(Object.entries(a ?? {}).filter(([, v]) => typeof v === 'string' && v.trim() !== '')
    .map(([k, v]) => [k, (v as string).trim()]))
}
