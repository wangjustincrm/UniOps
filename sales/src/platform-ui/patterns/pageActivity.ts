import { createContext, useContext } from 'react'

/** Whether the page this is in is the one shown (user 2026-11-26): an app keeps pages alive in tabs, mounted but hidden.
 *  The app's layout provides it per tab (a page outside any tab is always active). Auto-refresh ticks only on an active page;
 *  coming back to a page reads it again when what it shows is older than `reReadAfterMs`. */
export interface PageActivity {
  active: boolean
}

export const PageActivityContext = createContext<PageActivity>({ active: true })

export function usePageActive(): boolean {
  return useContext(PageActivityContext).active
}

/** The platform's one setting for it: coming back to a page, what is older than this is read again at once. */
export const PAGE_ACTIVITY_OPTIONS = { reReadAfterMs: 30_000 }
