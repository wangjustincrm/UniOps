import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Tour } from './Tour'
import { ManagerContext } from './tourContext'
import { DEFAULT_SLOTS } from './tourOrder'
import type { TourStep } from './types'

/** The one place tours are managed (the user, 2026-11-17), mounted once at the app's root: the slot
 *  order (DEFAULT_SLOTS, `slots` overriding by name), the one tour on screen (Esc ends it first; a sheet
 *  it runs in stays open), and the "?" key (it opens the help of the page in view; never while typing or
 *  with a dialog open). Pages and features never pick numbers or draw tours. */
export function TourManager({ slots, children }: { slots?: Record<string, number>; children: ReactNode }) {
  const order = useCallback((s: string) => slots?.[s] ?? DEFAULT_SLOTS[s] ?? 999, [slots])
  const [active, setActive] = useState<{ steps: TourStep[]; container?: Element | null; onEnd?: () => void } | null>(null)
  const helps = useRef(new Set<{ trigger: () => HTMLElement | null; open: () => void }>())
  const value = useMemo(() => ({
    order,
    start: (steps: TourStep[], container?: Element | null, onEnd?: () => void) => setActive({ steps, container, onEnd }),
    registerHelp: (h: { trigger: () => HTMLElement | null; open: () => void }) => { helps.current.add(h); return () => { helps.current.delete(h) } },
  }), [order])
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key !== '?' || e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || document.querySelector('[role="dialog"]')) return
      // The help of the page in view: the first shown ? in page order (a hidden keep-alive tab's has no box).
      const shown = [...helps.current].map((h) => ({ h, el: h.trigger() }))
        .filter((x): x is { h: typeof x.h; el: HTMLElement } => !!x.el && x.el.getBoundingClientRect().width > 0)
        .sort((a, b) => (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1))
      if (!shown[0]) return
      e.preventDefault()
      shown[0].h.open()
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [])
  const end = useCallback(() => { const done = active?.onEnd; setActive(null); done?.() }, [active])
  return (
    <ManagerContext.Provider value={value}>
      {children}
      {active && <Tour steps={active.steps} container={active.container} onEnd={end} />}
    </ManagerContext.Provider>
  )
}

const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))
