import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../lib/cn'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium',
  {
    variants: {
      variant: {
        neutral: 'bg-neutral-100 text-neutral-500',
        warning: 'bg-warning-50 text-warning-600',
        info: 'bg-primary-100 text-primary-700',
        success: 'bg-success-50 text-success-700',
        danger: 'bg-danger-50 text-danger-600',
        dark: 'bg-neutral-600 text-white',
      },
    },
    defaultVariants: { variant: 'neutral' },
  }
)

interface BadgeProps extends VariantProps<typeof badgeVariants> {
  className?: string
  children: React.ReactNode
}

export function Badge({ variant, className, children }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant }), className)}>
      {children}
    </span>
  )
}
// StatusBadge (domain status→variant mapping) stays in the app — see epms badge.tsx.
