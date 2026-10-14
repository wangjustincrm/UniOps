// Links into the Trace page. One tab per material (route /trace/:material);
// batch and date ride in the query string.
//
// The tab shell keys a tab on the PATH only, so following a link to a
// material whose tab is already open would just focus that tab with its OLD
// query (old batch / date) — the "static tab, new query" trap. Before
// navigating, push the new location into the existing tab so focusing it
// shows what the link asked for.
import { useNavigate } from 'react-router-dom'
import { useOptionalTabStoreApi } from '@uniops/shell'

/** Route pattern of the per-material Trace tab (routes.tsx). */
export const TRACE_ROUTE = '/trace/:material'

export function normCode(code: string): string {
  return code.trim().toUpperCase()
}

export function traceHref(material: string, opts: { batch?: string | null; date?: string | null } = {}): string {
  const sp = new URLSearchParams()
  if (opts.batch && opts.batch.trim()) sp.set('batch', opts.batch.trim())
  if (opts.date) sp.set('date', opts.date)
  const q = sp.toString()
  return `/trace/${encodeURIComponent(normCode(material))}${q ? `?${q}` : ''}`
}

/** Same key deriveTabMeta computes for a 'param' route. */
function tabKey(material: string): string {
  return `${TRACE_ROUTE}:${normCode(material)}`
}

/** Returns a function that points an already-open Trace tab at `href`
 *  (no-op when that material has no tab yet, or outside a tab shell). */
export function usePrimeTraceTab() {
  const tabs = useOptionalTabStoreApi()
  return (material: string, href: string) => {
    if (!tabs) return
    const key = tabKey(material)
    const st = tabs.getState()
    if (st.tabs.some((t) => t.key === key)) st.setTabPath(key, href)
  }
}

/** navigate() to a Trace tab, priming an open one first. */
export function useOpenTrace() {
  const navigate = useNavigate()
  const prime = usePrimeTraceTab()
  return (material: string, opts: { batch?: string | null; date?: string | null } = {}) => {
    const href = traceHref(material, opts)
    prime(material, href)
    navigate(href)
  }
}
