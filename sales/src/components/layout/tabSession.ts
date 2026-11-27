import { useSyncExternalStore } from 'react'

// What the Sales tab bar keeps for this session beside the shell's own tab store (stage 4, user 2026-11-27): a refresh
// tick per tab (its menu's Refresh), the tabs closed lately (Reopen closed tab, All tabs ▾), the notice the record-tab
// cap leaves, and the cap itself (a per-browser setting). Nothing here outlives a reload: tabs are not kept either.

export interface ClosedTab { key: string; path: string; title: string }
export interface TabNotice { text: string; reopen?: ClosedTab; tone: 'info' | 'warning' }

export const RECORD_TAB_CAP_DEFAULT = 12
export const RECORD_TAB_CAPS = [6, 12, 20] as const
const CAP_KEY = 'sales.recordTabCap'
const RECENT_MAX = 10

interface State { ticks: Record<string, number>; recent: ClosedTab[]; notice: TabNotice | null; cap: number; sheets: Record<string, boolean> }

function storedCap(): number {
  try {
    const n = Number(localStorage.getItem(CAP_KEY))
    return (RECORD_TAB_CAPS as readonly number[]).includes(n) ? n : RECORD_TAB_CAP_DEFAULT
  } catch {
    return RECORD_TAB_CAP_DEFAULT  // no storage (a private window): the default
  }
}

let state: State = { ticks: {}, recent: [], notice: null, cap: storedCap(), sheets: {} }
const listeners = new Set<() => void>()
const set = (next: Partial<State>) => { state = { ...state, ...next }; listeners.forEach((l) => l()) }
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }

export const tabSession = {
  get: () => state,
  subscribe,
  /** Every refresh on this tab's page runs (useRefresh watches the tick). */
  refresh: (key: string) => set({ ticks: { ...state.ticks, [key]: (state.ticks[key] ?? 0) + 1 } }),
  closed: (t: ClosedTab) => set({ recent: [t, ...state.recent.filter((r) => r.path !== t.path)].slice(0, RECENT_MAX) }),
  /** The latest closed (or the given one), taken off the list to be opened again. */
  takeClosed: (path?: string): ClosedTab | undefined => {
    const t = path ? state.recent.find((r) => r.path === path) : state.recent[0]
    if (t) set({ recent: state.recent.filter((r) => r !== t), notice: state.notice?.reopen === t ? null : state.notice })
    return t
  },
  notify: (notice: TabNotice | null) => set({ notice }),
  setCap: (cap: number) => {
    try { localStorage.setItem(CAP_KEY, String(cap)) } catch { /* no storage: this session only */ }
    set({ cap })
  },
  /** Tests: a clean session. */
  /** Whether a sheet is open in this tab's page (a dot on the tab while it is not shown). */
  sheet: (key: string, open: boolean) => { if (!!state.sheets[key] !== open) set({ sheets: { ...state.sheets, [key]: open } }) },
  reset: () => set({ ticks: {}, recent: [], notice: null, cap: storedCap(), sheets: {} }),
}

export function useTabSession<T>(pick: (s: State) => T): T {
  return useSyncExternalStore(subscribe, () => pick(state), () => pick(state))
}
