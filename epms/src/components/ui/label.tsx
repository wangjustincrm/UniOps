import React from 'react'
import { cn } from '@/lib/utils'

interface LabelProps extends React.LabelHTMLAttributes<HTMLLabelElement> {
  required?: boolean
}

export function Label({ className, required, children, ...props }: LabelProps) {
  return (
    <label
      className={cn('text-sm font-medium text-neutral-700', className)}
      {...props}
    >
      {children}
      {required && <span className="ml-1 text-neutral-400 font-normal" aria-label="required">*</span>}
    </label>
  )
}
