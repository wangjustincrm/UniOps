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
//       <THead className={stickyHeadCls}>…</THead>
//       …
//   </ScrollTable>
import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'
import { cardClass } from '@uniops/shell'

/** Height cap: the viewport minus the app header, tab bar, page padding and
 *  pager — erring small so the whole box fits once the page is scrolled to it. */
export const scrollTableMaxH = 'max-h-[calc(100vh-12rem)]'

export function ScrollTable({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div {...rest}
      className={cn(cn(cardClass({ inset: true }), 'overflow-auto'), scrollTableMaxH, className)}>
      {children}
    </div>
  )
}

/** For the THead of a ScrollTable. The Shell THead already sticks the whole
 *  header block, is opaque and draws its bottom line (an inset shadow, since
 *  border-collapse would leave a border behind with the scrolled rows); this
 *  only sets the header text. */
export const stickyHeadCls = 'text-xs text-fg-muted'
