import * as RP from '@radix-ui/react-popover'
import { useCallback, useContext, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { findAnchor } from './anchor'
import { HelpContext } from './context'
import type { TourStep } from './types'

interface Box { top: number; left: number; width: number; height: number }

interface At { i: number; id: string; shown: number[] }

/** Walks a tour: the step's element is lit (the rest of the page dimmed, still clickable through) and a
 *  box beside it says what it is, with Back / Next / Done and "2 of 4" (counting the steps shown). Scrolls
 *  the element into view in whatever scrolls it (a table, a sheet, the page). A step whose element isn't
 *  there is skipped; one that goes away mid-tour (a sheet opened, the list re-rendered) is found again, or
 *  after ~300 ms still gone, the tour moves on. A step on something hidden (a tab, a menu) is skipped: there is no "open it first"
 *  yet. Esc, End or Done ends it. */
export function Tour({ steps, onEnd }: { steps: TourStep[]; onEnd: () => void }) {
  const ctx = useContext(HelpContext)
  const textOf = useCallback((step: TourStep, id: string) => ctx?.source(step.text ?? id), [ctx])
  // A step that can be shown now: its element (shown, with a box) and a text; its help id.
  const resolve = useCallback((i: number): string | null => {
    const el = findAnchor(steps[i].anchor)
    const id = el?.getAttribute('data-help') ?? ''
    return el && textOf(steps[i], id) ? id : null
  }, [steps, textOf])
  // Arrive at the first step from `from` going `dir` that can be shown, with the steps shown now (for
  // "2 of 4" and Back / Next); null: none that way.
  const arrive = useCallback((from: number, dir: 1 | -1): At | null => {
    const ids = steps.map((_, i) => resolve(i))
    const shown = ids.flatMap((id, i) => (id ? [i] : []))
    for (let i = from; i >= 0 && i < steps.length; i += dir) if (ids[i]) return { i, id: ids[i]!, shown }
    return null
  }, [steps, resolve])
  // undefined: not looked yet (the page renders first: the first step is found once it is on screen).
  const [at, setAt] = useState<At | null | undefined>(undefined)
  const [box, setBox] = useState<Box | null>(null)

  useEffect(() => {
    const t = setTimeout(() => setAt(arrive(0, 1)))
    return () => clearTimeout(t)
  }, [arrive])
  useEffect(() => { if (at === null) onEnd() }, [at, onEnd])
  // Light the step's element and follow it (scrolling, resizing). Gone or without a box: find it again;
  // still nowhere: the next step that can be shown (or end). The box is set only when it moved.
  useEffect(() => {
    if (!at) return
    let el = findAnchor(steps[at.i].anchor)
    el?.scrollIntoView?.({ block: 'center', inline: 'nearest' })
    let frame = 0
    let last = ''
    let missingSince = 0  // a refetch can replace the element for a frame or two: wait a moment before moving on
    const loop = () => {
      const r = el?.isConnected ? el.getBoundingClientRect() : null
      if (!r || r.width === 0 || r.height === 0) {
        el = findAnchor(steps[at.i].anchor)
        if (!el) {
          missingSince ||= performance.now()
          if (performance.now() - missingSince > 300) { setAt(arrive(at.i + 1, 1) ?? arrive(at.i - 1, -1)); return }
        }
        frame = requestAnimationFrame(loop)
        return
      }
      missingSince = 0
      const key = `${r.top}|${r.left}|${r.width}|${r.height}`
      if (key !== last) {
        last = key
        setBox({ top: r.top - 4, left: r.left - 4, width: r.width + 8, height: r.height + 8 })
      }
      frame = requestAnimationFrame(loop)
    }
    loop()
    return () => cancelAnimationFrame(frame)
  }, [at, steps, arrive])
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onEnd() }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [onEnd])

  const go = (d: 1 | -1) => {
    if (!at) return
    const next = arrive(at.i + d, d)
    if (next) setAt(next)
    else if (d === 1) onEnd()
  }
  const entry = at ? textOf(steps[at.i], at.id) : undefined
  const pos = at ? at.shown.indexOf(at.i) + 1 : 0
  const shown = at?.shown.length ?? 0
  const hasNext = !!at && at.shown.some((x) => x > at.i)
  const hasBack = !!at && at.shown.some((x) => x < at.i)
  if (!box || !entry) return null
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
              <span className="text-xs tabular-nums text-neutral-500">{pos} of {shown}</span>
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
