import React from 'react'
import { ChevronDown, Search } from 'lucide-react'
import { cn } from '../lib/cn'
import { controlBase, controlClass } from './input'

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: boolean
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, error, rows = 3, ...props }, ref) => (
    <textarea
      ref={ref}
      rows={rows}
      aria-invalid={error || undefined}
      className={cn(controlBase, 'min-h-20 px-3 py-2', error ? 'border-danger-solid bg-danger-soft' : 'border-line', className)}
      {...props}
    />
  )
)
Textarea.displayName = 'Textarea'

export interface SelectProps extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  error?: boolean
  selectSize?: 'sm' | 'md'
}

/** Native select (keyboard, mobile pickers and form posts all keep working). */
export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, error, selectSize = 'md', children, ...props }, ref) => (
    // className sizes/positions the wrapper (e.g. "w-40"); the select fills it.
    <div className={cn('relative w-full', className)}>
      <select
        ref={ref}
        aria-invalid={error || undefined}
        className={cn(controlClass(error, selectSize), 'appearance-none pr-8')}
        {...props}
      >
        {children}
      </select>
      <ChevronDown aria-hidden className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-faint" />
    </div>
  )
)
Select.displayName = 'Select'

export interface SearchInputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size' | 'type'> {
  inputSize?: 'sm' | 'md'
}

/** Text input with a leading search icon. Placeholder should be short: "Search". */
export const SearchInput = React.forwardRef<HTMLInputElement, SearchInputProps>(
  ({ className, inputSize = 'md', placeholder = 'Search', ...props }, ref) => (
    <div className={cn('relative', className)}>
      <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-faint" />
      <input ref={ref} type="search" placeholder={placeholder} className={cn(controlClass(false, inputSize), 'pl-8')} {...props} />
    </div>
  )
)
SearchInput.displayName = 'SearchInput'

interface CheckProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: React.ReactNode
  description?: React.ReactNode
}

const checkBase =
  'h-4 w-4 shrink-0 cursor-pointer border-line-strong text-accent accent-accent ' +
  'focus-visible:shadow-focus disabled:cursor-not-allowed disabled:opacity-50'

function withLabel(control: React.ReactNode, label: React.ReactNode, description: React.ReactNode, disabled?: boolean, className?: string) {
  if (!label) return control
  return (
    <label className={cn('inline-flex items-start gap-2 text-sm text-fg', disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer', className)}>
      <span className="flex h-5 items-center">{control}</span>
      <span className="min-w-0">
        {label}
        {description && <span className="block text-xs text-fg-muted">{description}</span>}
      </span>
    </label>
  )
}

export const Checkbox = React.forwardRef<HTMLInputElement, CheckProps>(
  ({ label, description, className, ...props }, ref) =>
    <>{withLabel(<input ref={ref} type="checkbox" className={cn(checkBase, 'rounded', !label && className)} {...props} />, label, description, props.disabled, className)}</>
)
Checkbox.displayName = 'Checkbox'

export const Radio = React.forwardRef<HTMLInputElement, CheckProps>(
  ({ label, description, className, ...props }, ref) =>
    <>{withLabel(<input ref={ref} type="radio" className={cn(checkBase, 'rounded-full', !label && className)} {...props} />, label, description, props.disabled, className)}</>
)
Radio.displayName = 'Radio'

/** File picker: hidden native input behind a styled trigger. */
export const FileInput = React.forwardRef<HTMLInputElement, Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      type="file"
      className={cn(
        'block w-full text-sm text-fg-secondary file:mr-3 file:h-8 file:cursor-pointer file:rounded-lg file:border file:border-line',
        'file:bg-surface file:px-3 file:text-xs file:font-medium file:text-fg-secondary hover:file:bg-surface-subtle',
        className,
      )}
      {...props}
    />
  )
)
FileInput.displayName = 'FileInput'
