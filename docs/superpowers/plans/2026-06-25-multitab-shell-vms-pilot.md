# Multi-Tab Shell — Tab Engine + VMS Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable keep-alive multi-tab (MDI) engine as a shared `@uniops/shell` workspace package, and wire it into the VMS app as the pilot to validate the architecture end-to-end.

**Architecture:** A framework-agnostic tab engine (zustand vanilla store + react-router `matchRoutes`) lives in `packages/shell`. Each alive tab renders its page inside its own `<Routes location={tabPath}>` pinned to that tab's path — so the page component stays mounted (keep-alive) and `useParams()` still works, regardless of the browser URL. The browser URL only controls which tab is *visible*. VMS keeps its existing Sidebar/Header chrome; we only replace its `<Outlet/>` with `TabBar` + `TabHost`, and add `TabRouterSync` to turn URL changes (from sidebar NavLinks) into tab open/activate.

**Tech Stack:** npm workspaces, React 19, react-router-dom v7, zustand v5, TypeScript 6 (bundler resolution), Vitest + @testing-library/react + jsdom for the shell package.

**Scope:** This is Phase 0 (partial: workspace bootstrap) + Phase 2 (tab engine + VMS pilot) of the design spec `docs/superpowers/specs/2026-06-25-uniops-multitab-shell-design.md`. The `@uniops/tokens` / `@uniops/ui` consolidation (Phase 1), rollout to OA/EPMS (Phase 3), and the Finance independent-deploy split (Phase 4) are deliberately **out of scope** here and become follow-on plans once the engine is proven in VMS.

---

## File Structure

**New shared package — `packages/shell/`:**
- `package.json`, `tsconfig.json`, `vitest.config.ts` — package config
- `src/index.ts` — public exports
- `src/tab/types.ts` — `TabMeta`, `TabKind`, `RouteDef`, `TabSpec`
- `src/tab/routeTable.ts` — `resolveRoute`, `deriveTabMeta` (path → tab identity)
- `src/tab/routeTable.test.ts`
- `src/tab/tabStore.ts` — `createTabStore` (zustand vanilla + persist + eviction)
- `src/tab/tabStore.test.ts`
- `src/tab/TabStoreContext.tsx` — React provider + `useTabStore` hook
- `src/tab/RouteRenderer.tsx` — pinned-location `<Routes>` per tab + error boundary
- `src/tab/TabHost.tsx` — keep-alive renderer (alive set, show/hide)
- `src/tab/TabHost.test.tsx` — keep-alive integration test (state retained across switch)
- `src/tab/TabBar.tsx` — tab strip UI (click/close/context menu)
- `src/tab/TabRouterSync.tsx` — URL ↔ tab sync effect
- `src/tab/useTabDirty.ts` — page hook to mark its tab dirty

**Workspace root:**
- `package.json` — adds `workspaces` array (NEW at repo root)

**VMS app (modified):**
- `vms/src/app/routes.tsx` — `vmsRoutes: RouteDef[]` (NEW)
- `vms/src/components/layout/AppLayout.tsx` — replace `<Outlet/>` with shell pieces
- `vms/src/App.tsx` — collapse child routes into a single `/*` → `AppLayout`
- `vms/package.json` — add `@uniops/shell` dependency

---

## Task 1: Workspace bootstrap + `@uniops/shell` skeleton

**Files:**
- Create: `package.json` (repo root)
- Create: `packages/shell/package.json`
- Create: `packages/shell/tsconfig.json`
- Create: `packages/shell/vitest.config.ts`
- Create: `packages/shell/src/index.ts`

- [ ] **Step 1: Create the workspace root `package.json`**

There is currently no root `package.json` (each app is standalone). Create one that registers the apps and the new package as workspaces. This does NOT change how apps build individually.

```json
{
  "name": "uniops-monorepo",
  "private": true,
  "version": "0.0.0",
  "workspaces": [
    "packages/*",
    "epms",
    "oa",
    "portal",
    "vms"
  ]
}
```

- [ ] **Step 2: Create `packages/shell/package.json`**

The package is consumed as TypeScript source (Vite transpiles it in each app). `exports` points at `src/index.ts`.

```json
{
  "name": "@uniops/shell",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "exports": {
    ".": "./src/index.ts"
  },
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "peerDependencies": {
    "react": "^19",
    "react-dom": "^19",
    "react-router-dom": "^7",
    "zustand": "^5"
  },
  "dependencies": {
    "clsx": "^2.1.1",
    "tailwind-merge": "^3.5.0",
    "lucide-react": "^1.14.0"
  },
  "devDependencies": {
    "@testing-library/react": "^16.1.0",
    "@testing-library/jest-dom": "^6.6.3",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "jsdom": "^25.0.1",
    "react": "^19.2.5",
    "react-dom": "^19.2.5",
    "react-router-dom": "^7.14.2",
    "typescript": "~6.0.2",
    "vitest": "^2.1.8",
    "zustand": "^5.0.12"
  }
}
```

- [ ] **Step 3: Create `packages/shell/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Create `packages/shell/vitest.config.ts`**

```ts
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
  },
})
```

- [ ] **Step 5: Create `packages/shell/src/test-setup.ts`**

```ts
import '@testing-library/jest-dom/vitest'
```

- [ ] **Step 6: Create a placeholder `packages/shell/src/index.ts`**

```ts
// Public exports — populated by later tasks.
export {}
```

- [ ] **Step 7: Install workspace deps**

Run: `npm install` (from repo root `c:/Project/uniops`)
Expected: creates a root `node_modules` with `@uniops/shell` symlinked; no errors.

- [ ] **Step 8: Verify vitest runs (no tests yet)**

Run: `npm run test --workspace @uniops/shell`
Expected: Vitest reports "No test files found" and exits 0 (this confirms the toolchain is wired).

- [ ] **Step 9: Commit**

```bash
git add package.json packages/shell
git commit -m "chore: bootstrap npm workspace + @uniops/shell skeleton"
```

---

## Task 2: Route table resolver (`routeTable.ts`)

**Files:**
- Create: `packages/shell/src/tab/types.ts`
- Create: `packages/shell/src/tab/routeTable.ts`
- Test: `packages/shell/src/tab/routeTable.test.ts`

- [ ] **Step 1: Create `types.ts`**

```ts
import type { ReactNode } from 'react'

export type TabKind = 'page' | 'iframe'

export interface TabMeta {
  /** Stable unique identity used for dedup. */
  key: string
  /** Label shown on the tab; may be updated at runtime. */
  title: string
  kind: TabKind
  /** Module-relative route for kind:'page' (e.g. '/visit/123'). */
  path?: string
  /** iframe src for kind:'iframe'. */
  src?: string
  /** Serializable lucide icon name (resolved at render time). */
  icon?: string
  /** Pinned tabs cannot be closed and are exempt from eviction. */
  pinned?: boolean
  /** Closable tabs show an × button. Pinned tabs are never closable. */
  closable: boolean
  /** Marked by pages with unsaved changes; guards close + eviction. */
  dirty?: boolean
}

/** How a route's tab identity (key) is computed. */
export interface TabSpec {
  title: string | ((params: Record<string, string>) => string)
  icon?: string
  /** 'static' → one tab per route; 'param' → one tab per param value. */
  keyStrategy: 'static' | 'param'
  /** Which path param distinguishes tabs when keyStrategy:'param'. */
  paramName?: string
  pinned?: boolean
  closable?: boolean
}

export interface RouteDef {
  /** react-router path pattern, e.g. '/visit/:visitId'. */
  path: string
  element: ReactNode
  /** Routes WITHOUT a tab spec (e.g. login) are not tabbable. */
  tab?: TabSpec
}
```

- [ ] **Step 2: Write the failing test `routeTable.test.ts`**

```ts
import { describe, it, expect } from 'vitest'
import { resolveRoute, deriveTabMeta } from './routeTable'
import type { RouteDef } from './types'

const routes: RouteDef[] = [
  { path: '/dashboard', element: null, tab: { title: 'Dashboard', icon: 'LayoutDashboard', keyStrategy: 'static', pinned: true, closable: false } },
  { path: '/all', element: null, tab: { title: 'All Visits', icon: 'ListChecks', keyStrategy: 'static' } },
  { path: '/visit/:visitId', element: null, tab: { title: (p) => `Visit ${p.visitId}`, keyStrategy: 'param', paramName: 'visitId' } },
  { path: '/login', element: null }, // not tabbable
]

describe('resolveRoute', () => {
  it('matches a static route', () => {
    const r = resolveRoute(routes, '/all')
    expect(r?.def.path).toBe('/all')
    expect(r?.params).toEqual({})
  })
  it('matches a param route and extracts params', () => {
    const r = resolveRoute(routes, '/visit/123')
    expect(r?.def.path).toBe('/visit/:visitId')
    expect(r?.params.visitId).toBe('123')
  })
  it('returns null for an unknown path', () => {
    expect(resolveRoute(routes, '/nope/x')).toBeNull()
  })
})

describe('deriveTabMeta', () => {
  it('keys static routes by route path', () => {
    expect(deriveTabMeta(routes, '/all')?.key).toBe('/all')
  })
  it('keys param routes by route path + param value', () => {
    expect(deriveTabMeta(routes, '/visit/123')?.key).toBe('/visit/:visitId:123')
    expect(deriveTabMeta(routes, '/visit/456')?.key).toBe('/visit/:visitId:456')
  })
  it('resolves a function title with params', () => {
    expect(deriveTabMeta(routes, '/visit/123')?.title).toBe('Visit 123')
  })
  it('carries pinned + closable flags', () => {
    const m = deriveTabMeta(routes, '/dashboard')!
    expect(m.pinned).toBe(true)
    expect(m.closable).toBe(false)
  })
  it('defaults closable to true', () => {
    expect(deriveTabMeta(routes, '/all')?.closable).toBe(true)
  })
  it('returns null for non-tabbable routes', () => {
    expect(deriveTabMeta(routes, '/login')).toBeNull()
  })
  it('sets path to the concrete pathname', () => {
    expect(deriveTabMeta(routes, '/visit/123')?.path).toBe('/visit/123')
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm run test --workspace @uniops/shell -- routeTable`
Expected: FAIL — `resolveRoute`/`deriveTabMeta` not found.

- [ ] **Step 4: Implement `routeTable.ts`**

```ts
import { matchRoutes } from 'react-router-dom'
import type { RouteDef, TabMeta } from './types'

export interface ResolvedRoute {
  def: RouteDef
  params: Record<string, string>
}

/** Match a concrete pathname against the route table. */
export function resolveRoute(routes: RouteDef[], pathname: string): ResolvedRoute | null {
  const matches = matchRoutes(routes.map((r) => ({ path: r.path })), pathname)
  if (!matches || matches.length === 0) return null
  const matched = matches[matches.length - 1]
  const def = routes.find((r) => r.path === matched.route.path)
  if (!def) return null
  const params: Record<string, string> = {}
  for (const [k, v] of Object.entries(matched.params)) {
    if (typeof v === 'string') params[k] = v
  }
  return { def, params }
}

/** Compute the TabMeta for a pathname, or null if the route is not tabbable. */
export function deriveTabMeta(routes: RouteDef[], pathname: string): TabMeta | null {
  const res = resolveRoute(routes, pathname)
  if (!res || !res.def.tab) return null
  const { def, params } = res
  const spec = def.tab
  const key =
    spec.keyStrategy === 'param' && spec.paramName
      ? `${def.path}:${params[spec.paramName] ?? ''}`
      : def.path
  const title = typeof spec.title === 'function' ? spec.title(params) : spec.title
  return {
    key,
    title,
    kind: 'page',
    path: pathname,
    icon: spec.icon,
    pinned: spec.pinned ?? false,
    closable: spec.pinned ? false : spec.closable ?? true,
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test --workspace @uniops/shell -- routeTable`
Expected: PASS (all cases).

- [ ] **Step 6: Commit**

```bash
git add packages/shell/src/tab/types.ts packages/shell/src/tab/routeTable.ts packages/shell/src/tab/routeTable.test.ts
git commit -m "feat(shell): route table resolver + tab identity derivation"
```

---

## Task 3: Tab store core (`tabStore.ts`)

**Files:**
- Create: `packages/shell/src/tab/tabStore.ts`
- Test: `packages/shell/src/tab/tabStore.test.ts`

- [ ] **Step 1: Write the failing test `tabStore.test.ts`**

```ts
import { describe, it, expect, beforeEach } from 'vitest'
import { createTabStore } from './tabStore'
import type { TabMeta } from './types'

const dash: TabMeta = { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', pinned: true, closable: false }
const page = (key: string): TabMeta => ({ key, title: key, kind: 'page', path: key, closable: true })

function fresh() {
  localStorage.clear()
  return createTabStore({ storageKey: 'test:tabs', initialTabs: [dash] })
}

describe('tab store', () => {
  let store: ReturnType<typeof createTabStore>
  beforeEach(() => { store = fresh() })

  it('seeds with the pinned initial tab as active', () => {
    const s = store.getState()
    expect(s.tabs.map(t => t.key)).toEqual(['/dashboard'])
    expect(s.activeKey).toBe('/dashboard')
    expect(s.alive).toContain('/dashboard')
  })

  it('opens a new tab and makes it active + alive', () => {
    store.getState().openTab(page('/all'))
    const s = store.getState()
    expect(s.tabs.map(t => t.key)).toEqual(['/dashboard', '/all'])
    expect(s.activeKey).toBe('/all')
    expect(s.alive).toContain('/all')
  })

  it('dedups by key — re-opening focuses the existing tab', () => {
    store.getState().openTab(page('/all'))
    store.getState().openTab(page('/active'))
    store.getState().openTab(page('/all'))
    const s = store.getState()
    expect(s.tabs.filter(t => t.key === '/all')).toHaveLength(1)
    expect(s.activeKey).toBe('/all')
  })

  it('closes a tab and activates the right neighbour', () => {
    store.getState().openTab(page('/a'))
    store.getState().openTab(page('/b'))
    store.getState().openTab(page('/c'))
    store.getState().setActive('/b')
    store.getState().closeTab('/b')
    const s = store.getState()
    expect(s.tabs.map(t => t.key)).toEqual(['/dashboard', '/a', '/c'])
    expect(s.activeKey).toBe('/c') // right neighbour
    expect(s.alive).not.toContain('/b')
  })

  it('falls back to left neighbour when closing the last tab', () => {
    store.getState().openTab(page('/a'))
    store.getState().openTab(page('/b'))
    store.getState().closeTab('/b')
    expect(store.getState().activeKey).toBe('/a')
  })

  it('refuses to close a pinned tab', () => {
    store.getState().closeTab('/dashboard')
    expect(store.getState().tabs.map(t => t.key)).toEqual(['/dashboard'])
  })

  it('closeOthers keeps the target + pinned', () => {
    store.getState().openTab(page('/a'))
    store.getState().openTab(page('/b'))
    store.getState().closeOthers('/a')
    expect(store.getState().tabs.map(t => t.key)).toEqual(['/dashboard', '/a'])
  })

  it('closeAll keeps only pinned and activates it', () => {
    store.getState().openTab(page('/a'))
    store.getState().openTab(page('/b'))
    store.getState().closeAll()
    const s = store.getState()
    expect(s.tabs.map(t => t.key)).toEqual(['/dashboard'])
    expect(s.activeKey).toBe('/dashboard')
  })

  it('updateTitle changes the tab title', () => {
    store.getState().openTab(page('/visit/1'))
    store.getState().updateTitle('/visit/1', 'Visit ACME')
    expect(store.getState().tabs.find(t => t.key === '/visit/1')?.title).toBe('Visit ACME')
  })

  it('setDirty flags + clears a tab', () => {
    store.getState().openTab(page('/new'))
    store.getState().setDirty('/new', true)
    expect(store.getState().tabs.find(t => t.key === '/new')?.dirty).toBe(true)
    store.getState().setDirty('/new', false)
    expect(store.getState().tabs.find(t => t.key === '/new')?.dirty).toBe(false)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test --workspace @uniops/shell -- tabStore`
Expected: FAIL — `createTabStore` not found.

- [ ] **Step 3: Implement `tabStore.ts`** (eviction added in Task 4; persist in Task 5)

```ts
import { createStore } from 'zustand/vanilla'
import type { TabMeta } from './types'

export interface TabStoreState {
  tabs: TabMeta[]
  activeKey: string
  /** Keys mounted at least once this session (keep-alive set). */
  alive: string[]
  /** Activation order, oldest first — drives LRU eviction. */
  lru: string[]
  openTab: (meta: TabMeta) => void
  closeTab: (key: string) => void
  closeOthers: (key: string) => void
  closeAll: () => void
  setActive: (key: string) => void
  updateTitle: (key: string, title: string) => void
  setDirty: (key: string, dirty: boolean) => void
}

export interface TabStoreOptions {
  storageKey: string
  cap?: number
  initialTabs?: TabMeta[]
}

function touchLru(lru: string[], key: string): string[] {
  return [...lru.filter((k) => k !== key), key]
}

export function createTabStore(opts: TabStoreOptions) {
  const initial = opts.initialTabs ?? []
  const firstKey = initial[0]?.key ?? ''

  return createStore<TabStoreState>((set, get) => ({
    tabs: initial,
    activeKey: firstKey,
    alive: firstKey ? [firstKey] : [],
    lru: firstKey ? [firstKey] : [],

    openTab: (meta) => {
      const { tabs } = get()
      if (tabs.some((t) => t.key === meta.key)) {
        get().setActive(meta.key)
        return
      }
      set((s) => ({
        tabs: [...s.tabs, meta],
        activeKey: meta.key,
        alive: s.alive.includes(meta.key) ? s.alive : [...s.alive, meta.key],
        lru: touchLru(s.lru, meta.key),
      }))
    },

    setActive: (key) => {
      set((s) => {
        if (!s.tabs.some((t) => t.key === key)) return s
        return {
          activeKey: key,
          alive: s.alive.includes(key) ? s.alive : [...s.alive, key],
          lru: touchLru(s.lru, key),
        }
      })
    },

    closeTab: (key) => {
      const { tabs, activeKey } = get()
      const target = tabs.find((t) => t.key === key)
      if (!target || target.pinned) return
      const idx = tabs.findIndex((t) => t.key === key)
      const nextTabs = tabs.filter((t) => t.key !== key)
      let nextActive = activeKey
      if (activeKey === key) {
        const neighbour = nextTabs[idx] ?? nextTabs[idx - 1] ?? nextTabs[nextTabs.length - 1]
        nextActive = neighbour?.key ?? ''
      }
      set((s) => ({
        tabs: nextTabs,
        activeKey: nextActive,
        alive: s.alive.filter((k) => k !== key),
        lru: s.lru.filter((k) => k !== key),
      }))
    },

    closeOthers: (key) => {
      set((s) => {
        const kept = s.tabs.filter((t) => t.pinned || t.key === key)
        const keptKeys = new Set(kept.map((t) => t.key))
        return {
          tabs: kept,
          activeKey: keptKeys.has(s.activeKey) ? s.activeKey : key,
          alive: s.alive.filter((k) => keptKeys.has(k)),
          lru: s.lru.filter((k) => keptKeys.has(k)),
        }
      })
    },

    closeAll: () => {
      set((s) => {
        const kept = s.tabs.filter((t) => t.pinned)
        const keptKeys = new Set(kept.map((t) => t.key))
        const active = kept[0]?.key ?? ''
        return {
          tabs: kept,
          activeKey: active,
          alive: s.alive.filter((k) => keptKeys.has(k)),
          lru: s.lru.filter((k) => keptKeys.has(k)),
        }
      })
    },

    updateTitle: (key, title) =>
      set((s) => ({ tabs: s.tabs.map((t) => (t.key === key ? { ...t, title } : t)) })),

    setDirty: (key, dirty) =>
      set((s) => ({ tabs: s.tabs.map((t) => (t.key === key ? { ...t, dirty } : t)) })),
  }))
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test --workspace @uniops/shell -- tabStore`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/shell/src/tab/tabStore.ts packages/shell/src/tab/tabStore.test.ts
git commit -m "feat(shell): tab store (open/dedup/close/closeOthers/closeAll/dirty)"
```

---

## Task 4: Eviction (LRU cap) in the tab store

**Files:**
- Modify: `packages/shell/src/tab/tabStore.ts` (the `openTab` action)
- Test: `packages/shell/src/tab/tabStore.test.ts` (add a describe block)

- [ ] **Step 1: Add the failing eviction tests**

Append to `tabStore.test.ts`:

```ts
describe('tab store — eviction (cap)', () => {
  const dash2: TabMeta = { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', pinned: true, closable: false }
  const pg = (k: string): TabMeta => ({ key: k, title: k, kind: 'page', path: k, closable: true })

  function freshCapped(cap: number) {
    localStorage.clear()
    return createTabStore({ storageKey: 'test:cap', cap, initialTabs: [dash2] })
  }

  it('evicts the least-recently-used non-pinned tab when over cap', () => {
    const store = freshCapped(3) // cap counts page tabs incl. pinned
    store.getState().openTab(pg('/a'))
    store.getState().openTab(pg('/b'))     // tabs: dash,a,b (=3, at cap)
    store.getState().setActive('/a')        // lru: dash,b,a  → b is now LRU non-pinned
    store.getState().openTab(pg('/c'))      // over cap → evict b
    expect(store.getState().tabs.map(t => t.key)).toEqual(['/dashboard', '/a', '/c'])
  })

  it('never evicts pinned tabs', () => {
    const store = freshCapped(2)
    store.getState().openTab(pg('/a')) // dash,a (cap 2)
    store.getState().openTab(pg('/b')) // over cap → evict a (not dash)
    expect(store.getState().tabs.map(t => t.key)).toEqual(['/dashboard', '/b'])
  })

  it('skips dirty tabs when choosing an eviction victim', () => {
    const store = freshCapped(3)
    store.getState().openTab(pg('/a'))
    store.getState().openTab(pg('/b'))
    store.getState().setDirty('/a', true) // a is oldest non-pinned but dirty
    store.getState().setActive('/b')
    store.getState().openTab(pg('/c'))     // should evict b-area LRU, skipping dirty /a
    const keys = store.getState().tabs.map(t => t.key)
    expect(keys).toContain('/a')           // dirty preserved
    expect(keys).toContain('/dashboard')
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test --workspace @uniops/shell -- tabStore`
Expected: FAIL — over-cap tabs are not evicted (length grows past cap).

- [ ] **Step 3: Add eviction to `openTab`**

Replace the `openTab` action body in `tabStore.ts` with:

```ts
    openTab: (meta) => {
      const cap = opts.cap ?? 15
      const { tabs } = get()
      if (tabs.some((t) => t.key === meta.key)) {
        get().setActive(meta.key)
        return
      }
      set((s) => {
        let nextTabs = [...s.tabs, meta]
        let nextAlive = s.alive.includes(meta.key) ? s.alive : [...s.alive, meta.key]
        const nextLru = touchLru(s.lru, meta.key)

        // Evict LRU non-pinned, non-dirty, non-incoming tabs until within cap.
        while (nextTabs.filter((t) => t.kind === 'page').length > cap) {
          const victimKey = nextLru.find((k) => {
            const t = nextTabs.find((x) => x.key === k)
            return t && !t.pinned && !t.dirty && t.key !== meta.key
          })
          if (!victimKey) break // nothing safe to evict
          nextTabs = nextTabs.filter((t) => t.key !== victimKey)
          nextAlive = nextAlive.filter((k) => k !== victimKey)
        }

        return {
          tabs: nextTabs,
          activeKey: meta.key,
          alive: nextAlive,
          lru: nextLru.filter((k) => nextTabs.some((t) => t.key === k)),
        }
      })
    },
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test --workspace @uniops/shell -- tabStore`
Expected: PASS (all eviction + earlier cases).

- [ ] **Step 5: Commit**

```bash
git add packages/shell/src/tab/tabStore.ts packages/shell/src/tab/tabStore.test.ts
git commit -m "feat(shell): LRU eviction with pinned + dirty guards"
```

---

## Task 5: Persistence + restore

**Files:**
- Modify: `packages/shell/src/tab/tabStore.ts` (wrap in `persist`, rehydrate merge)
- Test: `packages/shell/src/tab/tabStore.test.ts` (add a describe block)

- [ ] **Step 1: Add the failing persistence tests**

Append to `tabStore.test.ts`:

```ts
describe('tab store — persistence', () => {
  const dash3: TabMeta = { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', pinned: true, closable: false }
  const pg = (k: string): TabMeta => ({ key: k, title: k, kind: 'page', path: k, closable: true })

  it('persists open tabs + activeKey to localStorage', () => {
    localStorage.clear()
    const a = createTabStore({ storageKey: 'persist:tabs', initialTabs: [dash3] })
    a.getState().openTab(pg('/all'))
    a.getState().openTab(pg('/active'))
    a.getState().setActive('/all')

    // New store instance with the same storageKey rehydrates prior tabs.
    const b = createTabStore({ storageKey: 'persist:tabs', initialTabs: [dash3] })
    const s = b.getState()
    expect(s.tabs.map(t => t.key)).toEqual(['/dashboard', '/all', '/active'])
    expect(s.activeKey).toBe('/all')
  })

  it('on restore, only the active tab is alive (others lazy-mount)', () => {
    localStorage.clear()
    const a = createTabStore({ storageKey: 'persist:alive', initialTabs: [dash3] })
    a.getState().openTab(pg('/all'))
    a.getState().openTab(pg('/active'))
    a.getState().setActive('/active')

    const b = createTabStore({ storageKey: 'persist:alive', initialTabs: [dash3] })
    expect(b.getState().alive).toEqual(['/active'])
  })

  it('re-injects the pinned tab if storage somehow lacks it', () => {
    localStorage.setItem('persist:nopin', JSON.stringify({
      state: { tabs: [{ key: '/all', title: 'All', kind: 'page', path: '/all', closable: true }], activeKey: '/all' },
      version: 0,
    }))
    const b = createTabStore({ storageKey: 'persist:nopin', initialTabs: [dash3] })
    expect(b.getState().tabs.some(t => t.key === '/dashboard')).toBe(true)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test --workspace @uniops/shell -- tabStore`
Expected: FAIL — second instance does not see the first instance's tabs (no persistence yet).

- [ ] **Step 3: Wrap the store creator in `persist`**

In `tabStore.ts`, update imports and wrap the store. Persist only `tabs` + `activeKey`; recompute `alive`/`lru` from `activeKey` on rehydrate; ensure pinned `initialTabs` are present.

```ts
import { createStore } from 'zustand/vanilla'
import { persist, createJSONStorage } from 'zustand/middleware'
import type { TabMeta } from './types'
```

Change the `createTabStore` return to:

```ts
  return createStore<TabStoreState>()(
    persist(
      (set, get) => ({
        tabs: initial,
        activeKey: firstKey,
        alive: firstKey ? [firstKey] : [],
        lru: firstKey ? [firstKey] : [],
        // ...all actions unchanged from Tasks 3 & 4...
      }),
      {
        name: opts.storageKey,
        storage: createJSONStorage(() => localStorage),
        partialize: (s) => ({ tabs: s.tabs, activeKey: s.activeKey }),
        merge: (persisted, current) => {
          const p = (persisted ?? {}) as Partial<TabStoreState>
          let tabs = p.tabs ?? current.tabs
          // Guarantee pinned initial tabs exist and sit first.
          for (const pin of initial) {
            if (!tabs.some((t) => t.key === pin.key)) tabs = [pin, ...tabs]
          }
          const activeKey =
            p.activeKey && tabs.some((t) => t.key === p.activeKey)
              ? p.activeKey
              : tabs[0]?.key ?? ''
          return { ...current, tabs, activeKey, alive: activeKey ? [activeKey] : [], lru: activeKey ? [activeKey] : [] }
        },
      },
    ),
  )
```

> Note: keep the action implementations from Tasks 3–4 exactly; only the store is now wrapped in `persist(...)`.

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test --workspace @uniops/shell -- tabStore`
Expected: PASS (persistence + all earlier cases).

- [ ] **Step 5: Commit**

```bash
git add packages/shell/src/tab/tabStore.ts packages/shell/src/tab/tabStore.test.ts
git commit -m "feat(shell): persist tabs + restore with lazy alive and pinned guarantee"
```

---

## Task 6: React context + hook (`TabStoreContext.tsx`)

**Files:**
- Create: `packages/shell/src/tab/TabStoreContext.tsx`

- [ ] **Step 1: Implement the provider + hook**

```tsx
import { createContext, useContext, useRef, type ReactNode } from 'react'
import { useStore } from 'zustand'
import { createTabStore, type TabStoreOptions, type TabStoreState } from './tabStore'

type TabStoreApi = ReturnType<typeof createTabStore>

const TabStoreContext = createContext<TabStoreApi | null>(null)

export function TabStoreProvider({ options, children }: { options: TabStoreOptions; children: ReactNode }) {
  // Create exactly once per provider lifetime.
  const ref = useRef<TabStoreApi>(null)
  if (ref.current === null) ref.current = createTabStore(options)
  return <TabStoreContext.Provider value={ref.current}>{children}</TabStoreContext.Provider>
}

export function useTabStoreApi(): TabStoreApi {
  const api = useContext(TabStoreContext)
  if (!api) throw new Error('useTabStore must be used within <TabStoreProvider>')
  return api
}

export function useTabStore<T>(selector: (s: TabStoreState) => T): T {
  return useStore(useTabStoreApi(), selector)
}
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc -p packages/shell/tsconfig.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add packages/shell/src/tab/TabStoreContext.tsx
git commit -m "feat(shell): TabStoreProvider + useTabStore hook"
```

---

## Task 7: `RouteRenderer` (pinned-location routing per tab)

**Files:**
- Create: `packages/shell/src/tab/RouteRenderer.tsx`

- [ ] **Step 1: Implement `RouteRenderer.tsx`**

Each tab renders its page inside a `<Routes location={path}>` pinned to that tab's path, so `useParams()` works and the element stays mounted while the div is hidden. A class error boundary keeps one broken tab from blanking the whole shell.

```tsx
import { Component, Suspense, type ReactNode } from 'react'
import { Routes, Route } from 'react-router-dom'
import type { RouteDef } from './types'

class TabErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  render() {
    if (this.state.error) {
      return (
        <div className="p-6 text-sm text-danger-600">
          This tab failed to render: {this.state.error.message}
        </div>
      )
    }
    return this.props.children
  }
}

export function RouteRenderer({ routes, path }: { routes: RouteDef[]; path: string }) {
  return (
    <TabErrorBoundary>
      <Suspense fallback={<div className="p-6 text-sm text-neutral-500">Loading…</div>}>
        <Routes location={path}>
          {routes.map((r) => (
            <Route key={r.path} path={r.path} element={r.element} />
          ))}
          <Route path="*" element={null} />
        </Routes>
      </Suspense>
    </TabErrorBoundary>
  )
}
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc -p packages/shell/tsconfig.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add packages/shell/src/tab/RouteRenderer.tsx
git commit -m "feat(shell): RouteRenderer with pinned per-tab location + error boundary"
```

---

## Task 8: `TabHost` keep-alive renderer + integration test

**Files:**
- Create: `packages/shell/src/tab/TabHost.tsx`
- Test: `packages/shell/src/tab/TabHost.test.tsx`

- [ ] **Step 1: Implement `TabHost.tsx`**

Renders only alive tabs; inactive ones are hidden (not unmounted). Each wrapper is its own scroll container so scroll position survives switching.

```tsx
import { MemoryRouter } from 'react-router-dom'
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
              // Isolated router per tab keeps the page mounted at its own path
              // regardless of the browser URL (which only drives `activeKey`).
              <MemoryRouter initialEntries={[t.path!]}>
                <RouteRenderer routes={routes} path={t.path!} />
              </MemoryRouter>
            )}
          </div>
        ))}
    </div>
  )
}
```

> Why `MemoryRouter` per page tab: it gives each kept-alive page its own routing context fixed to the tab's path, fully decoupled from the shared browser `BrowserRouter`. Navigation *within* a page (e.g. a detail page pushing to an edit sub-route) stays inside that tab's memory history; cross-page navigation goes through the sidebar/links → `TabRouterSync` (Task 10). For the VMS pilot this is sufficient; richer in-tab→shell navigation is a documented follow-up.

- [ ] **Step 2: Write the keep-alive integration test `TabHost.test.tsx`**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { useState } from 'react'
import { BrowserRouter } from 'react-router-dom'
import { TabStoreProvider, useTabStoreApi } from './TabStoreContext'
import { TabHost } from './TabHost'
import type { RouteDef, TabMeta } from './types'

function Counter({ label }: { label: string }) {
  const [n, setN] = useState(0)
  return (
    <div>
      <span>{label} count: {n}</span>
      <button onClick={() => setN((v) => v + 1)}>{label}-inc</button>
    </div>
  )
}

const routes: RouteDef[] = [
  { path: '/a', element: <Counter label="A" />, tab: { title: 'A', keyStrategy: 'static' } },
  { path: '/b', element: <Counter label="B" />, tab: { title: 'B', keyStrategy: 'static' } },
]

const tabA: TabMeta = { key: '/a', title: 'A', kind: 'page', path: '/a', closable: true }
const tabB: TabMeta = { key: '/b', title: 'B', kind: 'page', path: '/b', closable: true }

function Harness() {
  const api = useTabStoreApi()
  return (
    <>
      <button onClick={() => api.getState().openTab(tabB)}>open-b</button>
      <button onClick={() => api.getState().setActive('/a')}>activate-a</button>
      <TabHost routes={routes} />
    </>
  )
}

describe('TabHost keep-alive', () => {
  it('preserves a tab’s component state across switching away and back', () => {
    localStorage.clear()
    render(
      <BrowserRouter>
        <TabStoreProvider options={{ storageKey: 'ka:test', initialTabs: [tabA] }}>
          <Harness />
        </TabStoreProvider>
      </BrowserRouter>,
    )

    // Increment A's counter to 1.
    fireEvent.click(screen.getByText('A-inc'))
    expect(screen.getByText('A count: 1')).toBeInTheDocument()

    // Open B (switch away), then back to A.
    act(() => { fireEvent.click(screen.getByText('open-b')) })
    act(() => { fireEvent.click(screen.getByText('activate-a')) })

    // A's state survived (still 1, not reset to 0) → component was not unmounted.
    expect(screen.getByText('A count: 1')).toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run to verify it passes**

Run: `npm run test --workspace @uniops/shell -- TabHost`
Expected: PASS — "A count: 1" persists after switching to B and back (proves keep-alive).

- [ ] **Step 4: Commit**

```bash
git add packages/shell/src/tab/TabHost.tsx packages/shell/src/tab/TabHost.test.tsx
git commit -m "feat(shell): keep-alive TabHost + state-retention integration test"
```

---

## Task 9: `TabBar` UI

**Files:**
- Create: `packages/shell/src/tab/TabBar.tsx`
- Create: `packages/shell/src/lib/cn.ts`
- Create: `packages/shell/src/lib/icon.ts`

- [ ] **Step 1: Create `cn` util `packages/shell/src/lib/cn.ts`**

```ts
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```

- [ ] **Step 2: Create the icon resolver `packages/shell/src/lib/icon.ts`**

Tab meta stores icon *names* (serializable). Resolve to a lucide component at render time.

```ts
import * as Lucide from 'lucide-react'
import type { ComponentType } from 'react'

type IconProps = { className?: string }

export function resolveIcon(name?: string): ComponentType<IconProps> | null {
  if (!name) return null
  const lib = Lucide as unknown as Record<string, ComponentType<IconProps>>
  return lib[name] ?? null
}
```

- [ ] **Step 3: Implement `TabBar.tsx`**

```tsx
import { useNavigate } from 'react-router-dom'
import { X } from 'lucide-react'
import { useTabStore, useTabStoreApi } from './TabStoreContext'
import { resolveIcon } from '../lib/icon'
import { cn } from '../lib/cn'

export function TabBar() {
  const tabs = useTabStore((s) => s.tabs)
  const activeKey = useTabStore((s) => s.activeKey)
  const api = useTabStoreApi()
  const navigate = useNavigate()

  const select = (key: string, path?: string) => {
    api.getState().setActive(key)
    if (path) navigate(path)
  }

  const close = (e: React.MouseEvent, key: string) => {
    e.stopPropagation()
    const tab = api.getState().tabs.find((t) => t.key === key)
    if (tab?.dirty && !window.confirm('This tab has unsaved changes. Close anyway?')) return
    api.getState().closeTab(key)
    // After close, sync the URL to whatever became active.
    const next = api.getState().tabs.find((t) => t.key === api.getState().activeKey)
    if (next?.path) navigate(next.path)
  }

  return (
    <div className="flex h-10 shrink-0 items-stretch gap-1 overflow-x-auto border-b border-neutral-200 bg-white px-2">
      {tabs.map((t) => {
        const Icon = resolveIcon(t.icon)
        const active = t.key === activeKey
        return (
          <button
            key={t.key}
            onClick={() => select(t.key, t.path)}
            className={cn(
              'group flex items-center gap-1.5 self-center rounded-md px-3 py-1.5 text-sm transition-colors',
              active
                ? 'bg-primary-50 text-primary-700 font-medium'
                : 'text-neutral-600 hover:bg-neutral-50',
            )}
          >
            {Icon && <Icon className="h-3.5 w-3.5 shrink-0" />}
            <span className="max-w-[160px] truncate">{t.title}</span>
            {t.dirty && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-warning-500" />}
            {t.closable && (
              <span
                role="button"
                tabIndex={-1}
                aria-label={`Close ${t.title}`}
                onClick={(e) => close(e, t.key)}
                className="ml-1 rounded p-0.5 text-neutral-400 opacity-60 hover:bg-neutral-200 hover:text-neutral-700 group-hover:opacity-100"
              >
                <X className="h-3 w-3" />
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 4: Typecheck**

Run: `npx tsc -p packages/shell/tsconfig.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add packages/shell/src/tab/TabBar.tsx packages/shell/src/lib/cn.ts packages/shell/src/lib/icon.ts
git commit -m "feat(shell): TabBar with close + dirty indicator + icon resolver"
```

---

## Task 10: `TabRouterSync` (URL → tab) + `useTabDirty`

**Files:**
- Create: `packages/shell/src/tab/TabRouterSync.tsx`
- Create: `packages/shell/src/tab/useTabDirty.ts`

- [ ] **Step 1: Implement `TabRouterSync.tsx`**

Observes the browser URL. When it changes (sidebar NavLink, deep link, back/forward), it derives the tab and opens/activates it. Guarded so it never fights a `setActive`-driven navigate.

```tsx
import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useTabStoreApi } from './TabStoreContext'
import { deriveTabMeta } from './routeTable'
import type { RouteDef } from './types'

export function TabRouterSync({ routes }: { routes: RouteDef[] }) {
  const api = useTabStoreApi()
  const { pathname } = useLocation()

  useEffect(() => {
    const meta = deriveTabMeta(routes, pathname)
    if (!meta) return
    if (api.getState().activeKey === meta.key) return // already showing this tab
    api.getState().openTab(meta) // openTab dedups → focus or create
  }, [pathname, routes, api])

  return null
}
```

- [ ] **Step 2: Implement `useTabDirty.ts`**

Lets a page flag its own tab as dirty (unsaved changes). The page passes its tab key (derived from the current route).

```ts
import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useTabStoreApi } from './TabStoreContext'
import { deriveTabMeta } from './routeTable'
import type { RouteDef } from './types'

export function useTabDirty(routes: RouteDef[], dirty: boolean) {
  const api = useTabStoreApi()
  const { pathname } = useLocation()
  useEffect(() => {
    const meta = deriveTabMeta(routes, pathname)
    if (!meta) return
    api.getState().setDirty(meta.key, dirty)
    return () => api.getState().setDirty(meta.key, false)
  }, [api, routes, pathname, dirty])
}
```

> Note: in the pilot, pages call `useTabDirty` with the VMS route table. Because `TabHost` renders pages inside a `MemoryRouter` pinned to the tab path, `useLocation()` here resolves to that tab's path — exactly the tab we want to mark.

- [ ] **Step 3: Typecheck**

Run: `npx tsc -p packages/shell/tsconfig.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add packages/shell/src/tab/TabRouterSync.tsx packages/shell/src/tab/useTabDirty.ts
git commit -m "feat(shell): TabRouterSync URL→tab bridge + useTabDirty hook"
```

---

## Task 11: Public exports (`index.ts`)

**Files:**
- Modify: `packages/shell/src/index.ts`

- [ ] **Step 1: Replace `index.ts` with the public surface**

```ts
export type { TabMeta, TabKind, TabSpec, RouteDef } from './tab/types'
export { resolveRoute, deriveTabMeta } from './tab/routeTable'
export { createTabStore } from './tab/tabStore'
export type { TabStoreState, TabStoreOptions } from './tab/tabStore'
export { TabStoreProvider, useTabStore, useTabStoreApi } from './tab/TabStoreContext'
export { TabHost } from './tab/TabHost'
export { TabBar } from './tab/TabBar'
export { TabRouterSync } from './tab/TabRouterSync'
export { useTabDirty } from './tab/useTabDirty'
export { RouteRenderer } from './tab/RouteRenderer'
```

- [ ] **Step 2: Run the full shell test suite + typecheck**

Run: `npm run test --workspace @uniops/shell`
Expected: PASS (routeTable + tabStore + TabHost suites).
Run: `npx tsc -p packages/shell/tsconfig.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add packages/shell/src/index.ts
git commit -m "feat(shell): public package exports"
```

---

## Task 12: VMS route table

**Files:**
- Create: `vms/src/app/routes.tsx`
- Modify: `vms/package.json`

- [ ] **Step 1: Add `@uniops/shell` to VMS deps**

In `vms/package.json`, add to `"dependencies"`:

```json
    "@uniops/shell": "*",
```

Then run: `npm install` (repo root) — links the workspace package into `vms/node_modules`.

- [ ] **Step 2: Create `vms/src/app/routes.tsx`**

One `RouteDef` per existing VMS page. Dashboard is pinned. Detail/param routes use `keyStrategy:'param'`. Order matters only for matching specificity — keep literal segments before `:param` (mirror the current `App.tsx` order).

```tsx
import type { RouteDef } from '@uniops/shell'
import VisitListPage from '@/pages/VisitListPage'
import VisitCreatePage from '@/pages/VisitCreatePage'
import VisitDetailPage from '@/pages/VisitDetailPage'
import ActiveVisitsPage from '@/pages/ActiveVisitsPage'
import BadgePrintPage from '@/pages/BadgePrintPage'
import CheckOutPage from '@/pages/CheckOutPage'
import DashboardPage from '@/pages/DashboardPage'
import AuditLogPage from '@/pages/AuditLogPage'
import ReportsPage from '@/pages/ReportsPage'
import HealthDeclarationsPage from '@/pages/HealthDeclarationsPage'
import TaskInboxPage from '@/pages/TaskInboxPage'
import VisitorCompliancePage from '@/pages/VisitorCompliancePage'
import AdminPanel from '@/pages/admin/AdminPanel'

export const vmsRoutes: RouteDef[] = [
  { path: '/dashboard', element: <DashboardPage />, tab: { title: 'Dashboard', icon: 'LayoutDashboard', keyStrategy: 'static', pinned: true } },
  { path: '/tasks', element: <TaskInboxPage />, tab: { title: 'Task Inbox', icon: 'Inbox', keyStrategy: 'static' } },
  { path: '/', element: <VisitListPage scope="today" />, tab: { title: 'Today’s Visits', icon: 'CalendarCheck', keyStrategy: 'static' } },
  { path: '/all', element: <VisitListPage scope="all" />, tab: { title: 'All Visits', icon: 'ListChecks', keyStrategy: 'static' } },
  { path: '/active', element: <ActiveVisitsPage />, tab: { title: 'On-Site Now', icon: 'UserCheck', keyStrategy: 'static' } },
  { path: '/new', element: <VisitCreatePage />, tab: { title: 'New Visit', icon: 'Plus', keyStrategy: 'static' } },
  { path: '/check-out', element: <CheckOutPage />, tab: { title: 'Check Out', icon: 'ScanLine', keyStrategy: 'static' } },
  { path: '/audit-log', element: <AuditLogPage />, tab: { title: 'Audit Log', icon: 'FileSearch', keyStrategy: 'static' } },
  { path: '/reports', element: <ReportsPage />, tab: { title: 'Reports', icon: 'FileDown', keyStrategy: 'static' } },
  { path: '/health-declarations', element: <HealthDeclarationsPage />, tab: { title: 'Health Declarations', icon: 'ClipboardCheck', keyStrategy: 'static' } },
  { path: '/admin/*', element: <AdminPanel />, tab: { title: 'VMS Admin', icon: 'Settings', keyStrategy: 'static' } },
  { path: '/visitor/:visitorId/compliance', element: <VisitorCompliancePage />, tab: { title: (p) => `Compliance ${p.visitorId}`, icon: 'ClipboardCheck', keyStrategy: 'param', paramName: 'visitorId' } },
  { path: '/badge/:visitId', element: <BadgePrintPage />, tab: { title: (p) => `Badge ${p.visitId}`, icon: 'Plus', keyStrategy: 'param', paramName: 'visitId' } },
  { path: '/:visitId', element: <VisitDetailPage />, tab: { title: (p) => `Visit ${p.visitId}`, icon: 'CalendarCheck', keyStrategy: 'param', paramName: 'visitId' } },
]
```

- [ ] **Step 3: Typecheck VMS**

Run: `cd vms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors (route table compiles; pages already exist).

- [ ] **Step 4: Commit**

```bash
git add vms/src/app/routes.tsx vms/package.json
git commit -m "feat(vms): route table for the tab shell"
```

---

## Task 13: Wire the tab shell into VMS `AppLayout`

**Files:**
- Modify: `vms/src/components/layout/AppLayout.tsx`

The Sidebar/Header stay as-is. We only: (a) wrap the content area in `TabStoreProvider`, (b) replace `<Outlet/>` with `TabBar` + `TabHost`, (c) mount `TabRouterSync`. Clicking a sidebar NavLink changes the URL → `TabRouterSync` opens/activates the tab.

- [ ] **Step 1: Add imports at the top of `AppLayout.tsx`**

```tsx
import { TabStoreProvider, TabBar, TabHost, TabRouterSync } from '@uniops/shell'
import type { TabMeta } from '@uniops/shell'
import { vmsRoutes } from '@/app/routes'
```

- [ ] **Step 2: Define the pinned Dashboard seed (above the `AppLayout` component)**

```tsx
const VMS_INITIAL_TABS: TabMeta[] = [
  { key: '/dashboard', title: 'Dashboard', kind: 'page', path: '/dashboard', icon: 'LayoutDashboard', pinned: true, closable: false },
]
```

- [ ] **Step 3: Replace the `return (...)` body of the `AppLayout` component**

Replace the existing authenticated `return (...)` (the `<div className="relative flex h-screen ...">` block) with:

```tsx
  return (
    <TabStoreProvider options={{ storageKey: 'uniops:vms:tabs', initialTabs: VMS_INITIAL_TABS }}>
      <div className="relative flex h-screen overflow-hidden bg-[#FAFBFC]">
        {mobileOpen && (
          <div
            className="fixed inset-0 z-20 bg-black/50 md:hidden"
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
          />
        )}

        <Sidebar
          mobileOpen={mobileOpen}
          onClose={() => setMobileOpen(false)}
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed(v => !v)}
        />

        <div className="flex flex-1 flex-col overflow-hidden min-w-0">
          <Header onMobileMenuToggle={() => setMobileOpen(v => !v)} />
          <TabRouterSync routes={vmsRoutes} />
          <TabBar />
          <main className="relative flex-1 overflow-hidden">
            <TabHost routes={vmsRoutes} />
          </main>
        </div>
      </div>
    </TabStoreProvider>
  )
```

> The old `<main className="flex-1 overflow-y-auto p-6"><div className="mx-auto max-w-[1440px]"><Outlet/></div></main>` is removed. Per-page padding/width now lives inside each page or is added by a follow-up shared `PageContainer`; for the pilot, VMS pages already render their own outer padding via their page roots — verify visually in Task 15 and add a wrapping `div` with `p-6` inside `TabHost`'s page branch only if pages look edge-to-edge.

- [ ] **Step 4: Remove the now-unused `Outlet` import**

In `AppLayout.tsx` line 2, change:
```tsx
import { Outlet, NavLink, useLocation } from 'react-router-dom'
```
to:
```tsx
import { NavLink, useLocation } from 'react-router-dom'
```

- [ ] **Step 5: Typecheck**

Run: `cd vms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors, no unused-import error for `Outlet`.

- [ ] **Step 6: Commit**

```bash
git add vms/src/components/layout/AppLayout.tsx
git commit -m "feat(vms): mount tab shell (TabBar + TabHost + TabRouterSync) in AppLayout"
```

---

## Task 14: Collapse VMS `App.tsx` routing into the shell

**Files:**
- Modify: `vms/src/App.tsx`

The page `<Route>`s now live in the tab route table and are rendered by `TabHost`. The top-level router only needs to mount `AppLayout` for all paths (so the browser URL drives `TabRouterSync`).

- [ ] **Step 1: Replace `vms/src/App.tsx` with**

```tsx
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AppLayout from '@/components/layout/AppLayout'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* AppLayout owns all in-app rendering via the tab host. */}
          <Route path="/*" element={<AppLayout />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
```

- [ ] **Step 2: Typecheck + build VMS**

Run: `cd vms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors (page imports now unused in App.tsx are gone).
Run: `cd vms && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add vms/src/App.tsx
git commit -m "feat(vms): route all paths through AppLayout tab host"
```

---

## Task 15: Manual verification in the browser

**Files:** none (verification only)

- [ ] **Step 1: Start the VMS dev server**

Run: `cd vms && npm run dev`
Open: `http://localhost:5176` (log in via portal handoff if redirected, per existing auth flow).

- [ ] **Step 2: Verify tab open + dedup**

- Click several sidebar items (Today’s Visits, All Visits, On-Site Now). Each opens a new tab; the clicked tab becomes active.
- Click "All Visits" again → focuses the existing tab (no duplicate).
- Expected: Dashboard tab is pinned, leftmost, with no × button.

- [ ] **Step 3: Verify keep-alive (the core promise)**

- Open "All Visits", scroll down / apply a filter.
- Open "New Visit", type into a field (do not save).
- Switch back to "All Visits" → scroll position + filter intact.
- Switch back to "New Visit" → typed value still there.
- Expected: no reload/refetch on tab switch; state preserved.

- [ ] **Step 4: Verify detail tabs key by id**

- Open a visit detail (`/:visitId`) for two different visits → two separate tabs.
- Re-open the first → focuses its existing tab.

- [ ] **Step 5: Verify close behavior + URL sync**

- Close the active tab → a neighbour activates and the URL updates to match.
- Close all closable tabs → Dashboard remains and is active.

- [ ] **Step 6: Verify persistence**

- Open 3 tabs, switch to one, refresh the browser.
- Expected: the tab strip is restored; the active tab is mounted (others lazy-mount when clicked); page state inside tabs is reset (acceptable per design).

- [ ] **Step 7: Verify deep link**

- Paste a detail URL (e.g. `http://localhost:5176/active`) into a new tab/window.
- Expected: the shell opens with that tab active.

- [ ] **Step 8: Record results**

If any step fails, capture the symptom and route back to the relevant task (TabHost for keep-alive, TabRouterSync for URL/open issues, tabStore for close/persist issues). If all pass, the pilot validates the architecture.

- [ ] **Step 9: Final commit (verification notes, optional)**

```bash
git commit --allow-empty -m "test(vms): manual tab-shell verification passed (pilot validated)"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** This plan implements spec §3 (tab engine: data model, deriveTabKey, route table, keep-alive TabHost, URL sync, dirty guard, eviction, persistence) and §5 Phase 0 (workspace) + Phase 2 (VMS pilot). Spec §3.6 cross-module iframe drill-down is represented by the `kind:'iframe'` branch in `TabHost`/`TabBar` but is only exercised in Phase 4 (Finance) — no VMS iframe tabs in this pilot. §2.1 `@uniops/tokens`/`@uniops/ui` and §2.2 Finance split are explicitly out of scope (follow-on plans).
- **Token classes:** `TabBar`/`RouteRenderer` use `primary-*`/`neutral-*`/`danger-*`/`warning-*` classes, which resolve against VMS’s existing `@theme` in `vms/src/index.css`. They will render correctly in VMS without `@uniops/tokens`. When the shell is later consumed by an app lacking these tokens, Phase 1 (`@uniops/tokens`) must land first — note this in the Phase 1 plan.
- **Known follow-ups (not bugs):** in-tab→shell cross-page navigation (a page calling `navigate('/other')` inside its `MemoryRouter`) won’t change the active tab; pilot pages don’t do this, but the Phase 3 generalization should add a shell-aware navigation helper. TabBar right-click context menu (close others/all) is wired in the store but not yet surfaced in the pilot UI — add in Phase 3.
