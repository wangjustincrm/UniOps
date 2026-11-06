import type { KeyboardEvent } from 'react'

/** Marks an input whose Enter is its own: it chooses from a list, or adds what was typed. */
export const CHOOSING = { 'data-choosing': '' } as const

/** The Enter rule of a search form (put it on the form's onKeyDownCapture). While a list is open Enter
 *  chooses; once nothing is left to choose, Enter confirms and searches. So a select does not reopen, a
 *  checkbox does not swallow it, and a real button still does what it says. */
export function submitOnEnter(e: KeyboardEvent<HTMLFormElement>): void {
  if (e.key !== 'Enter' || e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return
  const el = e.target as HTMLElement
  if (el.closest('[data-choosing],[role="listbox"],[role="option"]')) return
  if (el.getAttribute('aria-expanded') === 'true' || el.tagName === 'TEXTAREA') return
  const role = el.getAttribute('role')
  if (el.tagName === 'BUTTON' && role !== 'combobox' && role !== 'checkbox') return
  e.preventDefault()
  e.stopPropagation()
  e.currentTarget.requestSubmit()
}
