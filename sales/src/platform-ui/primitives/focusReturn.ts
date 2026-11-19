import { useRef } from 'react'

// A closed dialog or sheet gives the focus back to what opened it (step 6, WCAG 2.4.3). Radix does this with the
// element focused when the dialog mounts, which is too late in two cases: an ActionButton is disabled while its action
// runs (the focus has already dropped to the body), and a list row is drawn again by the refresh that follows (the
// element Radix kept is gone). So the last element the person focused is kept here, and found again by its row or id
// when it was replaced.

let last: HTMLElement | null = null
if (typeof document !== 'undefined') {
  document.addEventListener('focusin', (e) => {
    if (e.target instanceof HTMLElement && e.target !== document.body) last = e.target
  }, true)
}

/** How to find the element again once it is drawn anew: its id, or its row in a list. */
function locator(el: HTMLElement): string | null {
  if (el.id) return `#${CSS.escape(el.id)}`
  const row = el.closest<HTMLElement>('[data-row-id]')
  return row ? `[data-row-id="${CSS.escape(row.dataset.rowId ?? '')}"]` : null
}

function focusable(el: HTMLElement | null): el is HTMLElement {
  return !!el && el.isConnected && !(el as HTMLButtonElement).disabled && !el.closest('[inert], [aria-hidden="true"]')
}

/** A dialog's two Radix focus events: what had the focus is kept as it opens (before the focus moves in) and given
 *  back as it closes. Spread onto RD.Content. */
export function useReturnFocus() {
  const kept = useRef<{ el: HTMLElement; where: string | null } | null>(null)
  return {
    onOpenAutoFocus: () => {
      const a = document.activeElement
      const el = a instanceof HTMLElement && a !== document.body ? a : last
      kept.current = el ? { el, where: locator(el) } : null
    },
    onCloseAutoFocus: (e: Event) => {
      const k = kept.current
      if (!k) return  // nothing known: Radix's own
      e.preventDefault()
      const back = () => {
        // Found again: the first match that is shown (the same list may be open in a hidden keep-alive tab).
        const el = focusable(k.el) ? k.el : k.where
          ? [...document.querySelectorAll<HTMLElement>(k.where)].find((x) => x.getClientRects().length > 0) ?? null : null
        if (focusable(el)) el.focus({ preventScroll: true })
      }
      back()
      // Still pending (the button re-enables a moment later) or drawn again by the refresh: once more after the frame.
      if (document.activeElement === document.body) requestAnimationFrame(back)
    },
  }
}
