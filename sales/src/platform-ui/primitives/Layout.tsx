import { MoreHorizontal } from 'lucide-react'
import { useRef, type CSSProperties, type ElementType, type ReactNode } from 'react'
import { cn } from '../lib/cn'
import { Button } from './shell'
import { DropdownMenu, type MenuItem } from './DropdownMenu'
import { useElementWidth } from './useResponsive'

// Responsive layout (the user, 2026-11-13: "userfriendly and responsive app").
// Most of what the module shows lives in a Sheet, a panel or a card narrower than the
// window, so a layout reacts to the width of its own box (a container query, or
// a measured width), not to the window's. These are the few shapes every page
// needs; pages use them instead of a hand-made flex / grid that only fits a desktop.

type BoxProps = { children?: ReactNode; className?: string; as?: ElementType; 'aria-label'?: string; style?: CSSProperties }

/** A box its contents can react to (a Tailwind container: `@sm:…`, `@lg:…` inside it
 *  follow this box's width, not the window's). `name` → `@sm/<name>:…`. */
export function Responsive({ children, className, as: As = 'div', name, ...rest }: BoxProps & { name?: string }) {
  return (
    <As data-responsive className={cn(name ? `@container/${name}` : '@container', 'min-w-0', className)} {...rest}>
      {children}
    </As>
  )
}

const GAP = { 1: 'gap-1', 2: 'gap-2', 3: 'gap-3', 4: 'gap-4', 6: 'gap-6' } as const
export type Gap = keyof typeof GAP

/** Items in as many columns as fit, each at least `min` wide (one column on a phone):
 *  tiles, cards, form fields. Never wider than its box. */
export function AutoGrid({ children, className, as: As = 'div', min = '12rem', gap = 3, fill, style, ...rest }:
  BoxProps & { min?: string; gap?: Gap; fill?: boolean }) {
  return (
    <As data-auto-grid className={cn('grid min-w-0', GAP[gap], className)}
      style={{ gridTemplateColumns: `repeat(${fill ? 'auto-fill' : 'auto-fit'}, minmax(min(${min}, 100%), 1fr))`, ...style }} {...rest}>
      {children}
    </As>
  )
}

/** Items in a row that wraps when it runs out of room (badges, buttons, chips, a label and
 *  its value): never pushes the page sideways. */
export function Cluster({ children, className, as: As = 'div', gap = 2, align = 'center', justify, ...rest }:
  BoxProps & { gap?: Gap; align?: 'start' | 'center' | 'baseline' | 'end'; justify?: 'start' | 'between' | 'end' }) {
  return (
    <As data-cluster className={cn('flex min-w-0 flex-wrap', GAP[gap], {
      start: 'items-start', center: 'items-center', baseline: 'items-baseline', end: 'items-end',
    }[align], justify && { start: 'justify-start', between: 'justify-between', end: 'justify-end' }[justify], className)} {...rest}>
      {children}
    </As>
  )
}

export interface ToolbarItem {
  /** Its name: the More menu's item, and the key. */
  label: string
  /** What the bar shows (a button, a control); without it, a small secondary Button. */
  node?: ReactNode
  icon?: ReactNode
  /** What the More menu's item does (required for an item that can move there). */
  onSelect?: () => void | Promise<unknown>
  disabled?: boolean
  /** Moves into the More menu when the bar is narrower than `collapseBelow`. */
  collapse?: boolean
  opensDialog?: boolean
}

/** A row of actions (role=toolbar) that fits its box: it wraps, and the items marked
 *  `collapse` go into a "More" menu when the bar is narrow (a phone, a narrow sheet),
 *  joined by `more` — the items that are always in the menu. */
export function Toolbar({ label, items, more = [], collapseBelow = 480, className, end }: {
  label: string; items: ToolbarItem[]; more?: MenuItem[]; collapseBelow?: number; className?: string
  /** Pinned to the bar's right end (e.g. the main action). */
  end?: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const width = useElementWidth(ref)
  const narrow = width !== null && width > 0 && width < collapseBelow
  const moves = (it: ToolbarItem) => narrow && !!it.collapse && !!it.onSelect
  const shown = items.filter((it) => !moves(it))
  const menu: MenuItem[] = [
    ...items.filter(moves).map((it) => ({ label: it.label, icon: it.icon, onSelect: it.onSelect!, disabled: it.disabled, opensDialog: it.opensDialog })),
    ...more.map((m, i) => (i === 0 && items.some(moves) ? { ...m, separated: true } : m)),
  ]
  return (
    <div ref={ref} role="toolbar" aria-label={label} data-narrow={narrow || undefined}
      className={cn('flex min-w-0 flex-wrap items-center gap-2', className)}>
      {shown.map((it) => (
        <span key={it.label} className="contents">
          {it.node ?? (
            <Button variant="secondary" size="sm" disabled={it.disabled} onClick={() => { void it.onSelect?.() }}>
              {it.icon}{it.label}
            </Button>
          )}
        </span>
      ))}
      {menu.length > 0 && (
        <DropdownMenu label={`${label}: more`} align="end" items={menu}
          trigger={<Button variant="secondary" size="sm" aria-label="More actions"><MoreHorizontal className="h-4 w-4" aria-hidden />More</Button>} />
      )}
      {end && <div className="ml-auto flex items-center gap-2">{end}</div>}
    </div>
  )
}

/** A form's or a record's main buttons (Save / Cancel, Confirm): on a phone a bar stuck to
 *  the bottom of the scrolling sheet, full-width buttons in reach of a thumb; from `sm` up,
 *  a plain row at the end. */
export function ActionBar({ children, className, label = 'Form actions' }: { children: ReactNode; className?: string; label?: string }) {
  return (
    <div role="group" aria-label={label} data-action-bar
      className={cn('sticky bottom-0 z-[2] flex flex-col-reverse gap-2 border-t border-neutral-200 bg-white/95 py-2',
        // the sheet's own bottom padding is below a sticky bar: a white band covers it, so nothing shows under the bar
        'pb-[max(0.5rem,env(safe-area-inset-bottom))] shadow-[0_1.5rem_0_0_var(--color-white)] backdrop-blur [&>*]:w-full',
        'sm:static sm:flex-row sm:justify-end sm:border-0 sm:bg-transparent sm:p-0 sm:shadow-none sm:backdrop-blur-none sm:[&>*]:w-auto',
        className)}>
      {children}
    </div>
  )
}
