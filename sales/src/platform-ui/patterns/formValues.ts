// Values between a react-hook-form form, its FieldSpecs and the server
// (SchemaForm, MasterDetailForm).
import type { FieldValues, UseFormReturn } from 'react-hook-form'
import { businessErrorOf } from '../resource/businessError'
import type { FieldSpec } from './SchemaForm'

/** A refusal from the server that names its field(s) goes next to the input:
 *  details.field, or details.errors [{field, message}]. True when it did. */
export function applyServerError(form: UseFormReturn<FieldValues>, fields: FieldSpec[], e: unknown,
  errorMessages?: Record<string, string>): boolean {
  const be = businessErrorOf(e)
  const known = (f: unknown): f is string => typeof f === 'string' && fields.some((x) => x.name === f)
  const listed = Array.isArray(be?.details?.errors)
    ? (be.details.errors as { field?: unknown; message?: unknown }[]).filter((x) => known(x.field))
    : []
  if (be && listed.length > 0) {
    for (const x of listed) form.setError(x.field as string, { message: String(x.message ?? be.message) })
    return true
  }
  if (be && known(be.details?.field)) {
    form.setError(be.details.field, { message: errorMessages?.[be.code] ?? be.message })
    return true
  }
  return false
}



/** Before validation: an empty number or select is null, a number input's text a number. */
export function prepare(values: FieldValues, fields: FieldSpec[]): FieldValues {
  const out: FieldValues = { ...values }
  for (const f of fields) {
    const v = out[f.name]
    if (f.type === 'number') {
      out[f.name] = v === '' || v === null || v === undefined ? null : typeof v === 'string' ? Number(v) : v
    } else if (f.type === 'select' || f.type === 'reference' || f.type === 'date') {
      out[f.name] = v === '' || v === undefined ? null : v
    }
  }
  return out
}

export function blankToEmpty(v: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, x === null || x === undefined ? '' : x]))
}

export function emptyToNull(v: unknown): unknown {
  return v === '' ? null : v
}
