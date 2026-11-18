import React from 'react'
import { cn } from '../lib/cn'

interface LabelProps extends React.LabelHTMLAttributes<HTMLLabelElement> {
  required?: boolean
}

export function Label({ className, required, children, ...props }: LabelProps) {
  return (
    <label className={cn('text-sm font-medium text-fg-secondary', className)} {...props}>
      {children}
      {required && <span className="ml-1 font-normal text-danger-fg" aria-label="required">*</span>}
    </label>
  )
}
