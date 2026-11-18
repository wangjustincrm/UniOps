import React from 'react'
import { cn } from '../lib/cn'

/** Shared look of every text-like control (input, select, textarea). */
export const controlBase =
  'w-full rounded-lg border bg-surface text-sm text-fg placeholder:text-fg-faint ' +
  'transition-colors duration-150 focus:outline-none focus:border-accent focus:shadow-focus ' +
  'disabled:cursor-not-allowed disabled:bg-surface-sunken disabled:text-fg-muted ' +
  'read-only:bg-surface-subtle'

const controlSize = { sm: 'h-8 px-2.5 text-xs', md: 'h-10 px-3' } as const

export function controlClass(error?: boolean, size: keyof typeof controlSize = 'md') {
  return cn(controlBase, controlSize[size], error ? 'border-danger-solid bg-danger-soft' : 'border-line')
}

export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  error?: boolean
  /** sm for filters / table cells, md (default) for forms. */
  inputSize?: keyof typeof controlSize
  /** No border, fill or padding — for an input inside a composite control
   *  (search box in a picker) whose container draws the frame. */
  bare?: boolean
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, error, inputSize = 'md', bare, ...props }, ref) => (
    <input
      className={cn(
        bare ? 'w-full bg-transparent text-sm text-fg placeholder:text-fg-faint focus:outline-none' : controlClass(error, inputSize),
        className,
      )}
      aria-invalid={error || undefined}
      ref={ref}
      {...props}
    />
  )
)
Input.displayName = 'Input'
