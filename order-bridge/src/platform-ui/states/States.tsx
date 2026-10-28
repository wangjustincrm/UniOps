import type { ReactNode } from 'react'
import { AlertTriangle, Inbox } from 'lucide-react'
import { Button } from '@uniops/shell'
import { errorText } from '../lib/businessError'
import { cn } from '../lib/cn'

export function EmptyState({
  title, description, action, className,
}: {
  title: string
  description?: string
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-neutral-200 bg-white p-10 text-center', className)}>
      <Inbox className="h-6 w-6 text-neutral-300" />
      <p className="font-medium text-neutral-800">{title}</p>
      {description && <p className="max-w-md text-sm text-neutral-500">{description}</p>}
      {action}
    </div>
  )
}

/** A failed load, with the error's text (by BusinessError code when mapped). */
export function ErrorState({
  error, messages, onRetry, className,
}: {
  error: unknown
  messages?: Record<string, string>
  onRetry?: () => void
  className?: string
}) {
  return (
    <div role="alert" className={cn('flex flex-col items-center gap-2 rounded-lg border border-danger-100 bg-danger-50 p-6 text-center', className)}>
      <AlertTriangle className="h-5 w-5 text-danger-600" />
      <p className="text-sm text-danger-700">{errorText(error, messages)}</p>
      {onRetry && <Button variant="secondary" size="sm" onClick={onRetry}>Try again</Button>}
    </div>
  )
}

/** Inline error line for forms and actions. */
export function ErrorMessage({ error, messages }: { error: unknown; messages?: Record<string, string> }) {
  if (!error) return null
  return <p role="alert" className="text-sm text-danger-600">{errorText(error, messages)}</p>
}
