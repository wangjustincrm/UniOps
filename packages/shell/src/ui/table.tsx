/**
 * The UniOps table. Every list and grid renders through these parts, so the
 * header style, row height, hover/selection states and the locked header are
 * the same on every screen (rule: every list sorts and locks its header).
 *
 *   <TableScroll>                       // box: header sticks inside the box
 *     <Table>
 *       <THead><Tr><SortTh …/><Th align="right">Amount</Th></Tr></THead>
 *       <TBody>{rows.map(r => <Tr key={r.id} onClick={…}><Td>…</Td></Tr>)}</TBody>
 *     </Table>
 *   </TableScroll>
 *
 * scroll="page" lets a narrow table scroll with the page instead; the header
 * then sticks at `stickyTop` (e.g. below a filter bar).
 */
import React, { createContext, useContext } from 'react'
import { cn } from '../lib/cn'

type Density = 'compact' | 'normal'
const DensityCtx = createContext<Density>('normal')

export interface TableScrollProps {
  children: React.ReactNode
  className?: string
  /** box (default): own scroll area with a max height. page: scrolls with the page. */
  scroll?: 'box' | 'page'
  /** Tailwind max-height class for scroll="box". */
  maxHeight?: string
  /** No outer border/radius, for a table that sits inside a Card. */
  bare?: boolean
}

export function TableScroll({ children, className, scroll = 'box', maxHeight = 'max-h-[calc(100vh-15rem)]', bare }: TableScrollProps) {
  return (
    <div
      className={cn(
        !bare && 'rounded-xl border border-line-subtle bg-surface',
        scroll === 'box' ? cn('overflow-auto', maxHeight) : 'overflow-x-clip',
        className,
      )}
    >
      {children}
    </div>
  )
}

export interface TableProps extends React.TableHTMLAttributes<HTMLTableElement> {
  density?: Density
}

export function Table({ density = 'normal', className, children, ...props }: TableProps) {
  return (
    <DensityCtx.Provider value={density}>
      <table className={cn('w-full border-collapse text-sm', className)} {...props}>{children}</table>
    </DensityCtx.Provider>
  )
}

export function THead({ className, children, stickyTop, ...props }: React.HTMLAttributes<HTMLTableSectionElement> & { stickyTop?: string }) {
  return (
    <thead
      className={cn('sticky z-10 bg-surface-subtle shadow-[inset_0_-1px_0_var(--color-line)]', stickyTop ?? 'top-0', className)}
      {...props}
    >
      {children}
    </thead>
  )
}

export function TBody({ className, children, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn('divide-y divide-line-subtle', className)} {...props}>{children}</tbody>
}

export function TFoot({ className, children, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <tfoot className={cn('bg-surface-subtle font-medium shadow-[inset_0_1px_0_var(--color-line)]', className)} {...props}>
      {children}
    </tfoot>
  )
}

export interface TrProps extends React.HTMLAttributes<HTMLTableRowElement> {
  selected?: boolean
  /** Rows that open something on click get hover + pointer. Implied by onClick. */
  interactive?: boolean
  muted?: boolean
}

export const Tr = React.forwardRef<HTMLTableRowElement, TrProps>(function Tr(
  { selected, interactive, muted, className, children, ...props }, ref,
) {
  const clickable = interactive ?? !!props.onClick
  return (
    <tr
      ref={ref}
      aria-selected={selected || undefined}
      className={cn(
        'transition-colors duration-100',
        clickable && 'cursor-pointer hover:bg-surface-subtle',
        selected && 'bg-accent-soft hover:bg-accent-soft',
        muted && 'text-fg-muted',
        className,
      )}
      {...props}
    >
      {children}
    </tr>
  )
})

type Align = 'left' | 'right' | 'center'
const alignClass = (a?: Align) => (a === 'right' ? 'text-right' : a === 'center' ? 'text-center' : 'text-left')

/** Header cell classes, shared with SortTh. Sentence case, quiet, one size. */
export function thClass(density: Density = 'normal', align?: Align) {
  return cn(
    'whitespace-nowrap text-xs font-medium text-fg-muted align-bottom',
    density === 'compact' ? 'px-3 py-2' : 'px-4 py-2.5',
    alignClass(align),
  )
}

export interface ThProps extends React.ThHTMLAttributes<HTMLTableCellElement> {
  align?: Align
}

export const Th = React.forwardRef<HTMLTableCellElement, ThProps>(function Th({ align, className, children, ...props }, ref) {
  const density = useContext(DensityCtx)
  return <th ref={ref} scope="col" className={cn(thClass(density, align), className)} {...props}>{children}</th>
})

export interface TdProps extends React.TdHTMLAttributes<HTMLTableCellElement> {
  align?: Align
  /** Figures: tabular, right-aligned, mono. */
  numeric?: boolean
  /** Secondary text in the row (dates, codes, meta). */
  muted?: boolean
}

export const Td = React.forwardRef<HTMLTableCellElement, TdProps>(function Td(
  { align, numeric, muted, className, children, ...props }, ref,
) {
  const density = useContext(DensityCtx)
  return (
    <td
      ref={ref}
      className={cn(
        density === 'compact' ? 'px-3 py-2' : 'px-4 py-3',
        alignClass(numeric ? 'right' : align),
        numeric && 'whitespace-nowrap font-mono tabular-nums',
        muted ? 'text-fg-muted' : 'text-fg',
        className,
      )}
      {...props}
    >
      {children}
    </td>
  )
})

/** One full-width row for loading / empty / error states inside TBody. */
export function TableMessage({ colSpan, children, className }: { colSpan: number; children: React.ReactNode; className?: string }) {
  return (
    <tr>
      <td colSpan={colSpan} className={cn('px-4 py-10 text-center text-sm text-fg-muted', className)}>{children}</td>
    </tr>
  )
}

export function useTableDensity() { return useContext(DensityCtx) }
