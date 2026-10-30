import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

export interface ChipProps {
  children: ReactNode
  /** Clicking the chip's text (e.g. flips a sort direction). */
  onClick?: () => void
  /** Shows an × that calls this. */
  onRemove?: () => void
  /** The ×'s accessible name, e.g. "Remove sort by Code". */
  removeLabel?: string
  className?: string
}

/** A small rounded token: an active sort or filter, or a value in a tag input. */
export function Chip({ children, onClick, onRemove, removeLabel = 'Remove', className }: ChipProps) {
  return (
    <span className={cn('inline-flex max-w-full items-center gap-1 rounded-full border border-neutral-200 bg-neutral-50 py-0.5 pl-2.5 pr-1 text-xs text-neutral-700', !onRemove && 'pr-2.5', className)}>
      {onClick
        ? <button type="button" onClick={onClick} className="truncate hover:text-neutral-900">{children}</button>
        : <span className="truncate">{children}</span>}
      {onRemove && (
        <button type="button" aria-label={removeLabel} onClick={onRemove} className="rounded-full p-0.5 text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700">
          <X className="h-3 w-3" />
        </button>
      )}
    </span>
  )
}
