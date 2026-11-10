// A click in a cell opens the row unless it is meant for something in the cell (2026-11-10): a
// button, link, field or menu there, anything opened from the cell in a portal (a popover, a
// confirm dialog: React bubbles it through the row), or the end of selecting text to copy. Cells
// never need to stop the event themselves.
const INTERACTIVE = 'a[href],button,input,select,textarea,label,summary,[role="button"],[role="link"],[role="checkbox"],[role="menuitem"],[role="switch"],[role="tab"],[data-no-row-click]'

export function clickOpensRow(e: { target: EventTarget; currentTarget: Element }): boolean {
  const target = e.target as Element
  if (!(target instanceof Element) || !e.currentTarget.contains(target)) return false
  const hit = target.closest(INTERACTIVE)
  if (hit && hit !== e.currentTarget && e.currentTarget.contains(hit)) return false
  return !(window.getSelection?.()?.toString() ?? '')
}
