import { useMemo, useState } from 'react'
import { useForm, type FieldValues, type Resolver } from 'react-hook-form'
import type { z } from 'zod'
import { Button, FormField, Input } from '../primitives/shell'
import { businessErrorOf } from '../resource/businessError'
import { ErrorMessage } from './ErrorMessage'
import { zodResolver } from './zodResolver'

export type FieldType = 'text' | 'email' | 'number' | 'textarea' | 'checkbox' | 'select'

export interface FieldSpec {
  name: string
  label: string
  type?: FieldType
  options?: { value: string; label: string }[]
  required?: boolean
  hint?: string
  placeholder?: string
  /** Shown but not editable (e.g. a code that is immutable after creation). */
  readOnly?: boolean
}

export interface SchemaFormProps {
  schema: z.ZodType<FieldValues, FieldValues>
  fields: FieldSpec[]
  defaultValues: Record<string, unknown>
  /** With `changesOnly`, `values` holds only the fields the user changed, so
   *  an update never touches the rest; a cleared optional field is sent as
   *  null (which clears it on the server). */
  onSubmit: (values: Record<string, unknown>) => unknown | Promise<unknown>
  changesOnly?: boolean
  submitLabel?: string
  onCancel?: () => void
  errorMessages?: Record<string, string>
}

/** A config form generated from a zod schema and a field list. Config forms
 *  only — order screens are hand-built.
 *
 *  Before validation, an empty number or select is null (a cleared optional
 *  number is null, never 0; a blank optional select is accepted) and a number
 *  input's text becomes a number. */
export function SchemaForm({
  schema, fields, defaultValues, onSubmit, changesOnly, submitLabel = 'Save', onCancel, errorMessages,
}: SchemaFormProps) {
  const resolver = useMemo<Resolver<FieldValues>>(() => {
    const inner = zodResolver(schema)
    return (values, ctx, opts) => inner(prepare(values, fields), ctx, opts)
  }, [schema, fields])
  const form = useForm<FieldValues>({
    resolver,
    defaultValues: blankToEmpty(defaultValues),
  })
  const [error, setError] = useState<unknown>(null)
  const { errors, isSubmitting, dirtyFields } = form.formState

  const submit = form.handleSubmit(async (values) => {
    setError(null)
    const out = changesOnly
      ? Object.fromEntries(Object.keys(dirtyFields).map((k) => [k, emptyToNull(values[k])]))
      : Object.fromEntries(Object.entries(values).map(([k, v]) => [k, emptyToNull(v)]))
    try {
      await onSubmit(out)
    } catch (e) {
      const be = businessErrorOf(e)
      const field = be?.details?.field
      if (be && typeof field === 'string' && fields.some((f) => f.name === field)) {
        form.setError(field, { message: errorMessages?.[be.code] ?? be.message })
      } else {
        setError(e)
      }
    }
  })

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      {fields.map((f) => {
        const id = `sf-${f.name}`
        const err = errors[f.name]?.message
        const common = { id, disabled: f.readOnly, 'aria-invalid': err ? true : undefined }
        let control
        if (f.type === 'checkbox') {
          control = <input type="checkbox" {...common} {...form.register(f.name)} className="h-4 w-4" />
        } else if (f.type === 'select') {
          control = (
            <select {...common} {...form.register(f.name)} className="h-10 rounded-lg border border-neutral-200 bg-white px-3 text-sm">
              <option value="">—</option>
              {f.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          )
        } else if (f.type === 'textarea') {
          control = <textarea {...common} {...form.register(f.name)} rows={4} placeholder={f.placeholder} className="rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
        } else {
          control = (
            <Input
              {...common}
              type={f.type === 'number' ? 'number' : f.type === 'email' ? 'email' : 'text'}
              step={f.type === 'number' ? 'any' : undefined}
              placeholder={f.placeholder}
              {...form.register(f.name)}
            />
          )
        }
        return (
          <FormField key={f.name} label={f.label} htmlFor={id} required={f.required} hint={f.hint} error={typeof err === 'string' ? err : undefined}>
            {control}
          </FormField>
        )
      })}
      {typeof errors.root?.message === 'string' && (
        <p role="alert" className="text-sm text-danger-600">{errors.root.message}</p>
      )}
      <ErrorMessage error={error} messages={errorMessages} />
      <div className="flex justify-end gap-2">
        {onCancel && <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>Cancel</Button>}
        <Button type="submit" disabled={isSubmitting}>{isSubmitting ? 'Saving…' : submitLabel}</Button>
      </div>
    </form>
  )
}

function prepare(values: FieldValues, fields: FieldSpec[]): FieldValues {
  const out: FieldValues = { ...values }
  for (const f of fields) {
    const v = out[f.name]
    if (f.type === 'number') {
      out[f.name] = v === '' || v === null || v === undefined ? null : typeof v === 'string' ? Number(v) : v
    } else if (f.type === 'select') {
      out[f.name] = v === '' || v === undefined ? null : v
    }
  }
  return out
}

function blankToEmpty(v: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, x === null || x === undefined ? '' : x]))
}

function emptyToNull(v: unknown): unknown {
  return v === '' ? null : v
}
