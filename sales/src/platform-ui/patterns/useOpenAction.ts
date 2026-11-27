import { useState } from 'react'
import type { RecordAction } from './recordActions'

/** The open action and its record (one dialog per menu or sheet). */
export function useOpenAction<T>() {
  const [open, setOpen] = useState<{ action: RecordAction<T>; rows: T[] } | null>(null)
  return { open, start: (action: RecordAction<T>, rows: T[]) => setOpen({ action, rows }), close: () => setOpen(null) }
}
