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
  /** From TabSpec.reloadOnQuery: the page is remounted when `path` changes. */
  reloadOnQuery?: boolean
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
  /**
   * The page initialises itself from its query string — a create page reached
   * by deep link, e.g. `/po/new?prId=…` or `/gr/new?poId=…`. A static key keeps
   * one tab for the route, so a link carrying a DIFFERENT query used to focus
   * the open tab with its old query and old form. With this set, such a link
   * re-points the open tab at the new path and the page is remounted, so it
   * initialises from the query it was opened with.
   */
  reloadOnQuery?: boolean
}

export interface RouteDef {
  /** react-router path pattern, e.g. '/visit/:visitId'. */
  path: string
  element: ReactNode
  /** Routes WITHOUT a tab spec (e.g. login) are not tabbable. */
  tab?: TabSpec
}
