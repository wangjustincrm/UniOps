import { useEffect } from 'react'

/** Forms with changes not saved yet. A sheet, a dialog and prev / next ask before they drop them. */
const dirty = new Set<symbol>()

export function useReportUnsaved(isDirty: boolean): void {
  useEffect(() => {
    if (!isDirty) return undefined
    const id = Symbol('unsaved')
    dirty.add(id)
    return () => { dirty.delete(id) }
  }, [isDirty])
}

/** True when nothing is lost by leaving: nothing unsaved, or the person agreed to drop it. */
export function confirmDiscard(): boolean {
  if (dirty.size === 0) return true
  const leave = window.confirm('You have changes that are not saved. Discard them?')
  if (leave) dirty.clear()
  return leave
}
