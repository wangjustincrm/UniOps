import React from 'react'
import { cn } from '../lib/cn'

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  error?: boolean
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, error, ...props }, ref) => (
    <input
      className={cn(
        'h-10 w-full rounded-lg border bg-neutral-100 px-3 py-2 text-sm text-neutral-900 placeholder:text-neutral-400',
        'transition-colors focus:outline-none focus:bg-white focus:border-primary-600 focus:shadow-[0_0_0_3px_rgba(10,124,124,0.10)]',
        'disabled:cursor-not-allowed disabled:bg-neutral-100 disabled:text-neutral-400 disabled:border-neutral-200',
        'read-only:bg-neutral-50 read-only:border-neutral-200',
        error
          ? 'border-danger-600 bg-danger-50 focus:shadow-[0_0_0_3px_rgba(220,38,38,0.10)]'
          : 'border-neutral-200',
        className
      )}
      ref={ref}
      {...props}
    />
  )
)
Input.displayName = 'Input'
