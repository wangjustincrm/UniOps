/** Alert, EmptyState, Spinner. */
import React from 'react'
import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from 'lucide-react'
import { cn } from '../lib/cn'

type Tone = 'info' | 'success' | 'warning' | 'danger' | 'neutral'

const alertTone: Record<Tone, { box: string; icon: React.ComponentType<{ className?: string }> }> = {
  info:    { box: 'border-info-line bg-info-soft text-info-fg', icon: Info },
  success: { box: 'border-success-line bg-success-soft text-success-fg', icon: CheckCircle2 },
  warning: { box: 'border-warning-line bg-warning-soft text-warning-fg', icon: AlertTriangle },
  danger:  { box: 'border-danger-line bg-danger-soft text-danger-fg', icon: XCircle },
  neutral: { box: 'border-line bg-surface-subtle text-fg-secondary', icon: Info },
}

export interface AlertProps {
  tone?: Tone
  title?: React.ReactNode
  children?: React.ReactNode
  /** Right-side action (a small Button). */
  action?: React.ReactNode
  /** Hide the leading icon. */
  plain?: boolean
  className?: string
}

/** Inline message on a page or in a form. role=alert for danger/warning. */
export function Alert({ tone = 'info', title, children, action, plain, className }: AlertProps) {
  const t = alertTone[tone]
  const Icon = t.icon
  return (
    <div
      role={tone === 'danger' || tone === 'warning' ? 'alert' : 'status'}
      className={cn('flex items-start gap-2.5 rounded-lg border px-3.5 py-2.5 text-sm', t.box, className)}
    >
      {!plain && <Icon className="mt-0.5 h-4 w-4 shrink-0" />}
      <div className="min-w-0 flex-1">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className={cn(title && 'mt-0.5', 'whitespace-pre-line [&_a]:underline')}>{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  )
}

export interface EmptyStateProps {
  /** lucide icon element, e.g. <FileText />. */
  icon?: React.ReactNode
  title: React.ReactNode
  description?: React.ReactNode
  action?: React.ReactNode
  className?: string
  /** compact for inside a table/card. */
  size?: 'compact' | 'normal'
}

export function EmptyState({ icon, title, description, action, className, size = 'normal' }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center text-center', size === 'compact' ? 'gap-1.5 py-8' : 'gap-2 py-16', className)}>
      {icon && <div className="mb-1 text-fg-faint [&>svg]:h-8 [&>svg]:w-8 [&>svg]:stroke-[1.5]">{icon}</div>}
      <p className="text-sm font-medium text-fg">{title}</p>
      {description && <p className="max-w-sm text-sm text-fg-muted">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}

export function Spinner({ className, label = 'Loading' }: { className?: string; label?: string }) {
  return <Loader2 role="status" aria-label={label} className={cn('h-4 w-4 animate-spin text-fg-muted', className)} />
}

/** Centered loading block for a page or panel. */
export function LoadingState({ label = 'Loading…', className }: { label?: string; className?: string }) {
  return (
    <div className={cn('flex items-center justify-center gap-2 py-16 text-sm text-fg-muted', className)}>
      <Spinner /> {label}
    </div>
  )
}
