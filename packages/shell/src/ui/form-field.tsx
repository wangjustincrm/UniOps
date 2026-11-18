import { cn } from '../lib/cn'
import { Label } from './label'

interface FormFieldProps {
  label: React.ReactNode
  required?: boolean
  error?: string | null
  hint?: React.ReactNode
  className?: string
  children: React.ReactNode
  htmlFor?: string
}

/** Label + control + hint/error, stacked. The standard way to lay out a field. */
export function FormField({ label, required, error, hint, className, children, htmlFor }: FormFieldProps) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <Label htmlFor={htmlFor} required={required}>{label}</Label>
      {children}
      {hint && !error && <p className="text-xs text-fg-muted">{hint}</p>}
      {error && <p className="text-xs text-danger-fg" role="alert">{error}</p>}
    </div>
  )
}
