import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { deriveTabMeta, useOptionalTabStoreApi, useTabStore, type RouteDef } from '@uniops/shell'
import { PageActivityContext, SheetLayerContext } from '@/platform-ui'
import { tabSession, useTabSession } from './tabSession'

const ALWAYS = { active: true }

/** Says to a page (and what it opens) whether its app tab is the active one (user 2026-11-26): the shell keeps page tabs
 *  mounted; one that is not shown must not tick. Sales-side, over the shell's store, with no change to the shell: inside a tab the
 *  location is the tab's own path, so its key is derived as the shell derives it. Outside a tab store (embedded): always active.
 *  It also gives the page its sheet layer (user 2026-11-27, "a drawer belongs to its tab"). */
export function TabActivity({ routes, children }: { routes: RouteDef[]; children: ReactNode }) {
  return useOptionalTabStoreApi()
    ? <InTab routes={routes}>{children}</InTab>
    : <PageActivityContext.Provider value={ALWAYS}>{children}</PageActivityContext.Provider>
}

function InTab({ routes, children }: { routes: RouteDef[]; children: ReactNode }) {
  const { pathname } = useLocation()
  const key = useMemo(() => deriveTabMeta(routes, pathname)?.key, [routes, pathname])
  const active = useTabStore((s) => !key || s.activeKey === key)
  const refreshTick = useTabSession((s) => (key ? s.ticks[key] ?? 0 : 0))  // its tab's Refresh (the tab bar's menu)
  const value = useMemo(() => ({ active, refreshTick }), [active, refreshTick])
  const [layer, setLayer] = useState<HTMLElement | null>(null)
  return (
    <PageActivityContext.Provider value={value}>
      <SheetLayerContext.Provider value={layer}>
        <TabFocus active={active} layer={layer}>{children}</TabFocus>
        {key && <SheetLayer tabKey={key} onLayer={setLayer} />}
      </SheetLayerContext.Provider>
    </PageActivityContext.Provider>
  )
}

/** The tab's sheet layer: over the content area only (the tab bar stays above, usable), and inside the tab's own element
 *  so it hides with the tab — a sheet in it stays open, with its record, inner tab, scroll and unsaved input, and shows
 *  again when the tab does. Says whether a sheet is open in it (the tab bar's dot). */
function SheetLayer({ tabKey, onLayer }: { tabKey: string; onLayer: (el: HTMLElement | null) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState<{ top: number; left: number; width: number; height: number } | null>(null)
  useLayoutEffect(() => {
    const el = ref.current
    const main = el?.closest('main')
    if (!el || !main) return
    const place = () => { const r = main.getBoundingClientRect(); setBox({ top: r.top, left: r.left, width: r.width, height: r.height }) }
    place()
    const ro = new ResizeObserver(place)
    ro.observe(main)
    window.addEventListener('resize', place)
    onLayer(el)
    const say = () => tabSession.sheet(tabKey, el.childElementCount > 0)
    const mo = new MutationObserver(say)
    mo.observe(el, { childList: true })
    return () => { ro.disconnect(); mo.disconnect(); window.removeEventListener('resize', place); onLayer(null); tabSession.sheet(tabKey, false) }
  }, [onLayer, tabKey])
  return <div ref={ref} data-sheet-layer className="pointer-events-none fixed z-30"
    style={box ? { top: box.top, left: box.left, width: box.width, height: box.height } : { inset: 0 }} />
}

/** Focus follows the tab: a tab that is shown again gets back the focus it had (inside its sheet, where it was); a tab
 *  shown for the first time takes the focus on its page (its heading) — never left on the hidden tab. */
function TabFocus({ active, layer, children }: { active: boolean; layer: HTMLElement | null; children: ReactNode }) {
  const page = useRef<HTMLDivElement>(null)
  const last = useRef<HTMLElement | null>(null)
  useEffect(() => {
    const keep = (e: FocusEvent) => { if (e.target instanceof HTMLElement) last.current = e.target }
    const roots = [page.current, layer].filter(Boolean) as HTMLElement[]
    roots.forEach((r) => r.addEventListener('focusin', keep))
    return () => roots.forEach((r) => r.removeEventListener('focusin', keep))
  }, [layer])
  const wasActive = useRef(false)  // a tab opened from a reference is shown at once: its page takes the focus too
  useEffect(() => {
    const shown = active && !wasActive.current
    wasActive.current = active
    if (!shown) return
    // Lost: on nothing, or still on the tab just hidden (a browser moves it to the page then; some do not).
    const a = document.activeElement
    const lost = !a || a === document.body || !!a.closest('[hidden]')
    if (!lost) return  // the person put the focus somewhere (the tab bar, a menu): left there
    requestAnimationFrame(() => {
      if (!page.current || page.current.closest('[hidden]')) return  // another tab was shown meanwhile: it has the focus
      const back = last.current && last.current.isConnected && !last.current.closest('[hidden]') ? last.current : null
      const target = back ?? page.current?.querySelector<HTMLElement>('h1, [data-page-focus]') ?? null
      if (target) { if (!target.hasAttribute('tabindex') && !back) target.setAttribute('tabindex', '-1'); target.focus({ preventScroll: true }) }
    })
  }, [active])
  return <div ref={page} className="contents">{children}</div>
}
