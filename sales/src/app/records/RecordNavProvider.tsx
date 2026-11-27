import { useEffect, useMemo, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { deriveTabMeta, useTabStoreApi, type RouteDef } from '@uniops/shell'
import { RecordNavContext, type RecordKey, type RecordNav } from '@/platform-ui'
import { forgetClosed, rememberBack } from './backLinks'
import { RECORD_TYPES, hrefOf } from './recordTypes'

/** How platform-ui opens a record here (RefLink, "Open in its tab"): its route opens its own tab (one per record,
 *  keyStrategy 'param') or focuses it; the tab it was opened from is its "← Back to …". */
export function RecordNavProvider({ routes, children }: { routes: RouteDef[]; children: ReactNode }) {
  const api = useTabStoreApi()
  const navigate = useNavigate()
  const nav = useMemo<RecordNav>(() => {
    const keyOf = (r: RecordKey) => {
      const href = hrefOf(r)
      return href ? deriveTabMeta(routes, href)?.key : undefined
    }
    return {
      href: hrefOf,
      open: (r, sheet) => {
        const href = hrefOf(r)
        if (!href) return
        const s = api.getState()
        const from = s.tabs.find((t) => t.key === s.activeKey)
        // From a record's sheet: Back names that record, and goes to its tab, where the sheet is still open.
        const noun = sheet ? RECORD_TYPES[sheet.record.type]?.noun : undefined
        if (from?.path) rememberBack(href, { path: from.path, title: sheet ? `${noun ? `${noun} ` : ''}${sheet.title}` : from.title })
        navigate(href)
      },
      isOpen: (r) => {
        const key = keyOf(r)
        return !!key && api.getState().tabs.some((t) => t.key === key)
      },
      subscribe: (notify) => api.subscribe(notify),
    }
  }, [api, navigate, routes])
  // A record tab closed (✕, Back, Close others…): where it came from is forgotten with it.
  useEffect(() => api.subscribe((s) => forgetClosed((path) => {
    const key = deriveTabMeta(routes, path)?.key
    return !!key && s.tabs.some((t) => t.key === key)
  })), [api, routes])
  return <RecordNavContext.Provider value={nav}>{children}</RecordNavContext.Provider>
}
