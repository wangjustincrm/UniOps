import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { seenStore } from './seen'
import { Tour } from './Tour'
import { ManagerContext } from './tourContext'
import { DEFAULT_SLOTS, type SlotDef } from './tourOrder'
import type { TourStep } from './types'

/** The one place tours are managed (the user, 2026-11-17), mounted once at the app's root: the slot
 *  table (DEFAULT_SLOTS; `slots` moves, regroups or re-versions a slot by name), the one tour on screen
 *  (each step shown counts its slot as seen; Esc ends it first, a sheet it runs in stays open), and the
 *  "?" key (it opens the help of the page in view; never while typing or with a dialog open). Pages and
 *  features never pick numbers or draw tours. */
export function TourManager({ slots, appName, children }: { slots?: Record<string, Partial<SlotDef>>; appName?: string; children: ReactNode }) {
  const table = useMemo(() => {
    const t: Record<string, SlotDef> = { ...DEFAULT_SLOTS }
    for (const [k, v] of Object.entries(slots ?? {})) t[k] = { ...{ order: 999, group: 'page' }, ...t[k], ...v }
    return t
  }, [slots])
  const [active, setActive] = useState<{ steps: TourStep[]; container?: Element | null; onEnd?: () => void } | null>(null)
  const helps = useRef(new Set<{ trigger: () => HTMLElement | null; open: () => void }>())
  const value = useMemo(() => ({
    order: (s: string) => table[s]?.order ?? 999,
    slot: (s: string) => table[s],
    slots: () => Object.entries(table),
    appName,
    start: (steps: TourStep[], container?: Element | null, onEnd?: () => void) => setActive({ steps, container, onEnd }),
    registerHelp: (h: { trigger: () => HTMLElement | null; open: () => void }) => { helps.current.add(h); return () => { helps.current.delete(h) } },
  }), [table, appName])
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
  const shown = useCallback((step: TourStep) => {
    if (step.slot) seenStore.mark([{ id: step.seen ?? step.slot, v: table[step.slot]?.v }])
  }, [table])
  return (
    <ManagerContext.Provider value={value}>
      {children}
      {active && <Tour steps={active.steps} container={active.container} onEnd={end} onShown={shown} />}
    </ManagerContext.Provider>
  )
}

const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))
