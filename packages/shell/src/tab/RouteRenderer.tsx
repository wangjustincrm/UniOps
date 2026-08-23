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

// Rendered when `path` matches none of `routes`. This used to be a silent
// `element={null}` — a route-table bug (or a path/tab-key convention
// mismatch between this table and the caller) then presented as a mystery
// blank tab with zero console output and no error-boundary trigger. Render
// something visible instead so a routing failure is never indistinguishable
// from "still loading" or "nothing to show here".
function NoRouteMatched({ path }: { path: string }) {
  return (
    <div className="p-6 text-sm text-danger-600">
      No route matched: {path}
    </div>
  )
}

export function RouteRenderer({ routes, path }: { routes: RouteDef[]; path: string }) {
  return (
    <TabErrorBoundary>
      <Suspense fallback={<div className="p-6 text-sm text-neutral-500">Loading…</div>}>
        <Routes location={path}>
          {routes.map((r) => (
            <Route key={r.path} path={r.path} element={r.element} />
          ))}
          <Route path="*" element={<NoRouteMatched path={path} />} />
        </Routes>
      </Suspense>
    </TabErrorBoundary>
  )
}
