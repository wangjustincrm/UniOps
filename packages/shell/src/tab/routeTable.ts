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
  // The last match is the leaf match; routes are currently flat so this is
  // equivalent to the only match, but written this way to tolerate future
  // nested routes (where the leaf would be the most specific match).
  const matched = matches[matches.length - 1]
  const def = routes.find((r) => r.path === matched.route.path)
  if (!def) return null
  const params: Record<string, string> = {}
  for (const [k, v] of Object.entries(matched.params)) {
    if (typeof v === 'string') params[k] = v
  }
  return { def, params }
}

/**
 * Compute the TabMeta for a pathname, or null if the route is not tabbable.
 * `search` (e.g. `?settleFrom=x`) is matched on the pathname only but carried
 * into `path`, so pages can read query params under keep-alive. The tab KEY
 * ignores the query, so query changes don't fork a new tab.
 */
export function deriveTabMeta(routes: RouteDef[], pathname: string, search = ''): TabMeta | null {
  const res = resolveRoute(routes, pathname)
  if (!res || !res.def.tab) return null
  const { def, params } = res
  const spec = def.tab
  // Destructuring `def` from `res` drops the narrowing of the `!res.def.tab`
  // guard above; re-assert it so `spec` is `TabSpec`, not `TabSpec | undefined`.
  if (!spec) return null
  const key =
    spec.keyStrategy === 'param' && spec.paramName
      ? `${def.path}:${params[spec.paramName] ?? ''}`
      : def.path
  const title = typeof spec.title === 'function' ? spec.title(params) : spec.title
  return {
    key,
    title,
    kind: 'page',
    path: pathname + search,
    icon: spec.icon,
    pinned: spec.pinned ?? false,
    closable: spec.pinned ? false : spec.closable ?? true,
  }
}
