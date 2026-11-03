import { createContext } from 'react'
import type { SheetSize } from '../primitives/sheetSizes'

/** What the list that opened a record gives its sheet: open / close, the
 *  previous and next rows, the tab asked for (a Task Inbox link), and its own
 *  refresh. A RecordSheet without a host is controlled by its own props. */
export interface RecordSheetHost {
  open: boolean
  close: () => void
  prev?: () => void
  next?: () => void
  /** "3 of 50": where the record is in the list's current rows. */
  position?: string
  /** The tab to open on (asked for), else the tab last chosen in this list. */
  tab?: string
  onTabChange?: (tab: string) => void
  refreshList?: () => Promise<unknown>
  /** The default width (the list's choice). */
  size?: RecordSheetSize
}

export const RecordSheetHostContext = createContext<RecordSheetHost | null>(null)


export type RecordSheetSize = Exclude<SheetSize, 'full'>
