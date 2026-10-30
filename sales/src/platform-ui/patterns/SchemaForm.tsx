import { useMemo, useState } from 'react'
import { Controller, useForm, type FieldValues, type Resolver } from 'react-hook-form'
import type { z } from 'zod'
import { Autocomplete } from '../primitives/Autocomplete'
import { Checkbox } from '../primitives/Checkbox'
import type { ComboOption } from '../primitives/Combobox'
import { DateInput, NumberInput, Textarea } from '../primitives/FieldInputs'
import { Select } from '../primitives/Select'
import { Button, FormField, Input } from '../primitives/shell'
import { businessErrorOf } from '../resource/businessError'
import { ErrorMessage } from './ErrorMessage'
import { zodResolver } from './zodResolver'

// Each type has its control: select → Select, checkbox → Checkbox, number →
// NumberInput, date → DateInput ("YYYY-MM-DD", a string), textarea →
// Textarea, reference → Autocomplete over `loadOptions` (a code of another
// record, e.g. an item or a partner).
export type FieldType = 'text' | 'email' | 'number' | 'textarea' | 'checkbox' | 'select' | 'date' | 'reference'

export interface FieldSpec {
  name: string
  label: string
  type?: FieldType
  options?: { value: string; label: string }[]
  /** reference fields: the options for what was typed. */
  loadOptions?: (query: string) => Promise<ComboOption[]>
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
  /** View only: every field disabled, no submit (a reader's view of a record). */
  readOnly?: boolean
}

/** A config form generated from a zod schema and a field list. Config forms
 *  only — order screens are hand-built.
 *
 *  Before validation, an empty number or select is null (a cleared optional
 *  number is null, never 0; a blank optional select is accepted) and a number
 *  input's text becomes a number. */
export function SchemaForm({
  schema, fields, defaultValues, onSubmit, changesOnly, submitLabel = 'Save', onCancel, errorMessages, readOnly,
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
      // A refusal names its field (details.field), or several
      // (details.errors: [{field, message}] for a request the server's
      // schema refused); each message goes next to its input.
      const be = businessErrorOf(e)
      const known = (f: unknown): f is string => typeof f === 'string' && fields.some((x) => x.name === f)
      const listed = Array.isArray(be?.details?.errors)
        ? (be.details.errors as { field?: unknown; message?: unknown }[]).filter((x) => known(x.field))
        : []
      if (be && listed.length > 0) {
        for (const x of listed) form.setError(x.field as string, { message: String(x.message ?? be.message) })
      } else if (be && known(be.details?.field)) {
        form.setError(be.details.field, { message: errorMessages?.[be.code] ?? be.message })
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
        const common = { id, disabled: readOnly || f.readOnly, 'aria-invalid': err ? true : undefined }
        let control
        if (f.type === 'checkbox') {
          control = (
            <Controller control={form.control} name={f.name} render={({ field }) => (
              <Checkbox id={id} checked={!!field.value} onCheckedChange={field.onChange} onBlur={field.onBlur}
                disabled={common.disabled} aria-invalid={common['aria-invalid']} />
            )} />
          )
        } else if (f.type === 'select') {
          control = (
            <Controller control={form.control} name={f.name} render={({ field }) => (
              <Select id={id} value={field.value || null} onChange={(v) => field.onChange(v ?? '')} onBlur={field.onBlur}
                options={f.options ?? []} emptyLabel="—" placeholder="—" disabled={common.disabled}
                aria-invalid={common['aria-invalid']} />
            )} />
          )
        } else if (f.type === 'reference') {
          control = (
            <Controller control={form.control} name={f.name} render={({ field }) => (
              <Autocomplete id={id} value={field.value || null} onChange={(v) => field.onChange(v ?? '')} onBlur={field.onBlur}
                loadOptions={f.loadOptions ?? (async () => [])} placeholder={f.placeholder} disabled={common.disabled}
                aria-label={f.label} aria-invalid={common['aria-invalid']} />
            )} />
          )
        } else if (f.type === 'textarea') {
          control = <Textarea {...common} {...form.register(f.name)} placeholder={f.placeholder} />
        } else if (f.type === 'number') {
          control = <NumberInput {...common} placeholder={f.placeholder} {...form.register(f.name)} />
        } else if (f.type === 'date') {
          control = <DateInput {...common} {...form.register(f.name)} />
        } else if (f.type === 'email') {
          control = <Input {...common} type="email" placeholder={f.placeholder} {...form.register(f.name)} />
        } else {
          control = <Input {...common} placeholder={f.placeholder} {...form.register(f.name)} />
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
        {onCancel && (
          <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>{readOnly ? 'Close' : 'Cancel'}</Button>
        )}
        {!readOnly && <Button type="submit" disabled={isSubmitting}>{isSubmitting ? 'Saving…' : submitLabel}</Button>}
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
    } else if (f.type === 'select' || f.type === 'reference' || f.type === 'date') {
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
