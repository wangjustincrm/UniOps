import { createContext, useContext, useSyncExternalStore } from 'react'

// THE one way a list or a record sheet reports something (the user, 2026-11-03:
// "messages beside the refresh button make the toolbar jump"): a refresh, a
// load error, a saved view, an import, an action's result. Each list and each
// sheet has its own store (its scope); TableStatusBar shows the current notice
// in a reserved row and the session's history behind a bell. Info and success
// clear themselves after a few seconds; a warning or an error stays until it is
// dismissed or cleared by its key (a refresh that works clears 'refresh').

export type NoticeTone = 'info' | 'success' | 'warning' | 'error'

export interface NoticeInput {
  tone: NoticeTone
  text: string
  action?: { label: string; onClick: () => void }
  /** Stays until dismissed or cleared (default: warnings and errors). */
  sticky?: boolean
  /** A notice with the same key replaces the earlier one (e.g. 'refresh'). */
  key?: string
}

export interface Notice extends NoticeInput {
  id: number
  at: number
  sticky: boolean
}

export interface NoticeSnapshot {
  /** The one shown now (the latest still active). */
  current: Notice | null
  /** The session's last 20, newest first (a closed notice stays here). */
  history: Notice[]
  unseen: number
  /** Sticky notices still open besides the current one (the bar's "+N"). */
  moreSticky: number
}

export interface NoticeStore {
  notify: (n: NoticeInput) => void
  /** Clears the active notices with this key (e.g. a failed refresh once one works). */
  clear: (key: string) => void
  dismiss: (id: number) => void
  markSeen: () => void
  clearHistory: () => void
  subscribe: (listener: () => void) => () => void
  get: () => NoticeSnapshot
}

const AUTO_CLEAR_MS = 4000
const HISTORY = 20
let nextId = 1

export function createNoticeStore(): NoticeStore {
  let active: Notice[] = []
  let history: Notice[] = []
  let unseen = 0
  let snap: NoticeSnapshot = { current: null, history: [], unseen: 0, moreSticky: 0 }
  const listeners = new Set<() => void>()
  const timers = new Map<number, ReturnType<typeof setTimeout>>()
  const emit = () => {
    const current = active[active.length - 1] ?? null
    snap = { current, history, unseen, moreSticky: active.filter((n) => n.sticky && n !== current).length }
    listeners.forEach((l) => l())
  }
  const drop = (pred: (n: Notice) => boolean) => {
    for (const n of active) if (pred(n)) { clearTimeout(timers.get(n.id)); timers.delete(n.id) }
    active = active.filter((n) => !pred(n))
  }
  return {
    notify(input) {
      const sticky = input.sticky ?? (input.tone === 'error' || input.tone === 'warning')
      const n: Notice = { ...input, sticky, id: nextId++, at: Date.now() }
      if (input.key) drop((x) => x.key === input.key)
      active = [...active, n]
      // "Updating…" is a state, not news: it is not kept in the history.
      if (!(input.key === 'refresh' && input.tone === 'info' && sticky)) {
        history = [n, ...history].slice(0, HISTORY)
        unseen += 1
      }
      if (!sticky) timers.set(n.id, setTimeout(() => { drop((x) => x.id === n.id); emit() }, AUTO_CLEAR_MS))
      emit()
    },
    clear(key) { drop((x) => x.key === key); emit() },
    dismiss(id) { drop((x) => x.id === id); emit() },
    markSeen() { if (unseen) { unseen = 0; emit() } },
    clearHistory() { history = []; unseen = 0; emit() },
    subscribe(l) { listeners.add(l); return () => { listeners.delete(l) } },
    get: () => snap,
  }
}

/** The list's or sheet's store (ListPage and RecordSheet provide one each). */
export const NoticesContext = createContext<NoticeStore | null>(null)

/** Report into the list or sheet this component is in (`notify`), or nothing outside one. */
export function useNotices(): Pick<NoticeStore, 'notify' | 'clear'> & { inScope: boolean } {
  const store = useContext(NoticesContext)
  return store ? { notify: store.notify, clear: store.clear, inScope: true } : { notify: () => {}, clear: () => {}, inScope: false }
}

const EMPTY: NoticeSnapshot = { current: null, history: [], unseen: 0, moreSticky: 0 }
const noop = () => () => {}

/** What a store shows now (TableStatusBar). */
export function useNoticeSnapshot(store: NoticeStore | null): NoticeSnapshot {
  return useSyncExternalStore(store ? store.subscribe : noop, store ? store.get : () => EMPTY)
}
