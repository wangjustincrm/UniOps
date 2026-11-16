import { useCallback, useLayoutEffect, useState, useSyncExternalStore, type RefObject } from 'react'

// The hooks behind the responsive layout (Layout.tsx): the window's breakpoint and an element's width.

/** Tailwind's window breakpoints (px), smallest first. */
export const BREAKPOINTS = { sm: 640, md: 768, lg: 1024, xl: 1280 } as const
export type Breakpoint = 'base' | keyof typeof BREAKPOINTS

function subscribeMedia(query: string) {
  return (notify: () => void) => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
    const list = window.matchMedia(query)
    list.addEventListener('change', notify)
    return () => list.removeEventListener('change', notify)
  }
}

/** Whether the window matches a media query (false where there is no matchMedia: a test, a server). */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback((notify: () => void) => subscribeMedia(query)(notify), [query])
  return useSyncExternalStore(subscribe,
    () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches,
    () => false)
}

/** The window's breakpoint: 'base' under 640 px (a phone), then sm / md / lg / xl.
 *  For behaviour only (what to render); a look is a Tailwind class. */
export function useBreakpoint(): Breakpoint {
  const sm = useMediaQuery(`(min-width: ${BREAKPOINTS.sm}px)`)
  const md = useMediaQuery(`(min-width: ${BREAKPOINTS.md}px)`)
  const lg = useMediaQuery(`(min-width: ${BREAKPOINTS.lg}px)`)
  const xl = useMediaQuery(`(min-width: ${BREAKPOINTS.xl}px)`)
  return xl ? 'xl' : lg ? 'lg' : md ? 'md' : sm ? 'sm' : 'base'
}

/** The width of an element in px, kept current (ResizeObserver); null until measured. */
export function useElementWidth(ref: RefObject<HTMLElement | null>): number | null {
  const [width, setWidth] = useState<number | null>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => setWidth(el.getBoundingClientRect().width)
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref])
  return width
}

