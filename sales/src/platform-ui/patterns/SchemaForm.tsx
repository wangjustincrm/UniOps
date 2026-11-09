import { useReportUnsaved } from '../primitives/unsaved'
import { useMemo, useState, type ReactNode } from 'react'
import { Controller, useForm, useWatch, type FieldValues, type Resolver, type UseFormReturn } from 'react-hook-form'
import type { z } from 'zod'
import { Autocomplete } from '../primitives/Autocomplete'
import { Checkbox } from '../primitives/Checkbox'
import { MultiChoice } from '../primitives/MultiChoice'
import { DayChips, PresetNumber, Segmented } from '../primitives/ChoiceControls'
import type { ComboOption } from '../primitives/Combobox'
import { DateInput, NumberInput, Textarea } from '../primitives/FieldInputs'
import { Select } from '../primitives/Select'
import { TagInput } from '../primitives/TagInput'
import { Button, FormField, Input } from '../primitives/shell'
import { ErrorMessage } from './ErrorMessage'
import { applyServerError, blankToEmpty, emptyToNull, hiddenFields, notShown, prepare, withDefaultsWhenHidden } from './formValues'
import { Section } from './Section'
import { zodResolver } from './zodResolver'

// Each type has its control: select → Select, checkbox → Checkbox, number →
// NumberInput, date → DateInput ("YYYY-MM-DD", a string), textarea →
// Textarea, reference → Autocomplete over `loadOptions` (a code of another
// record, e.g. an item or a partner), multiselect → CheckboxList over
// `options` (an array of values), segmented → Segmented over a few short
// `options`, days → DayChips (an array of values; `options` default to the week),
// presets → PresetNumber (a number: a few usual `options`, or Custom), note → a
// line computed from the values (`note`; no value of its own), tags → TagInput (an array of
// typed values, e.g. addresses; what is typed and not yet added is added when the box loses focus).
export type FieldType = 'text' | 'email' | 'number' | 'textarea' | 'checkbox' | 'select' | 'date' | 'reference' | 'multiselect' | 'segmented' | 'days'
  | 'presets' | 'note' | 'tags'

export interface FieldSpec {
  name: string
  label: string
  type?: FieldType
  options?: { value: string; label: string }[]
  /** reference fields: the options for what was typed. */
  loadOptions?: (query: string) => Promise<ComboOption[]>
  required?: boolean
  /** Under the field; a function when it depends on the values. */
  hint?: string | ((values: FieldValues) => string)
  placeholder?: string
  /** Shown but not editable (e.g. a code that is immutable after creation). */
  readOnly?: boolean
  /** The titled group it is in (in the order the groups first appear). */
  section?: string
  /** In the collapsed "Advanced" group of its section (it opens by itself on an error). */
  advanced?: boolean
  /** Whether it applies to these values. When not, it is neither shown nor
   *  validated, and its value is not sent; what was typed comes back if it
   *  applies again. */
  showWhen?: (values: FieldValues) => boolean
  /** With `showWhen`: a value the server needs whether or not it is shown, sent at its starting value. */
  alwaysSent?: boolean
  /** type note: the line, from the current values. */
  note?: (values: FieldValues) => ReactNode
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
  /** Field names in the server's messages → the words this form uses. */
  fieldLabels?: Record<string, string>
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
  schema, fields, defaultValues, onSubmit, changesOnly, submitLabel = 'Save', onCancel, errorMessages, fieldLabels, readOnly,
}: SchemaFormProps) {
  const resolver = useMemo<Resolver<FieldValues>>(() => {
    const inner = zodResolver(schema, (v) => hiddenFields(fields, v))
    const start = blankToEmpty(defaultValues)
    return (values, ctx, opts) => inner(prepare(withDefaultsWhenHidden(values, fields, start), fields), ctx, opts)
  }, [schema, fields, defaultValues])
  const form = useForm<FieldValues>({
    resolver,
    defaultValues: blankToEmpty(defaultValues),
  })
  const [error, setError] = useState<unknown>(null)
  const { errors, isSubmitting, dirtyFields, isDirty } = form.formState
  useReportUnsaved(isDirty)

  const submit = form.handleSubmit(async (values) => {
    setError(null)
    const off = notShown(fields, form.getValues())
    const out = changesOnly
      ? Object.fromEntries(Object.keys(dirtyFields).filter((k) => !off.includes(k)).map((k) => [k, emptyToNull(values[k])]))
      : Object.fromEntries(Object.entries(values).map(([k, v]) => [k, emptyToNull(v)]))
    try {
      await onSubmit(out)
    } catch (e) {
      // A refusal names its field (details.field), or several
      // (details.errors: [{field, message}] for a request the server's
      // schema refused); each message goes next to its input.
      // A field not on screen cannot show it: then it is said under the form.
      if (!applyServerError(form, fields.filter((f) => !off.includes(f.name)), e, errorMessages, fieldLabels)) setError(e)
    }
  })

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      <SchemaFields form={form} fields={fields} readOnly={readOnly} />
      {typeof errors.root?.message === 'string' && (
        <p role="alert" className="text-sm text-danger-600">{errors.root.message}</p>
      )}
      <ErrorMessage error={error} messages={errorMessages} fieldLabels={fieldLabels} />
      <div className="flex justify-end gap-2">
        {onCancel && (
          <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>{readOnly ? 'Close' : 'Cancel'}</Button>
        )}
        {!readOnly && <Button type="submit" disabled={isSubmitting}>{isSubmitting ? 'Saving…' : submitLabel}</Button>}
      </div>
    </form>
  )
}

/** The collapsed part of a group; it opens by itself when a field in it has an error. */
function Advanced({ failed, children }: { failed: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <details open={open || failed} onToggle={(e) => setOpen(e.currentTarget.open)} className="flex flex-col">
      <summary className="cursor-pointer text-sm font-medium text-neutral-600">Advanced</summary>
      <div className="mt-3 flex flex-col gap-4">{children}</div>
    </details>
  )
}

/** The fields of a SchemaForm on a react-hook-form form — also the header of
 *  a MasterDetailForm. Each FieldSpec type has its primitive (see FieldType);
 *  those with a `section` are grouped under its title, `advanced` ones collapsed,
 *  and one whose `showWhen` says no is left out. */
export function SchemaFields({ form, fields, readOnly, idPrefix = 'sf' }: {
  form: UseFormReturn<FieldValues>; fields: FieldSpec[]; readOnly?: boolean; idPrefix?: string
}) {
  const { errors } = form.formState
  const values = useWatch({ control: form.control })
  const hidden = notShown(fields, values)
  const shown = fields.filter((f) => !hidden.includes(f.name))
  const renderFields = (group: FieldSpec[]) => (
    <>
      {group.map((f) => {
        if (f.type === 'note') return <p key={f.name} className="text-sm font-medium text-neutral-700">{f.note?.(values)}</p>
        const id = `${idPrefix}-${f.name}`
        const err = errorOf(errors[f.name])
        const common = { id, disabled: readOnly || f.readOnly, 'aria-invalid': err ? true : undefined }
        let control
        if (f.type === 'checkbox') {
          control = (
            <Controller control={form.control} name={f.name} render={({ field }) => (
              <Checkbox id={id} checked={!!field.value} onCheckedChange={field.onChange} onBlur={field.onBlur}
                disabled={common.disabled} aria-invalid={common['aria-invalid']} />
            )} />
          )
        } else if (f.type === 'multiselect') {
          control = (
            <Controller control={form.control} name={f.name} render={({ field }) => (
              <MultiChoice label={f.label} options={f.options ?? []} values={Array.isArray(field.value) ? field.value : []}
                onChange={field.onChange} disabled={common.disabled} />
            )} />
          )
        } else if (f.type === 'tags') {
          control = (
            <Controller control={form.control} name={f.name} render={({ field }) => (
              <TagsControl label={f.label} values={Array.isArray(field.value) ? field.value : []} onChange={field.onChange}
                disabled={common.disabled} />
            )} />
          )
        } else if (f.type === 'segmented') {
          control = (
            <Controller control={form.control} name={f.name} render={({ field }) => (
              <Segmented id={id} value={field.value || null} onChange={field.onChange} onBlur={field.onBlur} options={f.options ?? []}
                disabled={common.disabled} aria-label={f.label} aria-invalid={common['aria-invalid']} />
            )} />
          )
        } else if (f.type === 'presets') {
          control = (
            <Controller control={form.control} name={f.name} render={({ field }) => (
              <PresetNumber id={id} value={field.value} onChange={field.onChange} onBlur={field.onBlur} options={f.options ?? []}
                disabled={common.disabled} aria-label={f.label} aria-invalid={common['aria-invalid']} />
            )} />
          )
        } else if (f.type === 'days') {
          control = (
            <Controller control={form.control} name={f.name} render={({ field }) => (
              <DayChips values={Array.isArray(field.value) ? field.value : []} onChange={field.onChange} options={f.options}
                disabled={common.disabled} aria-label={f.label} />
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
          <FormField key={f.name} label={f.label} htmlFor={id} required={f.required} hint={typeof f.hint === 'function' ? f.hint(values) : f.hint} error={typeof err === 'string' ? err : undefined}>
            {control}
          </FormField>
        )
      })}
    </>
  )
  const body = (group: FieldSpec[]) => {
    const advanced = group.filter((f) => f.advanced)
    return (
      <>
        {renderFields(group.filter((f) => !f.advanced))}
        {advanced.length > 0 && <Advanced failed={advanced.some((f) => errors[f.name])}>{renderFields(advanced)}</Advanced>}
      </>
    )
  }
  if (!fields.some((f) => f.section)) return body(shown)
  const titles = [...new Set(shown.map((f) => f.section))]
  return (
    <>
      {titles.map((t) => {
        const group = shown.filter((f) => f.section === t)
        return t ? <Section key={t} title={t} className="gap-4">{body(group)}</Section> : <div key="" className="flex flex-col gap-4">{body(group)}</div>
      })}
    </>
  )
}


/** TagInput in a form: the typed value that was not added yet is added when focus leaves the box (Save too). */
function TagsControl({ label, values, onChange, disabled }: {
  label: string; values: string[]; onChange: (v: string[]) => void; disabled?: boolean
}) {
  const [draft, setDraft] = useState('')
  if (disabled) return <p className="text-sm text-neutral-700">{values.length ? values.join(', ') : '—'}</p>
  return (
    <div onBlur={(e) => {
      if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
      const v = draft.trim()
      if (v && !values.includes(v)) onChange([...values, v])
      if (v) setDraft('')
    }}>
      <TagInput label={label} values={values} onChange={onChange} draft={draft} onDraftChange={setDraft} />
    </div>
  )
}


/** A field's message; for a list (tags, multiselect) the first of its items' messages. */
function errorOf(e: unknown): string | undefined {
  if (!e || typeof e !== 'object') return undefined
  const own = (e as { message?: unknown }).message
  if (typeof own === 'string' && own) return own
  const items = Array.isArray(e) ? e : Object.values(e as Record<string, unknown>)
  for (const x of items) {
    const m = x && typeof x === 'object' ? (x as { message?: unknown }).message : undefined
    if (typeof m === 'string' && m) return m
  }
  return undefined
}
