import type { ElementType, HTMLAttributes, ReactNode } from 'react'
import { cn } from '../lib/cn'
import { SIZE, TONE, WEIGHT, WRAP, type TextTone } from './textStyle'

type TextProps = { as?: ElementType; className?: string; children: ReactNode }

/** Secondary text at the size around it (an empty value, a note in a cell): <Text tone="muted">. */
export function Muted({ as = 'span', className, children }: TextProps) {
  return <Text as={as} tone="muted" className={className}>{children}</Text>
}

/** A small line of explanation under or beside something: <Text tone="muted" size="xs">. */
export function Hint({ as = 'span', className, children }: TextProps) {
  return <Text as={as} tone="muted" size="xs" className={className}>{children}</Text>
}

/** A run of text with one of the set looks (2026-11-18): tone, size, weight, figures that line up, how it
 *  wraps. Pages never write text classes by hand. */
export function Text({ as: As = 'span', tone = 'default', size, weight, nums, wrap, className, children, ...rest }: {
  as?: ElementType
  tone?: TextTone
  size?: keyof typeof SIZE
  weight?: keyof typeof WEIGHT
  /** Figures of equal width (amounts, quantities in a column). */
  nums?: boolean
  wrap?: keyof typeof WRAP
  className?: string
  children?: ReactNode
} & Omit<HTMLAttributes<HTMLElement>, 'className' | 'children'>) {
  return (
    <As className={cn(TONE[tone], size && SIZE[size], weight && WEIGHT[weight], nums && 'tabular-nums', wrap && WRAP[wrap], className) || undefined}
      {...rest}>
      {children}
    </As>
  )
}
