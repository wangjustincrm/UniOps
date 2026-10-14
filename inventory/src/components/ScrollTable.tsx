// A list table whose header stays in view while its rows scroll.
//
// `position: sticky` sticks to the NEAREST scrolling ancestor. The old
// wrappers were `overflow-x-auto`, which already makes them a scroll
// container — so a sticky header stuck to a box that never scrolled
// vertically and "did nothing" once the page scrolled. Here the wrapper is
// itself the vertical (and horizontal) scroller, capped at roughly one
// screen, and the <thead> sticks to its top.
//
// Usage:
//   <ScrollTable>
//     <table className="min-w-full text-sm">
//       <thead className={stickyHeadCls}>…</thead>
//       …
//   </ScrollTable>
import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/** Height cap: the viewport minus the app header, tab bar, page padding and
 *  pager — erring small so the whole box fits once the page is scrolled to it. */
export const scrollTableMaxH = 'max-h-[calc(100vh-12rem)]'

export function ScrollTable({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div {...rest}
      className={cn('overflow-auto rounded-lg border border-neutral-200 bg-white', scrollTableMaxH, className)}>
      {children}
    </div>
  )
}

/** For the <thead> of a ScrollTable: the whole header block (every row, every
 *  wrapped line) sticks together. Opaque background so rows don't show
 *  through; the inset shadow stands in for the bottom border, which
 *  border-collapse would otherwise leave behind with the scrolled rows. */
export const stickyHeadCls =
  'sticky top-0 z-10 bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500 shadow-[inset_0_-1px_0_var(--color-neutral-200)]'
