/** MenuItem (rows in dropdowns, listboxes and action menus) and ChoiceCard. */
import React from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '../lib/cn'

export interface MenuItemProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** warning / danger colour the row (Return for revision, Reject). */
  tone?: 'default' | 'warning' | 'danger'
  /** Highlighted (current value / keyboard focus in a listbox). */
  active?: boolean
}

/** One row in a dropdown, search result list or action menu. Always type="button". */
export const MenuItem = React.forwardRef<HTMLButtonElement, MenuItemProps>(function MenuItem(
  { tone = 'default', active, className, children, type = 'button', ...props }, ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        'flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors duration-100',
        'focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50',
        tone === 'danger' ? 'text-danger-fg hover:bg-danger-soft focus-visible:bg-danger-soft'
          : tone === 'warning' ? 'text-warning-fg hover:bg-warning-soft focus-visible:bg-warning-soft'
          : cn('text-fg hover:bg-surface-subtle focus-visible:bg-surface-subtle', active && 'bg-accent-soft'),
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
})

export interface ChoiceCardProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  selected?: boolean
}

/** A selectable tile (pick a type, pick a path). aria-pressed reflects selection. */
export const ChoiceCard = React.forwardRef<HTMLButtonElement, ChoiceCardProps>(function ChoiceCard(
  { selected, className, children, type = 'button', ...props }, ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-pressed={selected}
      className={cn(
        'flex flex-col items-start gap-2 rounded-xl border bg-surface p-4 text-left transition-colors duration-150',
        'focus-visible:outline-none focus-visible:shadow-focus disabled:cursor-not-allowed disabled:opacity-50',
        selected ? 'border-accent bg-accent-soft shadow-[inset_0_0_0_1px_var(--color-accent)]' : 'border-line hover:border-line-strong',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
})

export interface DisclosureButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  open: boolean
  /** Chevron size; match the label's text size. */
  iconSize?: 'sm' | 'md'
}

/** Header that expands / collapses a section. Chevron first, aria-expanded, always type="button". */
export const DisclosureButton = React.forwardRef<HTMLButtonElement, DisclosureButtonProps>(function DisclosureButton(
  { open, iconSize = 'md', className, children, type = 'button', ...props }, ref,
) {
  const Icon = open ? ChevronDown : ChevronRight
  return (
    <button
      ref={ref}
      type={type}
      aria-expanded={open}
      className={cn(
        'flex items-center gap-2 text-left transition-colors duration-100',
        'focus-visible:outline-none focus-visible:shadow-focus disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    >
      <Icon className={cn('shrink-0', iconSize === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4')} aria-hidden />
      {children}
    </button>
  )
})
