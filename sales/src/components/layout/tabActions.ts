import { useCallback, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTabStoreApi, type RouteDef, type TabMeta } from '@uniops/shell'
import { ask, copyLink } from '@/platform-ui'
import { tabSession } from './tabSession'

/** The record tabs (one per record: a route with keyStrategy 'param') — the only ones the cap may close. */
export function recordTabTest(routes: RouteDef[]): (t: TabMeta) => boolean {
  const prefixes = routes.filter((r) => r.tab?.keyStrategy === 'param').map((r) => `${r.path}:`)
  return (t) => prefixes.some((p) => t.key.startsWith(p))
}

/** What the tab bar does with tabs (stage 4, user 2026-11-27), over the shell's store, with nothing changed in the shell:
 *  close (an unsaved one asks in the app's own dialog), close others / to the right (unsaved ones are kept and said),
 *  reopen closed, refresh a tab's page, copy its link. Every close is remembered as Recently closed. */
export function useTabActions() {
  const api = useTabStoreApi()
  const navigate = useNavigate()
  const followActive = useCallback(() => {
    const s = api.getState()
    const next = s.tabs.find((t) => t.key === s.activeKey)
    if (next?.path) navigate(next.path)
  }, [api, navigate])
  const drop = useCallback((t: TabMeta) => {
    if (t.path) tabSession.closed({ key: t.key, path: t.path, title: t.title })
    api.getState().closeTab(t.key)
  }, [api])

  return useMemo(() => {
    const find = (key: string) => api.getState().tabs.find((t) => t.key === key)
    const closeMany = (keep: (t: TabMeta, i: number) => boolean) => {
      const tabs = api.getState().tabs
      const go = tabs.filter((t, i) => t.closable && !t.pinned && !keep(t, i))
      const dirty = go.filter((t) => t.dirty)
      go.filter((t) => !t.dirty).forEach(drop)
      tabSession.notify(dirty.length ? { tone: 'warning', text: `${dirty.length} ${dirty.length === 1 ? 'tab has' : 'tabs have'} unsaved changes: kept open.` } : null)
      followActive()
    }
    return {
      close: async (key: string) => {
        const t = find(key)
        if (!t || !t.closable) return
        if (t.dirty && !(await ask({ title: `Close ${t.title}?`, description: 'It has unsaved changes: they are lost if you close it.',
          confirmLabel: 'Close anyway', cancelLabel: 'Keep it open', destructive: true }))) return
        drop(t)
        followActive()
      },
      closeOthers: (key: string) => closeMany((t) => t.key === key),
      closeToTheRight: (key: string) => {
        const at = api.getState().tabs.findIndex((t) => t.key === key)
        closeMany((_t, i) => i <= at)
      },
      reopen: (path?: string) => {
        const t = tabSession.takeClosed(path)
        if (t) navigate(t.path)  // the route opens its tab again; the page reads afresh
      },
      refresh: (key: string) => tabSession.refresh(key),
      copyLink: (key: string) => { const t = find(key); return t?.path ? copyLink(t.path) : Promise.resolve() },
      select: (key: string) => { api.getState().setActive(key); followActive() },
    }
  }, [api, drop, followActive, navigate])
}

/** The record-tab cap (user 2026-11-27, decision F): a new record tab always opens; beyond the cap the least recently
 *  used record tab that is not shown and has no unsaved input closes — said, with Reopen, and kept in Recently closed.
 *  Never a list tab, an unsaved tab or the shown one. When every other record tab is unsaved, nothing closes: a warning. */
export function useRecordTabCap(routes: RouteDef[]) {
  const api = useTabStoreApi()
  useEffect(() => {
    const isRecord = recordTabTest(routes)
    let busy = false
    let lastCount = 0
    const enforce = () => {
      if (busy) return
      const s = api.getState()
      const records = s.tabs.filter(isRecord)
      const cap = tabSession.get().cap
      const grew = records.length > lastCount
      lastCount = records.length
      if (records.length <= cap || !grew) return
      const order = (t: TabMeta) => s.lru.indexOf(t.key)
      const closable = records.filter((t) => t.key !== s.activeKey && !t.dirty && t.closable).sort((a, b) => order(a) - order(b))
      const over = records.length - cap
      busy = true
      const gone = closable.slice(0, over)
      for (const t of gone) {
        if (t.path) tabSession.closed({ key: t.key, path: t.path, title: t.title })
        api.getState().closeTab(t.key)
      }
      busy = false
      lastCount = api.getState().tabs.filter(isRecord).length
      if (gone.length === over) {
        const last = tabSession.get().recent[0]
        tabSession.notify({ tone: 'info', reopen: last, text: gone.length === 1 ? `${gone[0].title} closed to keep ${cap} open.`
          : `${gone.length} record tabs closed to keep ${cap} open.` })
      } else {
        const unsaved = records.filter((t) => t.dirty).length
        tabSession.notify({ tone: 'warning', text: `${lastCount} record tabs open, ${unsaved} with unsaved changes. Save or close some.` })
      }
    }
    return api.subscribe(enforce)
  }, [api, routes])
}

export type TabActions = ReturnType<typeof useTabActions>
