import { useTabStore } from './TabStoreContext'
import { RouteRenderer } from './RouteRenderer'
import type { RouteDef } from './types'

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
              // Rendered via the single shared BrowserRouter using <Routes location>,
              // which pins this tab to its own path independent of the browser URL
              // without nesting a Router — this preserves all app-level React context
              // (QueryClientProvider, zustand stores, etc.) inside the tab.
              <RouteRenderer routes={routes} path={t.path!} />
            )}
          </div>
        ))}
    </div>
  )
}
