import type { Ref, TextareaHTMLAttributes } from 'react'
import { Input, type InputProps } from './shell'
import { isoToLocalInput, localInputToIso } from './dateValues'
import { cn } from '../lib/cn'

type Base = Omit<InputProps, 'type'> & { ref?: Ref<HTMLInputElement> }

/** A number as typed (a string, so no precision is lost); any step. */
export function NumberInput({ ref, ...props }: Base) {
  return <Input ref={ref} type="number" step="any" inputMode="decimal" {...props} />
}

/** A date-only value, "YYYY-MM-DD" as a string — never through `new Date`. */
export function DateInput({ ref, ...props }: Base) {
  return <Input ref={ref} type="date" {...props} />
}

export interface DateTimeInputProps extends Omit<Base, 'value' | 'onChange' | 'defaultValue'> {
  /** ISO with a zone (or null). */
  value: string | null
  onChange: (iso: string | null) => void
}

/** A date and time: shown in the user's local time, given back as ISO (UTC). */
export function DateTimeInput({ value, onChange, ref, ...props }: DateTimeInputProps) {
  return (
    <Input ref={ref} type="datetime-local" value={isoToLocalInput(value)}
      onChange={(e) => onChange(localInputToIso(e.target.value))} {...props} />
  )
}

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & { ref?: Ref<HTMLTextAreaElement> }

/** Several lines of text. */
export function Textarea({ className, ref, rows = 4, ...props }: TextareaProps) {
  return (
    <textarea ref={ref} rows={rows} {...props}
      className={cn(
        'w-full rounded-lg border border-neutral-200 bg-neutral-100 px-3 py-2 text-sm text-neutral-900 placeholder:text-neutral-400',
        'focus:outline-none focus:bg-white focus:border-primary-600 focus:shadow-[0_0_0_3px_rgba(10,124,124,0.10)]',
        'disabled:cursor-not-allowed disabled:text-neutral-400 aria-[invalid=true]:border-danger-600',
        className,
      )} />
  )
}
