import { act, useEffect, useRef } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { useTabStore } from './TabStoreContext'
import { RouteRenderer } from './RouteRenderer'
import type { RouteDef, TabMeta } from './types'

/**
 * Mounts a page tab's MemoryRouter tree in its own, fully separate React root.
 *
 * Why: react-router v7 throws "You cannot render a <Router> inside another
 * <Router>" if a MemoryRouter is nested (even via context, not DOM position)
 * under the app's outer BrowserRouter — useInRouterContext() checks React
 * context, so this happens regardless of where in the tree the MemoryRouter
 * sits. A separate createRoot() has no shared context with the outer tree,
 * so the per-tab MemoryRouter is free to provide its own pinned-location
 * routing context for useParams/useNavigate/useLocation inside the page.
 */
function TabRootMount({ tab, routes }: { tab: TabMeta; routes: RouteDef[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const rootRef = useRef<Root | null>(null)

  useEffect(() => {
    if (containerRef.current && !rootRef.current) {
      rootRef.current = createRoot(containerRef.current)
    }
    return () => {
      rootRef.current?.unmount()
      rootRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!rootRef.current) return
    act(() => {
      rootRef.current!.render(
        <MemoryRouter initialEntries={[tab.path!]}>
          <RouteRenderer routes={routes} path={tab.path!} />
        </MemoryRouter>,
      )
    })
  }, [tab.path, routes])

  return <div ref={containerRef} className="h-full" />
}

export function TabHost({ routes }: { routes: RouteDef[] }) {
  const tabs = useTabStore((s) => s.tabs)
  const activeKey = useTabStore((s) => s.activeKey)
  const alive = useTabStore((s) => s.alive)

  return (
    <div className="relative h-full">
      {tabs
        .filter((t) => alive.includes(t.key))
        .map((t) => (
          <div
            key={t.key}
            hidden={t.key !== activeKey}
            className="absolute inset-0 overflow-y-auto"
          >
            {t.kind === 'iframe' ? (
              <iframe title={t.title} src={t.src} className="block h-full w-full border-0 bg-white" />
            ) : (
              // Isolated router per tab keeps the page mounted at its own path
              // regardless of the browser URL (which only drives `activeKey`).
              <TabRootMount tab={t} routes={routes} />
            )}
          </div>
        ))}
    </div>
  )
}
