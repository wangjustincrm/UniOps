import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { create } from 'zustand'
import { RefreshCw } from 'lucide-react'
import { deriveTabMeta, useTabStore, useTabStoreApi } from '@uniops/shell'
import type { RouteDef } from '@uniops/shell'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

// ISS-0131: keep-alive tabs never remount and refetchOnWindowFocus is off, so
// a list or detail tab kept showing what it read when it was opened — a PO
// still "Issued, 0 / 10 received" after its GR was collected in another tab,
// a manager's PR list without the PR just submitted to them. F5 is no answer:
// it closes every other tab (UI-TAB-003).
//
// One mechanism for every list/detail route instead of an edit per page: each
// such tab gets its own QueryClient over the SAME query and mutation caches
// (data stays shared), which only notes the query hashes this tab's
// components ask for. That is what lets "re-read this tab" mean this tab:
//   - returning to the tab refetches its stale queries;
//   - the Refresh button beside the tab bar refetches all of them.
// Create/edit/confirm routes are deliberately not wrapped (AppLayout): a
// refetch there could re-seed a form from the server and wipe what was typed.

type Refresh = (opts?: { onlyStale?: boolean }) => Promise<unknown>

const useTabRefreshRegistry = create<{
  fns: Record<string, Refresh>
  set: (key: string, fn: Refresh | null) => void
}>((set) => ({
  fns: {},
  set: (key, fn) => set((s) => {
    const fns = { ...s.fns }
    if (fn) fns[key] = fn; else delete fns[key]
    return { fns }
  }),
}))

export function createTabClient(root: QueryClient) {
  const client = new QueryClient({
    queryCache: root.getQueryCache(),
    mutationCache: root.getMutationCache(),
    defaultOptions: root.getDefaultOptions(),
  })
  const seen = new Set<string>()
  // Every useQuery / useQueries observer defaults its options through its
  // client — the one place a query can be attributed to this tab.
  const base = client.defaultQueryOptions.bind(client)
  client.defaultQueryOptions = ((options: Parameters<typeof base>[0]) => {
    const o = base(options)
    seen.add(o.queryHash)
    return o
  }) as typeof client.defaultQueryOptions
  const refresh: Refresh = ({ onlyStale = false } = {}) =>
    client.refetchQueries({
      type: 'active',
      ...(onlyStale ? { stale: true } : {}),
      predicate: (q) => seen.has(q.queryHash),
    })
  return { client, refresh }
}

export function TabQueryScope({ routes, children }: { routes: RouteDef[]; children: ReactNode }) {
  const root = useQueryClient()
  const [{ client, refresh }] = useState(() => createTabClient(root))
  // Inside the tab host, the location is this tab's own path.
  const { pathname } = useLocation()
  const tabKey = useMemo(() => deriveTabMeta(routes, pathname)?.key ?? null, [routes, pathname])
  const activeKey = useTabStore((s) => s.activeKey)
  const api = useTabStoreApi()
  const setRefresh = useTabRefreshRegistry((s) => s.set)

  useEffect(() => {
    if (!tabKey) return
    setRefresh(tabKey, refresh)
    return () => setRefresh(tabKey, null)
  }, [tabKey, refresh, setRefresh])

  // Coming back to this tab: re-read what has gone stale. Not on first open
  // (the queries are fetching anyway), and not for a tab marked dirty.
  const prevActive = useRef(activeKey)
  useEffect(() => {
    const was = prevActive.current
    prevActive.current = activeKey
    if (!tabKey || activeKey !== tabKey || was === tabKey) return
    if (api.getState().tabs.find((t) => t.key === tabKey)?.dirty) return
    void refresh({ onlyStale: true })
  }, [activeKey, tabKey, api, refresh])

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

/** Refresh for the active tab — shown only when that tab is a TabQueryScope. */
export function TabRefreshButton() {
  const activeKey = useTabStore((s) => s.activeKey)
  const fn = useTabRefreshRegistry((s) => (activeKey ? s.fns[activeKey] : undefined))
  const [busy, setBusy] = useState(false)
  const onClick = useCallback(async () => {
    if (!fn) return
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
  }, [fn])
  if (!fn) return null
  return (
    <div className="flex h-10 shrink-0 items-center border-b border-l border-neutral-200 bg-white px-2">
      <Button
        variant="ghost"
        size="sm"
        onClick={onClick}
        disabled={busy}
        aria-label="Refresh this tab"
        title="Re-read this tab's data (other tabs stay open)"
        className="gap-1.5 text-neutral-500"
      >
        <RefreshCw className={cn('h-4 w-4', busy && 'animate-spin')} />
        <span className="hidden sm:inline">Refresh</span>
      </Button>
    </div>
  )
}
