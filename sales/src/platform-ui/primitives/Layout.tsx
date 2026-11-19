import { MoreHorizontal } from 'lucide-react'
import { useRef, type CSSProperties, type ElementType, type HTMLAttributes, type ReactNode } from 'react'
import { cn } from '../lib/cn'
import { Button } from './shell'
import { DropdownMenu, type MenuItem } from './DropdownMenu'
import { useBreakpoint, useElementWidth } from './useResponsive'
import { TONE, type TextTone } from './textStyle'

// Responsive layout (the user, 2026-11-13: "userfriendly and responsive app").
// Most of what the module shows lives in a Sheet, a panel or a card narrower than the
// window, so a layout reacts to the width of its own box (a container query, or
// a measured width), not to the window's. These are the few shapes every page
// needs; pages use them instead of a hand-made flex / grid that only fits a desktop.

// A box passes its element's own attributes through (role, id, aria-*, data-*, events).
type BoxProps = Omit<HTMLAttributes<HTMLElement>, 'className' | 'style' | 'children'> & { children?: ReactNode; className?: string; as?: ElementType; style?: CSSProperties }

/** A box its contents can react to (a Tailwind container: `@sm:…`, `@lg:…` inside it
 *  follow this box's width, not the window's). `name` → `@sm/<name>:…`. */
export function Responsive({ children, className, as: As = 'div', name, ...rest }: BoxProps & { name?: string }) {
  return (
    <As data-responsive className={cn(name ? `@container/${name}` : '@container', 'min-w-0', className)} {...rest}>
      {children}
    </As>
  )
}

const GAP = { 0: 'gap-0', 0.5: 'gap-0.5', 1: 'gap-1', 1.5: 'gap-1.5', 2: 'gap-2', 3: 'gap-3', 4: 'gap-4', 6: 'gap-6' } as const
export type Gap = keyof typeof GAP

// The text a layout box sets for what is in it (size and tone), as Text does for one run of text.
const BOX_SIZE = { xs: 'text-xs', sm: 'text-sm' } as const
const ALIGN = { start: 'items-start', center: 'items-center', baseline: 'items-baseline', end: 'items-end', stretch: 'items-stretch' } as const
const JUSTIFY = { start: 'justify-start', between: 'justify-between', end: 'justify-end', center: 'justify-center' } as const
type BoxText = { size?: keyof typeof BOX_SIZE; tone?: TextTone }

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

// Fixed column counts per width (Tailwind needs every class written out whole, so each one is listed).
const COLS = { 1: 'grid-cols-1', 2: 'grid-cols-2', 3: 'grid-cols-3', 4: 'grid-cols-4', 5: 'grid-cols-5', 6: 'grid-cols-6' } as const
const SM_COLS = { 1: 'sm:grid-cols-1', 2: 'sm:grid-cols-2', 3: 'sm:grid-cols-3', 4: 'sm:grid-cols-4', 5: 'sm:grid-cols-5', 6: 'sm:grid-cols-6' } as const
const MD_COLS = { 1: 'md:grid-cols-1', 2: 'md:grid-cols-2', 3: 'md:grid-cols-3', 4: 'md:grid-cols-4', 5: 'md:grid-cols-5', 6: 'md:grid-cols-6' } as const
const LG_COLS = { 1: 'lg:grid-cols-1', 2: 'lg:grid-cols-2', 3: 'lg:grid-cols-3', 4: 'lg:grid-cols-4', 5: 'lg:grid-cols-5', 6: 'lg:grid-cols-6' } as const
const GAP_X = { 2: 'gap-x-2', 3: 'gap-x-3', 4: 'gap-x-4', 6: 'gap-x-6' } as const
const GAP_Y = { 1: 'gap-y-1', 2: 'gap-y-2', 3: 'gap-y-3', 4: 'gap-y-4' } as const
type Cols = keyof typeof COLS

/** A set number of columns, from the window's breakpoints (stat tiles, a details grid): `cols` on a phone,
 *  then `sm` / `md` / `lg`. `scrollOnPhone`: on a phone one row that scrolls sideways instead (the table below
 *  keeps the screen). AutoGrid decides the count itself; this is for when the count is the design. */
export function Columns({ children, className, as: As = 'div', cols = 1, sm, md, lg, gap = 3, gapX, gapY, scrollOnPhone, ...rest }:
  BoxProps & { cols?: Cols; sm?: Cols; md?: Cols; lg?: Cols; gap?: Gap; gapX?: keyof typeof GAP_X; gapY?: keyof typeof GAP_Y; scrollOnPhone?: boolean }) {
  return (
    <As data-columns className={cn(scrollOnPhone ? 'flex overflow-x-auto pb-1 sm:grid sm:overflow-visible sm:pb-0' : cn('grid', COLS[cols]),
      'min-w-0', GAP[gap], gapX && GAP_X[gapX], gapY && GAP_Y[gapY], sm && SM_COLS[sm], md && MD_COLS[md], lg && LG_COLS[lg], className)} {...rest}>
      {children}
    </As>
  )
}

/** Items in a row that wraps when it runs out of room (badges, buttons, chips, a label and
 *  its value): never pushes the page sideways. `inline`: inside a line of text. */
export function Cluster({ children, className, as: As = 'div', gap = 2, align = 'center', justify, inline, size, tone, ...rest }:
  BoxProps & BoxText & { gap?: Gap; align?: keyof typeof ALIGN; justify?: keyof typeof JUSTIFY; inline?: boolean }) {
  return (
    <As data-cluster className={cn(inline ? 'inline-flex' : 'flex', 'min-w-0 flex-wrap', GAP[gap], ALIGN[align], justify && JUSTIFY[justify],
      size && BOX_SIZE[size], tone && TONE[tone], className)} {...rest}>
      {children}
    </As>
  )
}

/** Items one under the other, with a set gap (a panel's parts, a label over its value, a list of lines). */
export function Stack({ children, className, as: As = 'div', gap = 2, align, justify, inline, size, tone, ...rest }:
  BoxProps & BoxText & { gap?: Gap; align?: keyof typeof ALIGN; justify?: keyof typeof JUSTIFY; inline?: boolean }) {
  return (
    <As data-stack className={cn(inline ? 'inline-flex' : 'flex', 'min-w-0 flex-col', GAP[gap], align && ALIGN[align], justify && JUSTIFY[justify],
      size && BOX_SIZE[size], tone && TONE[tone], className)} {...rest}>
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
  // Until it is measured, a phone starts collapsed (no jump from wide to narrow on the first paint).
  const phone = useBreakpoint() === 'base'
  const narrow = !width ? phone : width < collapseBelow
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
