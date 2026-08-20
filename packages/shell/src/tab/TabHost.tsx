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

  const mounted = tabs.filter((t) => alive.includes(t.key))
  // A tab bar reads `tabs` while this host reads `alive` — when the two
  // disagree (or `activeKey` names a tab that is not mounted) the result is a
  // blank content area with a perfectly normal-looking tab bar and no console
  // output. Surface the store state instead of rendering nothing, so the
  // mismatch is diagnosable on sight rather than by bisecting the store.
  if (mounted.length === 0 || !mounted.some((t) => t.key === activeKey)) {
    return (
      <div className="p-6 text-sm text-danger-600">
        <p className="font-medium">No tab is mounted for the active key.</p>
        <p className="mt-2 font-mono text-xs text-neutral-600">
          activeKey={JSON.stringify(activeKey)} · alive={JSON.stringify(alive)} ·
          tabKeys={JSON.stringify(tabs.map((t) => t.key))}
        </p>
      </div>
    )
  }

  return (
    // `absolute inset-0` rather than `h-full`: the host must fill its parent
    // even when that parent's height comes from flex growth rather than an
    // explicit height. `height:100%` against a flex-sized ancestor can resolve
    // to 0, and because every tab below is itself `absolute inset-0`, a 0-high
    // host renders the whole page into a zero-pixel box — present in the DOM,
    // invisible on screen, with no error anywhere. The parent must be
    // positioned (`relative`), which is the documented contract for this host.
    <div className="absolute inset-0">
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
