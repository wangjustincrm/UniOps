import { cva, type VariantProps } from 'class-variance-authority'
import { Loader2 } from 'lucide-react'
import { cn } from '../lib/cn'
import React from 'react'

/**
 * The only button in UniOps. Pages never style a <button> themselves.
 *
 *   primary      the one main action of a screen or dialog
 *   secondary    everything else that is a button
 *   ghost        quiet action inside a toolbar, row or card header
 *   link         inline text action
 *   danger       destructive confirm (delete / void / reject)
 *   danger-outline  destructive but not final (opens a confirm)
 *   success-outline  positive secondary (approve-like, rare)
 *
 * `loading` disables the button and shows a spinner, so a double click can
 * never fire the action twice (UI-BTN-001).
 */
export const buttonVariants = cva(
  'inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium ' +
  'transition-colors duration-150 focus-visible:outline-none focus-visible:shadow-focus ' +
  'disabled:pointer-events-none disabled:opacity-50 active:translate-y-px',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-fg-on-accent hover:bg-accent-hover active:bg-accent-active shadow-xs',
        secondary: 'border border-line bg-surface text-fg-secondary hover:bg-surface-subtle hover:border-line-strong hover:text-fg',
        ghost: 'text-accent-fg hover:bg-accent-soft',
        subtle: 'text-fg-secondary hover:bg-surface-sunken hover:text-fg',
        link: 'h-auto px-0 text-accent-fg underline-offset-2 hover:underline',
        danger: 'bg-danger-solid text-fg-on-accent hover:bg-danger-700',
        destructive: 'bg-danger-solid text-fg-on-accent hover:bg-danger-700',
        'danger-outline': 'border border-danger-line bg-surface text-danger-fg hover:bg-danger-soft',
        'success-outline': 'border border-success-line bg-surface text-success-fg hover:bg-success-soft',
        // Positive final action (Approve). One per screen.
        success: 'bg-success-solid text-fg-on-accent hover:bg-success-700',
        // Return-for-revision style action.
        warning: 'bg-warning-solid text-fg-on-accent hover:bg-warning-700',
        // Accent outline: an important secondary action (Create PA).
        outline: 'border border-accent bg-surface text-accent-fg hover:bg-accent-soft',
        // Remove-row / delete icon: quiet until hovered.
        'danger-ghost': 'text-fg-faint hover:bg-danger-soft hover:text-danger-fg',
        'warning-outline': 'border border-warning-line bg-surface text-warning-fg hover:bg-warning-soft',
        // On dark surfaces (PDF viewer).
        inverse: 'bg-inverse-raised text-fg-on-inverse hover:bg-inverse-hover',
        'inverse-ghost': 'text-fg-on-inverse-muted hover:bg-inverse-raised hover:text-fg-on-inverse',
      },
      size: {
        xs: 'h-7 px-2.5 text-xs',
        sm: 'h-8 px-3 text-xs',
        md: 'h-10 px-4 text-sm',
        lg: 'h-12 px-6 text-base',
        icon: 'h-10 w-10',
        'icon-sm': 'h-8 w-8',
        'icon-xs': 'h-7 w-7',
      },
    },
    compoundVariants: [{ variant: 'link', className: 'h-auto px-0' }],
    defaultVariants: { variant: 'primary', size: 'md' },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
  loading?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  // `type` is NOT defaulted: existing forms rely on the HTML default (submit).
  ({ className, variant, size, children, loading, disabled, asChild: _asChild, ...props }, ref) => (
    <button
      className={cn(buttonVariants({ variant, size }), className)}
      ref={ref}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  )
)
Button.displayName = 'Button'

export interface IconButtonProps extends Omit<ButtonProps, 'children'> {
  /** Required: an icon-only button must say what it does. */
  label: string
  icon: React.ReactNode
}

/** Square icon-only button. Quiet by default. */
export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ label, icon, variant = 'subtle', size = 'icon-sm', title, ...props }, ref) => (
    <Button ref={ref} variant={variant} size={size} aria-label={label} title={title ?? label} {...props}>
      {icon}
    </Button>
  )
)
IconButton.displayName = 'IconButton'
