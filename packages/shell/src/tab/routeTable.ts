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
