import { Controller, useWatch, type FieldValues, type UseFormReturn } from 'react-hook-form'
import { Checkbox } from '../primitives/Checkbox'
import { Select } from '../primitives/Select'
import { FormField, Input } from '../primitives/shell'
import { ADDRESS_FIELDS, CA_PROVINCES, COUNTRIES, isPoBox } from './address'

/** The address under `name` (e.g. "ship_to") on a react-hook-form form, in a
 *  fieldset: name, attention, phone, address lines, city, province (the
 *  Canadian provinces when the country is Canada), postal code, country.
 *  A PO box is a warning with a confirm box (carriers may not deliver there).
 *  Its rules are addressSchema(); MasterDetailForm renders it (`addresses`). */
export function AddressFields({ form, name, legend, readOnly, phoneRequired }: {
  form: UseFormReturn<FieldValues>; name: string; legend: string; readOnly?: boolean; phoneRequired?: boolean
}) {
  const [country, line1, line2] = useWatch({ control: form.control, name: [`${name}.country`, `${name}.address1`, `${name}.address2`] }) as
    (string | undefined)[]
  const poBox = isPoBox(line1, line2)
  const errors = (form.formState.errors[name] ?? {}) as Record<string, { message?: unknown } | undefined>
  const err = (f: string) => (typeof errors[f]?.message === 'string' ? (errors[f]!.message as string) : undefined)
  const id = (f: string) => `addr-${name}-${f}`
  const text = (f: (typeof ADDRESS_FIELDS)[number], label: string, required?: boolean, className?: string) => (
    <FormField label={label} htmlFor={id(f)} required={required} error={err(f)} className={className}>
      <Input id={id(f)} disabled={readOnly} aria-invalid={err(f) ? true : undefined} {...form.register(`${name}.${f}`)} />
    </FormField>
  )
  return (
    <fieldset aria-label={legend} className="flex flex-col gap-3" disabled={readOnly}>
      <legend className="mb-1 font-semibold text-neutral-800">{legend}</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        {text('name', 'Name', true)}
        {text('attention', 'Attention / contact')}
        {text('phone', 'Phone', phoneRequired)}
        <FormField label="Country" htmlFor={id('country')} required error={err('country')}>
          <Controller control={form.control} name={`${name}.country`} render={({ field }) => (
            <Select id={id('country')} value={field.value || null} onChange={(v) => field.onChange(v ?? '')} options={COUNTRIES}
              disabled={readOnly} aria-invalid={err('country') ? true : undefined} />
          )} />
        </FormField>
        {text('address1', 'Address', true, 'sm:col-span-2')}
        {text('address2', 'Address line 2', false, 'sm:col-span-2')}
        {text('city', 'City', true)}
        <FormField label="Province" htmlFor={id('province')} required error={err('province')}>
          {country === 'CA' ? (
            <Controller control={form.control} name={`${name}.province`} render={({ field }) => (
              <Select id={id('province')} value={field.value || null} onChange={(v) => field.onChange(v ?? '')} options={CA_PROVINCES}
                placeholder="Choose…" disabled={readOnly} aria-invalid={err('province') ? true : undefined} />
            )} />
          ) : (
            <Input id={id('province')} disabled={readOnly} aria-invalid={err('province') ? true : undefined} {...form.register(`${name}.province`)} />
          )}
        </FormField>
        {text('zip', country === 'CA' ? 'Postal code (A1A 1A1)' : 'Postal code', true)}
      </div>
      {poBox && (
        <div role="note" className="flex flex-col gap-1 rounded-md bg-warning-50 p-2 text-sm text-warning-700">
          <span>This looks like a PO box: carriers may not deliver there.</span>
          <Controller control={form.control} name={`${name}.po_box_ok`} render={({ field }) => (
            <Checkbox checked={!!field.value} onCheckedChange={field.onChange} disabled={readOnly}
              aria-invalid={err('po_box_ok') ? true : undefined} label="Ship to this PO box anyway" />
          )} />
          {err('po_box_ok') && <span role="alert" className="text-danger-600">{err('po_box_ok')}</span>}
        </div>
      )}
    </fieldset>
  )
}
