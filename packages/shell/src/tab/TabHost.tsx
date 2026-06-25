import { useTabStore } from './TabStoreContext'
import { RouteRenderer } from './RouteRenderer'
import type { RouteDef } from './types'

interface TabHostProps {
  routes: RouteDef[]
  /**
   * Class applied to the content wrapper around each page tab — use it to give
   * pages their shared container (e.g. `mx-auto max-w-[1440px] p-6`). Iframe
   * tabs are always full-bleed and ignore this.
   */
  pageClassName?: string
}

export function TabHost({ routes, pageClassName }: TabHostProps) {
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
              // Rendered via the single shared BrowserRouter using <Routes location>,
              // which pins this tab to its own path independent of the browser URL
              // without nesting a Router — this preserves all app-level React context
              // (QueryClientProvider, zustand stores, etc.) inside the tab.
              <div className={pageClassName}>
                <RouteRenderer routes={routes} path={t.path!} />
              </div>
            )}
          </div>
        ))}
    </div>
  )
}
