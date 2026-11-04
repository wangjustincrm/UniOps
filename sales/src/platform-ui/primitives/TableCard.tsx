import { useEffect, useRef, useState, type ReactNode } from 'react'
import { cn } from '../lib/cn'
import { BusyBar } from './BusyBar'

/** The table's card (the user, 2026-11-03: "the footer always needs scrolling
 *  to reach"): the rows scroll inside it — sideways too — under a sticky header
 *  and above sticky totals; the status row is pinned to its bottom, outside the
 *  scroll, and stays on screen (sticky to the page's bottom when the page itself
 *  scrolls, on a phone). With `fit`, the card fills what is left of the view, or
 *  less: a short list shrinks to its rows. Columns that do not fit: a fade over
 *  the scroll area, and "More columns →" in a thin strip pinned with the status
 *  row, so it covers no heading and stays in reach; at the last column it takes you back. */
export function TableCard({ children, busy, fit, highlight, bar }: {
  children: ReactNode; busy?: boolean; fit?: 'viewport' | number; highlight?: ReadonlySet<string | number>; bar?: ReactNode
}) {
  const card = useRef<HTMLDivElement>(null)
  const ref = useRef<HTMLDivElement>(null)
  const [wide, setWide] = useState(false)
  const [atEnd, setAtEnd] = useState(false)
  const [fadeH, setFadeH] = useState(0)
  const maxHeight = useFitHeight(card, ref, fit)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const check = () => {
      setWide(el.scrollWidth - el.clientWidth > 4)
      setAtEnd(el.scrollWidth - el.clientWidth - el.scrollLeft <= 4)
      setFadeH(el.clientHeight)
    }
    const first = requestAnimationFrame(check)
    el.addEventListener('scroll', check, { passive: true })
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(check)
    ro?.observe(el)
    if (el.firstElementChild) ro?.observe(el.firstElementChild)
    return () => { cancelAnimationFrame(first); el.removeEventListener('scroll', check); ro?.disconnect() }
  }, [])
  // A refresh's new rows: brought into view, but only for a user still at the top.
  useEffect(() => {
    const el = ref.current
    if (!el || !highlight?.size || el.scrollTop > 8) return
    const row = el.querySelector('tr[data-highlight]') as HTMLElement | null
    if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' })
  }, [highlight])
  return (
    <div ref={card} data-table-card className="relative flex flex-col rounded-lg border border-neutral-200 bg-white">
      <div ref={ref} data-table-scroll className={cn('overflow-auto', bar ? 'rounded-t-lg' : 'rounded-lg')}
        style={maxHeight ? { maxHeight } : undefined}>{children}</div>
      <BusyBar active={!!busy} className="rounded-t-lg" />
      {wide && !atEnd && (
        <div aria-hidden className="pointer-events-none absolute right-0 top-0 w-10 bg-gradient-to-l from-white to-transparent"
          style={{ height: fadeH }} />
      )}
      {(wide || bar) && (
        <div className="sticky bottom-0 z-[3] rounded-b-lg bg-white">
          {wide && (
            <div className="flex justify-end border-t border-neutral-200 px-1">
              <button type="button" onClick={() => {
                const el = ref.current
                if (el) el.scrollBy({ left: atEnd ? -el.scrollWidth : el.clientWidth * 0.8, behavior: 'smooth' })
              }} className="rounded px-2 py-0.5 text-xs text-neutral-600 hover:text-neutral-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
                {atEnd ? '← First columns' : 'More columns →'}
              </button>
            </div>
          )}
          {bar}
        </div>
      )}
    </div>
  )
}

/** The rows' height so the card ends at the bottom of what scrolls the page
 *  (a keep-alive tab pane, a sheet's body; else the window), measured as if
 *  scrolled to the top: on a desktop the whole card is in view. On a short
 *  view (a phone) it keeps at least 60 % of the view and the page scrolls with
 *  the bar pinned to its bottom. */
function useFitHeight(card: React.RefObject<HTMLDivElement | null>, scroll: React.RefObject<HTMLDivElement | null>,
  fit: 'viewport' | number | undefined): number | undefined {
  const [h, setH] = useState<number | undefined>(undefined)
  useEffect(() => {
    if (fit !== 'viewport') return
    const el = card.current
    if (!el) return
    const parent = scrollParent(el)
    const measure = () => {
      const view = parent ? parent.clientHeight : window.innerHeight
      const parentTop = parent ? parent.getBoundingClientRect().top - parent.scrollTop : 0
      const top = (scroll.current ?? el).getBoundingClientRect().top - parentTop
      const below = el.getBoundingClientRect().bottom - (scroll.current ?? el).getBoundingClientRect().bottom  // the status row
      const pad = parent ? parseFloat(getComputedStyle(parent.firstElementChild ?? parent).paddingBottom) || 24 : 24
      setH(Math.max(Math.round(view - top - below - pad), Math.round(view * 0.6), 240))
    }
    const first = requestAnimationFrame(measure)
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    if (parent) ro?.observe(parent)
    if (el.parentElement) ro?.observe(el.parentElement)
    window.addEventListener('resize', measure)
    return () => { cancelAnimationFrame(first); ro?.disconnect(); window.removeEventListener('resize', measure) }
  }, [fit, card, scroll])
  return typeof fit === 'number' ? fit : fit === 'viewport' ? h : undefined
}

function scrollParent(el: HTMLElement): HTMLElement | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY
    if ((o === 'auto' || o === 'scroll') && p !== document.body) return p
  }
  return null
}
