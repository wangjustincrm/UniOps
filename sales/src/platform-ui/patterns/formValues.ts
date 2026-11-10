// Values between a react-hook-form form, its FieldSpecs and the server
// (SchemaForm, MasterDetailForm).
import type { FieldValues, UseFormReturn } from 'react-hook-form'
import { businessErrorOf, plainFields } from '../resource/businessError'
import type { FieldSpec } from './SchemaForm'

/** A refusal from the server that names its field(s) goes next to the input:
 *  details.field, or details.errors [{field, message}]. True when it did. */
export function applyServerError(form: UseFormReturn<FieldValues>, fields: FieldSpec[], e: unknown,
  errorMessages?: Record<string, string>, fieldLabels?: Record<string, string>): boolean {
  const be = businessErrorOf(e)
  const known = (f: unknown): f is string => typeof f === 'string' && fields.some((x) => x.name === f)
  const listed = Array.isArray(be?.details?.errors)
    ? (be.details.errors as { field?: unknown; message?: unknown }[]).filter((x) => known(x.field))
    : []
  if (be && listed.length > 0) {
    for (const x of listed) form.setError(x.field as string, { message: plainFields(String(x.message ?? be.message), fieldLabels) })
    return true
  }
  if (be && known(be.details?.field)) {
    form.setError(be.details.field, { message: plainFields(errorMessages?.[be.code] ?? be.message, fieldLabels) })
    return true
  }
  return false
}



/** The fields not shown for these values (`showWhen`). */
export function notShown(fields: FieldSpec[], values: FieldValues): string[] {
  return fields.filter((f) => f.showWhen && !f.showWhen(values)).map((f) => f.name)
}

/** The fields that do not apply to these values: not validated, not sent (an `alwaysSent` one still is). */
export function hiddenFields(fields: FieldSpec[], values: FieldValues): string[] {
  const off = notShown(fields, values)
  return off.filter((n) => !fields.find((f) => f.name === n)?.alwaysSent)
}

/** A field not shown cannot be corrected: it is validated at its starting value, not at what was left in it. */
export function withDefaultsWhenHidden(values: FieldValues, fields: FieldSpec[], defaults: FieldValues): FieldValues {
  return { ...values, ...Object.fromEntries(notShown(fields, values).map((n) => [n, defaults[n]])) }
}

/** Before validation: an empty number or select is null, a number input's text a number. */
export function prepare(values: FieldValues, fields: FieldSpec[]): FieldValues {
  const out: FieldValues = { ...values }
  for (const f of fields) {
    const v = out[f.name]
    if (f.type === 'number' || f.type === 'presets') {
      out[f.name] = v === '' || v === null || v === undefined ? null : typeof v === 'string' ? Number(v) : v
    } else if (f.type === 'select' || f.type === 'segmented' || f.type === 'reference' || f.type === 'date') {
      out[f.name] = v === '' || v === undefined || v === null ? null : f.numeric && typeof v === 'string' ? Number(v) : v
    }
  }
  return out
}

/** The starting values as the inputs hold them: blank for none, a numeric choice as its option's text. */
export function toInputs(v: Record<string, unknown>, fields: FieldSpec[]): Record<string, unknown> {
  const out = blankToEmpty(v)
  for (const f of fields) if (f.numeric && typeof out[f.name] === 'number') out[f.name] = String(out[f.name])
  return out
}

export function blankToEmpty(v: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, x === null || x === undefined ? '' : x]))
}

export function emptyToNull(v: unknown): unknown {
  return v === '' ? null : v
}
