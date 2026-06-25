import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../lib/cn'
import React from 'react'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors focus-visible:outline-none focus-visible:shadow-[0_0_0_3px_rgba(10,124,124,0.15)] disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary:
          'bg-primary-600 text-white hover:bg-primary-700 active:bg-primary-800 shadow-[0_1px_3px_rgba(10,124,124,0.08)]',
        secondary:
          'border border-neutral-200 bg-white text-neutral-700 hover:bg-neutral-50 hover:border-neutral-300',
        destructive:
          'bg-danger-600 text-white hover:bg-danger-700',
        ghost:
          'text-primary-600 hover:bg-primary-600/8',
        'success-outline':
          'border border-success-600 text-success-600 hover:bg-success-50',
      },
      size: {
        sm: 'h-8 px-3 text-xs',
        md: 'h-10 px-4 text-sm',
        lg: 'h-12 px-6 text-base',
        icon: 'h-10 w-10',
        'icon-sm': 'h-8 w-8',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, children, ...props }, ref) => (
    <button
      className={cn(buttonVariants({ variant, size }), className)}
      ref={ref}
      {...props}
    >
      {children}
    </button>
  )
)
Button.displayName = 'Button'
