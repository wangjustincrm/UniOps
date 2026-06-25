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
