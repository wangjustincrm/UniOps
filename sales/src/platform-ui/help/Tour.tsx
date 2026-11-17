import * as RP from '@radix-ui/react-popover'
import { useCallback, useContext, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { findAnchor } from './anchor'
import { HelpContext } from './context'
import type { TourStep } from './types'

interface Box { top: number; left: number; width: number; height: number }

/** Walks a tour: the step's element is lit (the rest of the page dimmed, still clickable through) and a
 *  box beside it says what it is, with Back / Next / Done and "2 of 5". Scrolls the element into view in
 *  whatever scrolls it (a table, a sheet, the page). A step whose element isn't there is skipped. Esc
 *  or Done ends it. */
export function Tour({ steps, onEnd }: { steps: TourStep[]; onEnd: () => void }) {
  const ctx = useContext(HelpContext)
  const textOf = useCallback((step: TourStep, id: string) => ctx?.source(step.text ?? id), [ctx])
  // The first step from `from` (going `dir`) whose element is shown and has a text: its index and the
  // element's help id; null when there is none that way.
  const shownFrom = useCallback((from: number, dir: 1 | -1): { i: number; id: string } | null => {
    for (let i = from; i >= 0 && i < steps.length; i += dir) {
      const el = findAnchor(steps[i].anchor)
      const id = el?.getAttribute('data-help') ?? ''
      if (el && textOf(steps[i], id)) return { i, id }
    }
    return null
  }, [steps, textOf])
  // undefined: not looked yet (the page renders first: the first step is found once it is on screen).
  const [at, setAt] = useState<{ i: number; id: string } | null | undefined>(undefined)
  const [box, setBox] = useState<Box | null>(null)

  useEffect(() => {
    const t = setTimeout(() => setAt(shownFrom(0, 1)))
    return () => clearTimeout(t)
  }, [shownFrom])
  useEffect(() => { if (at === null) onEnd() }, [at, onEnd])
  // Light the step's element, and keep following it (scrolling, resizing).
  useEffect(() => {
    if (!at) return
    const el = findAnchor(steps[at.i].anchor)
    if (!el) return
    el.scrollIntoView?.({ block: 'center', inline: 'nearest' })
    let frame = 0
    const measure = () => {
      const r = el.getBoundingClientRect()
      setBox({ top: r.top - 4, left: r.left - 4, width: r.width + 8, height: r.height + 8 })
    }
    const loop = () => { measure(); frame = requestAnimationFrame(loop) }
    measure()
    frame = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(frame)
  }, [at, steps])
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onEnd() }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [onEnd])

  const go = (d: 1 | -1) => {
    if (!at) return
    const next = shownFrom(at.i + d, d)
    if (next) setAt(next)
    else if (d === 1) onEnd()
  }
  const entry = at ? textOf(steps[at.i], at.id) : undefined
  const i = at?.i ?? 0
  const hasNext = !!at && shownFrom(at.i + 1, 1) !== null
  const hasBack = !!at && shownFrom(at.i - 1, -1) !== null
  if (!box || !entry) return null
  const shown = steps.length
  // Portalled to the body: a page's container (a keep-alive tab, a transform) would move a fixed box.
  return createPortal(
    <>
      <div aria-hidden data-tour-light className="pointer-events-none fixed z-[60] rounded-lg ring-2 ring-primary-500"
        style={{ ...box, boxShadow: '0 0 0 9999px rgb(0 0 0 / 0.35)' }} />
      <RP.Root open>
        <RP.Anchor asChild><div className="pointer-events-none fixed z-[60]" style={box} /></RP.Anchor>
        <RP.Portal>
          <RP.Content data-tour-step role="dialog" aria-label={entry.title ?? 'Tour'} side="bottom" align="start" sideOffset={10} collisionPadding={12}
            onOpenAutoFocus={(e) => { e.preventDefault(); (e.currentTarget as HTMLElement).focus() }} tabIndex={-1}
            onInteractOutside={(e) => e.preventDefault()}
            className="z-[61] w-[min(20rem,calc(100vw-1.5rem))] rounded-lg border border-neutral-200 bg-white p-3 text-sm text-neutral-700 shadow-xl focus:outline-none">
            {entry.title && <p className="mb-1 font-semibold text-neutral-900">{entry.title}</p>}
            <p className="leading-snug">{entry.body}</p>
            <div className="mt-3 flex items-center justify-between gap-2">
              <span className="text-xs tabular-nums text-neutral-500">{i + 1} of {shown}</span>
              <div className="flex gap-1.5">
                <button type="button" onClick={onEnd} className="rounded px-2 py-1 text-xs text-neutral-500 hover:text-neutral-800">End</button>
                {hasBack && <button type="button" onClick={() => go(-1)} className="rounded border border-neutral-200 px-2 py-1 text-xs hover:bg-neutral-50">Back</button>}
                {hasNext
                  ? <button type="button" onClick={() => go(1)} className="rounded bg-primary-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-primary-600/90">Next</button>
                  : <button type="button" onClick={onEnd} className="rounded bg-primary-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-primary-600/90">Done</button>}
              </div>
            </div>
            <RP.Arrow className="fill-white" />
          </RP.Content>
        </RP.Portal>
      </RP.Root>
    </>,
    document.body,
  )
}
