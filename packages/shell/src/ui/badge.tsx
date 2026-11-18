import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../lib/cn'

/**
 * Status / category label. Tone carries the meaning; pages never pass colour
 * classes. `dot` adds the small status dot in the tone's colour.
 *
 * Domain mapping (document status → tone + wording) stays in each app, built
 * with createStatusBadge() below, so every app renders statuses the same way.
 */
const badgeVariants = cva(
  'inline-flex max-w-full items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium',
  {
    variants: {
      variant: {
        neutral: 'bg-surface-sunken text-fg-secondary',
        info: 'bg-accent-soft text-accent-fg',
        accent: 'bg-accent-soft text-accent-fg',
        blue: 'bg-info-soft text-info-fg',
        success: 'bg-success-soft text-success-fg',
        warning: 'bg-warning-soft text-warning-fg',
        danger: 'bg-danger-soft text-danger-fg',
        // Finished / closed states (Paid, Closed, Processed). Mid grey, as before
        // the token migration — not near-black (white on #4E6070 = 6.6:1).
        dark: 'bg-fg-secondary text-fg-on-accent',
        outline: 'border border-line text-fg-secondary',
      },
    },
    defaultVariants: { variant: 'neutral' },
  }
)

const dotTone: Record<string, string> = {
  neutral: 'bg-fg-faint',
  info: 'bg-accent',
  accent: 'bg-accent',
  blue: 'bg-info-solid',
  success: 'bg-success-solid',
  warning: 'bg-warning-solid',
  danger: 'bg-danger-solid',
  dark: 'bg-fg-faint',
  outline: 'bg-fg-faint',
}

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>['variant']>

interface BadgeProps extends VariantProps<typeof badgeVariants> {
  className?: string
  /** Show a status dot before the text. */
  dot?: boolean
  title?: string
  children: React.ReactNode
}

export function Badge({ variant, dot, className, title, children }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant }), className)} title={title}>
      {dot && <span aria-hidden className={cn('size-1.5 shrink-0 rounded-full', dotTone[variant ?? 'neutral'])} />}
      {children}
    </span>
  )
}

export interface StatusDef { label: string; tone: BadgeTone }

/**
 * Build an app's StatusBadge + statusLabel from one table, so a status has one
 * name and one colour everywhere in that app.
 *
 *   const { StatusBadge, statusLabel } = createStatusBadge({
 *     draft: { label: 'Draft', tone: 'neutral' },
 *     approved: { label: 'Approved', tone: 'success' },
 *   })
 */
export function createStatusBadge<S extends string>(table: Record<S, StatusDef>) {
  const lookup = (status: string): StatusDef | undefined => (table as Record<string, StatusDef>)[status]
  function statusLabel(status: string): string {
    return lookup(status)?.label ?? String(status)
  }
  function statusTone(status: string): BadgeTone {
    return lookup(status)?.tone ?? 'neutral'
  }
  function StatusBadge({ status, label, className }: { status: S | string; label?: string; className?: string }) {
    return (
      <Badge variant={statusTone(status)} dot className={className}>
        {label ?? statusLabel(status)}
      </Badge>
    )
  }
  return { StatusBadge, statusLabel, statusTone }
}
