import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

type TextProps = { as?: 'span' | 'p' | 'dt'; className?: string; children: ReactNode }

/** Secondary text at the size around it (an empty value, a note in a cell). */
export function Muted({ as: Tag = 'span', className, children }: TextProps) {
  return <Tag className={cn('text-neutral-500', className)}>{children}</Tag>
}

/** A small line of explanation under or beside something. */
export function Hint({ as: Tag = 'span', className, children }: TextProps) {
  return <Tag className={cn('text-xs text-neutral-500', className)}>{children}</Tag>
}
