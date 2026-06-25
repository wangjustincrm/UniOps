import { cn } from '../lib/cn'
import { Label } from './label'

interface FormFieldProps {
  label: string
  required?: boolean
  error?: string
  hint?: string
  className?: string
  children: React.ReactNode
  htmlFor?: string
}

export function FormField({
  label,
  required,
  error,
  hint,
  className,
  children,
  htmlFor,
}: FormFieldProps) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <Label htmlFor={htmlFor} required={required}>
        {label}
      </Label>
      {children}
      {hint && !error && (
        <p className="text-xs text-neutral-500">{hint}</p>
      )}
      {error && (
        <p className="text-xs text-danger-600" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
