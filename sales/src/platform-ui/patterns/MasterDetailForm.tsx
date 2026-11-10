import { useReportUnsaved } from '../primitives/unsaved'
import { ChangedElsewhere } from './ChangedElsewhere'
import { useEditVersion } from './editVersion'
import { useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useForm, useWatch, type FieldValues, type Resolver } from 'react-hook-form'
import type { z } from 'zod'
import { EditableTable, type EditableColumn } from '../primitives/EditableTable'
import { Button } from '../primitives/shell'
import { ErrorMessage } from './ErrorMessage'
import { AddressFields } from './AddressFields'
import { ADDRESS_FIELDS } from './address'
import { SchemaFields, type FieldSpec } from './SchemaForm'
import { applyServerError, emptyToNull, hiddenFields, prepare, toInputs, withDefaultsWhenHidden } from './formValues'
import { zodResolver } from './zodResolver'

export interface MasterDetailState<L> {
  values: FieldValues
  lines: L[]
}

export interface MasterDetailLines<L> {
  /** The lines table's accessible name. */
  label: string
  columns: EditableColumn<L>[]
  initial: L[]
  /** With it: "Add line". */
  add?: () => L
  addLabel?: string
  removable?: boolean
  minRows?: number
  rowKey?: (line: L, index: number) => string | number
  /** A line's problem: shown in its row; the form cannot be submitted while any line has one. */
  problem?: (line: L, index: number) => string | null | undefined
  footer?: ReactNode[]
}

export interface MasterDetailFormProps<L> {
  /** The header: SchemaForm's zod schema and FieldSpecs (reference fields are Autocompletes). */
  schema: z.ZodType<FieldValues, FieldValues>
  fields: FieldSpec[]
  defaultValues: Record<string, unknown>
  lines: MasterDetailLines<L>
  /** Under the lines, e.g. a computed preview; it sees the current values. */
  preview?: (state: MasterDetailState<L>) => ReactNode
  /** On every change (e.g. to forget a preview that no longer applies). */
  onChange?: (state: MasterDetailState<L>) => void
  /** Why the form may not be submitted yet (e.g. "preview the EA first"), or null. */
  blocked?: (state: MasterDetailState<L>) => string | null
  onSubmit: (values: Record<string, unknown>, lines: L[], edit: { version: number | undefined }) => unknown | Promise<unknown>
  /** A versioned record's version, as SchemaForm's (useEditVersion). */
  version?: number
  submitLabel?: string
  /** A second way to submit the same form (e.g. "Confirm and send"). */
  alsoSubmit?: { label: string; onSubmit: (values: Record<string, unknown>, lines: L[], edit: { version: number | undefined }) => unknown | Promise<unknown> }
  onCancel?: () => void
  errorMessages?: Record<string, string>
  readOnly?: boolean
  /** Address sections after the header fields (e.g. Ship to): AddressFields
   *  under `name`; their rules go in `schema` (addressSchema()). */
  addresses?: { name: string; legend: string; phoneRequired?: boolean }[]
}

/** A record with lines — a manual order, a transfer, an invoice: the header's
 *  fields (as SchemaForm), the lines (EditableTable) and an optional preview,
 *  with ONE submit. Header errors go next to their field (the server's too),
 *  line errors in their row; the submit runs once (pending + a ref guard).
 *  Always rendered in a Sheet or a Dialog — a page never carries a form. */
export function MasterDetailForm<L>({
  schema, fields, defaultValues, lines: spec, preview, onChange, blocked, onSubmit, submitLabel = 'Save', alsoSubmit, onCancel,
  errorMessages, readOnly, addresses = [], version,
}: MasterDetailFormProps<L>) {
  // Server errors name nested address fields too (e.g. ship_to.zip).
  const known: FieldSpec[] = [...fields, ...addresses.flatMap((a) => ADDRESS_FIELDS.map((f) => ({ name: `${a.name}.${f}`, label: f })))]
  const resolver = useMemo<Resolver<FieldValues>>(() => {
    const inner = zodResolver(schema, (v) => hiddenFields(fields, v))
    const start = toInputs(defaultValues, fields)
    return (values, ctx, opts) => inner(prepare(withDefaultsWhenHidden(values, fields, start), fields), ctx, opts)
  }, [schema, fields, defaultValues])
  const form = useForm<FieldValues>({ resolver, defaultValues: toInputs(defaultValues, fields) })
  const [lines, setLines] = useState<L[]>(spec.initial)
  const [startLines, setStartLines] = useState(lines)
  const dirty = form.formState.isDirty || lines !== startLines
  useReportUnsaved(dirty)
  const edit = useEditVersion(version, dirty, () => {
    form.reset(toInputs(defaultValues, fields)); setLines(spec.initial); setStartLines(spec.initial)
  })
  const [error, setError] = useState<unknown>(null)
  const inFlight = useRef(false)
  const values = useWatch({ control: form.control }) as FieldValues
  const state = { values, lines }
  const lineProblems = lines.map((l, i) => spec.problem?.(l, i) || null)
  const tooFew = lines.length < (spec.minRows ?? 1)
  const waiting = blocked?.(state) ?? null
  const { isSubmitting } = form.formState

  // The lines as last changed: a header field's change event must not report older ones.
  const latestLines = useRef(lines)
  const changeLines = (next: L[]) => {
    latestLines.current = next
    setLines(next)
    onChange?.({ values: form.getValues(), lines: next })
  }
  // handleSubmit is built when the form is submitted, never during render (it reads the ref).
  const submitWith = (handler: typeof onSubmit) => form.handleSubmit(async (v) => {
    if (inFlight.current || lineProblems.some(Boolean) || tooFew || waiting) return
    inFlight.current = true
    setError(null)
    try {
      await handler(Object.fromEntries(Object.entries(v).map(([k, x]) => [k, emptyToNull(x)])), lines, { version: edit.seen })
    } catch (e) {
      if (!applyServerError(form, known, e, errorMessages)) setError(e)
    } finally {
      inFlight.current = false
    }
  })
  const submit = (e: FormEvent<HTMLFormElement>) => submitWith(onSubmit)(e)

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4"
      onChange={() => onChange?.({ values: form.getValues(), lines: latestLines.current })}>
      {edit.changedElsewhere && <ChangedElsewhere onReload={edit.takeTheirs} />}
      <SchemaFields form={form} fields={fields} readOnly={readOnly} idPrefix="md" />
      {addresses.map((a) => <AddressFields key={a.name} form={form} name={a.name} legend={a.legend} readOnly={readOnly}
        phoneRequired={a.phoneRequired} />)}
      <EditableTable<L> ariaLabel={spec.label} rows={lines} onChange={changeLines} columns={spec.columns}
        rowKey={spec.rowKey} errors={spec.problem ? (_, i) => lineProblems[i] : undefined}
        onAdd={readOnly ? undefined : spec.add} addLabel={spec.addLabel} removable={!readOnly && spec.removable}
        minRows={spec.minRows} footer={spec.footer} disabled={readOnly || isSubmitting} />
      {tooFew && <p role="alert" className="text-sm text-danger-600">Add at least {spec.minRows ?? 1} line.</p>}
      {preview?.(state)}
      {waiting && <p className="text-sm text-neutral-600">{waiting}</p>}
      <ErrorMessage error={error} messages={errorMessages} />
      <div className="flex justify-end gap-2">
        {onCancel && <Button type="button" variant="secondary" onClick={onCancel} disabled={isSubmitting}>{readOnly ? 'Close' : 'Cancel'}</Button>}
        {!readOnly && alsoSubmit && (
          <Button type="button" variant="secondary" disabled={isSubmitting || lineProblems.some(Boolean) || tooFew || !!waiting}
            onClick={(e) => submitWith(alsoSubmit.onSubmit)(e)}>{alsoSubmit.label}</Button>
        )}
        {!readOnly && (
          <Button type="submit" disabled={isSubmitting || lineProblems.some(Boolean) || tooFew || !!waiting}>
            {isSubmitting ? 'Saving…' : submitLabel}
          </Button>
        )}
      </div>
    </form>
  )
}
