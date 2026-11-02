import { useEffect } from 'react'

/** Esc closes a small pop-up menu (the approver bar's "More" on PR / PO / PA)
 *  while it is open — it used to stay open (ISS-0183). */
export function useCloseOnEscape(open: boolean, close: () => void) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, close])
}
