import * as RC from '@radix-ui/react-checkbox'
import { Check } from 'lucide-react'
import { useId, type ReactNode } from 'react'
import { cn } from '../lib/cn'

export interface CheckboxProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  /** Visible label (linked with htmlFor, so the whole text is clickable). */
  label?: ReactNode
  id?: string
  name?: string
  disabled?: boolean
  'aria-label'?: string
  'aria-invalid'?: boolean
  onBlur?: () => void
  className?: string
}

/** A checkbox (Radix): Space toggles, role and state for screen readers. */
export function Checkbox({ checked, onCheckedChange, label, id, name, disabled, onBlur, className, ...aria }: CheckboxProps) {
  const auto = useId()
  const boxId = id ?? auto
  const box = (
    <RC.Root
      id={boxId}
      name={name}
      checked={checked}
      disabled={disabled}
      onBlur={onBlur}
      onCheckedChange={(c) => onCheckedChange(c === true)}
      {...aria}
      className={cn(
        'flex h-4 w-4 shrink-0 items-center justify-center rounded border border-neutral-300 bg-white',
        'focus:outline-none focus-visible:shadow-[0_0_0_3px_rgba(10,124,124,0.15)]',
        'data-[state=checked]:border-primary-600 data-[state=checked]:bg-primary-600 data-[state=checked]:text-white',
        'disabled:cursor-not-allowed disabled:opacity-50',
        !label && className,
      )}
    >
      <RC.Indicator><Check className="h-3 w-3" strokeWidth={3} /></RC.Indicator>
    </RC.Root>
  )
  if (!label) return box
  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      {box}
      <label htmlFor={boxId} className={cn('cursor-pointer select-none', disabled && 'cursor-not-allowed opacity-60')}>{label}</label>
    </span>
  )
}

export interface CheckboxListProps {
  options: { value: string; label: string }[]
  values: string[]
  onChange: (values: string[]) => void
  /** The group's accessible name. */
  label: string
  disabled?: boolean
  className?: string
}

/** Several values from a short list (a multi-select): one checkbox each. */
export function CheckboxList({ options, values, onChange, label, disabled, className }: CheckboxListProps) {
  return (
    <div role="group" aria-label={label} className={cn('flex flex-wrap gap-x-4 gap-y-1.5 text-sm', className)}>
      {options.map((o) => (
        <Checkbox
          key={o.value}
          label={o.label}
          checked={values.includes(o.value)}
          disabled={disabled}
          onCheckedChange={(on) => onChange(on ? [...values, o.value] : values.filter((v) => v !== o.value))}
        />
      ))}
    </div>
  )
}
