import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react'

// Records and references (user 2026-11-27). A record with a page of its own (a record type the server declares)
// opens in its own app tab, one tab per record. The app hands in how (its routes and its tabs), so platform-ui
// knows neither: `href` is the record's address, `open` goes there (opening or focusing its tab), `isOpen` says
// whether its tab is open now (a list's sheet is then read-only, and says where it is).

/** A record: its type (as /meta `ref.type` names it) and its key (an id, an item code, a lot). */
export interface RecordKey {
  type: string
  key: string | number
}

export interface RecordNav {
  /** The record's address in this app; null: the type has no page here. */
  href: (r: RecordKey) => string | null
  /** Open its tab (or focus it), remembering where it was opened from (its "← Back to …"). */
  open: (r: RecordKey) => void
  /** Whether the record's tab is open now. */
  isOpen: (r: RecordKey) => boolean
  /** Told when tabs open or close (for isOpen). */
  subscribe: (notify: () => void) => () => void
}

export const RecordNavContext = createContext<RecordNav | null>(null)

export function useRecordNav(): RecordNav | null {
  return useContext(RecordNavContext)
}

const NONE = () => () => {}

/** Whether this record's tab is open (kept current). False without a RecordNav or a record. */
export function useRecordTabOpen(r: RecordKey | undefined): boolean {
  const nav = useRecordNav()
  return useSyncExternalStore(nav?.subscribe ?? NONE, () => !!(nav && r && nav.isOpen(r)), () => false)
}

/** The record view's container: a list's side sheet (default), or the record's own tab (a page). */
export interface RecordContainer {
  kind: 'tab'
  /** "← Back to …": the app's link (it closes this tab and returns). */
  back?: ReactNode
  /** Leave the record (its own close: the back link's target). */
  close: () => void
}

export const RecordContainerContext = createContext<RecordContainer | null>(null)

/** Read-only: the record is open in its own tab, so this copy (a sheet) changes nothing — its actions that change
 *  the record are not offered and an ActionButton inside is greyed with why. */
export const ReadOnlyContext = createContext<string | null>(null)

export const READ_ONLY_HERE = 'Open in its tab to change it: this record is open there.'
