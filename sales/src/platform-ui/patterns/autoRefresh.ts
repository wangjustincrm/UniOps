import { AUTO_REFRESH } from '../resource/viewState'

const KEY = (basePath: string) => `platform-ui.autoRefresh.${basePath}`

/** The auto-refresh a user last chose for a list (no saved view carries one). */
export function storedAutoRefresh(basePath: string): number {
  try {
    const n = Number(localStorage.getItem(KEY(basePath)) ?? 0)
    return (AUTO_REFRESH as readonly number[]).includes(n) ? n : 0
  } catch {
    return 0  // no storage (private mode): off
  }
}
export function storeAutoRefresh(basePath: string, seconds: number): void {
  try { localStorage.setItem(KEY(basePath), String(seconds)) } catch { /* not remembered */ }
}
