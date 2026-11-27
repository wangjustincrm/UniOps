import { createContext } from 'react'

/** Where a sheet is drawn (user 2026-11-27, "a drawer belongs to its tab"): the app gives each tab's page a layer over
 *  that page only — under the tab bar, hidden with its tab — and a sheet opened in the page goes there, not over the
 *  whole window. None (no tabs, an embedded page) or a phone: the window, as before. */
export const SheetLayerContext = createContext<HTMLElement | null>(null)
