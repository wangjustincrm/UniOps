import * as RD from '@radix-ui/react-dialog'
import { CircleHelp, Search, X } from 'lucide-react'
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { findAnchor } from './anchor'
import { ManagerContext, ScopeContext } from './tourContext'
import { planStops, type PlannedStop } from './tourStops'
import { Tour } from './Tour'
import { HelpContext } from './context'
import { searchHelp } from './search'
import { useHelp } from './useHelp'
import type { HelpEntry, HelpId, TourStep } from './types'

/** A page's help: a ? beside its title (or the "?" key, through TourManager) opening a side panel (full
 *  width on a phone) with what the page is for, "Show me" (its tour), its common tasks, the `sections`
 *  (e.g. "Using lists") and a search over every help text, each result that is on this page with a Show
 *  that lights it. The tour is the page entry's own `tour`, else the stops registered in its TourScope,
 *  in slot order (TourManager draws it). A first visit shows "New here? Show me" beside the ? until it is
 *  used or closed. In development the panel lists the page's stops (Tour inspector). No text: nothing. */
export function PageHelp({ id, page, sections }: { id: HelpId; page: string; sections?: { title: string; ids: string[] }[] }) {
  const entry = useHelp(id)
  const ctx = useContext(HelpContext)
  const scope = useContext(ScopeContext)
  const manager = useContext(ManagerContext)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [local, setLocal] = useState<TourStep[] | null>(null)  // without a TourManager (a test, another app)
  const [fresh, setFresh] = useState(() => !seen(id))
  const [plan, setPlan] = useState<PlannedStop[]>([])
  const trigger = useRef<HTMLButtonElement>(null)
  const all = useMemo(() => (ctx?.catalog ? ctx.catalog() : []), [ctx])
  const hits = useMemo(() => searchHelp(all, q), [all, q])
  const dismiss = useCallback(() => { markSeen(id); setFresh(false) }, [id])
  const pageSteps = useCallback((): TourStep[] => entry?.tour
    ?? planStops(scope?.stops() ?? [], manager?.order).filter((s) => s.shown).map((s) => ({ anchor: s.help })), [entry, scope, manager])
  const tour = useCallback((only?: TourStep[]) => {
    dismiss()
    setOpen(false)
    const steps = only ?? pageSteps()
    const back = () => trigger.current?.focus()  // the tour ends where it started
    if (manager) manager.start(steps, null, back)
    else setLocal(steps)
  }, [dismiss, pageSteps, manager])
  // The page's stops as they are now (once it is on screen, and on each open): with none, no "Show me".
  useEffect(() => {
    const t = setTimeout(() => setPlan(planStops(scope?.stops() ?? [], manager?.order)), 300)
    return () => clearTimeout(t)
  }, [scope, manager, open])
  const stops = entry?.tour ? entry.tour.length : plan.filter((s) => s.shown).length
  useEffect(() => manager?.registerHelp({ trigger: () => trigger.current, open: () => { dismiss(); setOpen(true) } }), [manager, dismiss])
  if (!entry) return null
  return (
    <>
    {local && <Tour steps={local} onEnd={() => { setLocal(null); trigger.current?.focus() }} />}
    <RD.Root open={open} onOpenChange={(o) => { setOpen(o); if (o) dismiss(); else setQ('') }}>
      <RD.Trigger asChild>
        <button ref={trigger} type="button" data-help={id} aria-label={`Help: ${page}`} aria-keyshortcuts="?"
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
          <CircleHelp className="h-4 w-4" aria-hidden />
        </button>
      </RD.Trigger>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-black/20" />
        <RD.Content data-help-panel aria-describedby={undefined}
          className="fixed inset-y-0 right-0 z-50 flex w-full flex-col bg-white shadow-xl focus:outline-none sm:w-[26rem] sm:max-w-[90vw]">
          <div className="flex items-start justify-between gap-3 border-b border-neutral-100 px-4 py-3">
            <RD.Title className="text-base font-semibold text-neutral-900">Help: {page}</RD.Title>
            <RD.Close aria-label="Close" className="rounded p-1 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-800">
              <X className="h-4 w-4" aria-hidden />
            </RD.Close>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4 text-sm text-neutral-700">
            <label className="relative block">
              <span className="sr-only">Search help</span>
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-neutral-400" aria-hidden />
              <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search help (a word, a status, a document…)"
                className="h-9 w-full rounded-md border border-neutral-200 bg-white pl-8 pr-2 text-sm placeholder:text-neutral-400 focus:outline-none focus:ring-2 focus:ring-primary-500" />
            </label>
            {q.trim() ? (
              <section aria-label="Search results" className="flex flex-col gap-2">
                {hits.length === 0 ? <p className="text-neutral-500">Nothing found for “{q.trim()}”.</p>
                  : hits.map((h) => <Hit key={h.id} entry={h.entry} onShow={findAnchor(h.id) ? () => tour([{ anchor: h.id }]) : undefined} />)}
              </section>
            ) : (
              <section aria-label="This page" className="flex flex-col gap-3">
                <p className="leading-relaxed">{entry.body}</p>
                {stops > 0 && (
                  <button type="button" onClick={() => tour()}
                    className="self-start rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-600/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-600">
                    Show me
                  </button>
                )}
                {entry.tasks && entry.tasks.length > 0 && (
                  <div>
                    <p className="mb-1 font-medium text-neutral-900">Common tasks</p>
                    <ul className="list-disc space-y-1 pl-5">{entry.tasks.map((t) => <li key={t}>{t}</li>)}</ul>
                  </div>
                )}
                {sections?.map((sec) => <Section key={sec.title} title={sec.title} ids={sec.ids} />)}
                {manager && <p className="text-xs text-neutral-500">Tip: press ? on any page to open its help.</p>}
                {import.meta.env.DEV && <Inspector plan={plan} text={(h) => !!ctx?.source(h)} />}
              </section>
            )}
          </div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
    {fresh && stops > 0 && (
      <span data-help-new className="inline-flex items-center gap-1 rounded-full border border-primary-200 bg-primary-50 pl-2 text-xs text-primary-800">
        <button type="button" onClick={() => tour()} className="font-medium hover:underline">New here? Show me</button>
        <button type="button" aria-label="Dismiss" onClick={dismiss} className="rounded-full p-1 text-primary-700 hover:bg-primary-100">
          <X className="h-3 w-3" aria-hidden />
        </button>
      </span>
    )}
    </>
  )
}

/** Development only: the page's tour stops as the manager sees them (order, slot, help id), and the ones
 *  that can't be shown now or have no text. */
export function Inspector({ plan, text }: { plan: PlannedStop[]; text: (help: string) => boolean }) {
  return (
    <details data-tour-inspector className="rounded-md border border-dashed border-neutral-300 p-2 text-xs">
      <summary className="cursor-pointer font-medium text-neutral-700">Tour inspector ({plan.length} stops)</summary>
      {plan.length === 0 ? <p className="mt-1 text-neutral-500">No stop is registered on this page.</p> : (
        <ol className="mt-1 flex flex-col gap-0.5">
          {plan.map((s) => (
            <li key={s.key} className="flex flex-wrap gap-x-2 tabular-nums">
              <span className="w-6 text-right text-neutral-500">{s.order}</span>
              <span className="font-mono">{s.slot}</span>
              <span className="font-mono text-neutral-500">{s.help}</span>
              {!s.shown && <span className="text-warning-700">hidden now</span>}
              {!text(s.help) && <span className="text-danger-600">no text</span>}
            </li>
          ))}
        </ol>
      )}
    </details>
  )
}

/** A record's "Show me around": its own tour (the stops in its TourScope), drawn inside its sheet. Only
 *  shown when it has stops. */
export function ShowMeAround({ className }: { className?: string }) {
  const scope = useContext(ScopeContext)
  const manager = useContext(ManagerContext)
  const me = useRef<HTMLButtonElement>(null)
  const [count, setCount] = useState(0)
  useEffect(() => {
    const t = setTimeout(() => setCount(planStops(scope?.stops() ?? [], manager?.order).filter((s) => s.shown).length), 300)
    return () => clearTimeout(t)
  })
  if (!manager || count === 0) return null
  return (
    <button ref={me} type="button" aria-label="Show me around" title="Show me around"
      onClick={() => {
        const steps = planStops(scope?.stops() ?? [], manager.order).filter((s) => s.shown).map((s) => ({ anchor: s.help }))
        manager.start(steps, me.current?.closest('[role="dialog"]'), () => me.current?.focus())
      }}
      className={className ?? 'inline-flex h-7 w-7 items-center justify-center rounded-md text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600'}>
      <CircleHelp className="h-4 w-4" aria-hidden />
    </button>
  )
}

// Whether this viewer has seen a page's help (a convenience: kept in this browser only; unreadable → not seen).
const seenKey = (id: string) => `help-seen:${id}`
function seen(id: string): boolean {
  try { return localStorage.getItem(seenKey(id)) === '1' } catch { return false }
}
function markSeen(id: string) {
  try { localStorage.setItem(seenKey(id), '1') } catch { /* private window: shown again next time */ }
}

function Section({ title, ids }: { title: string; ids: string[] }) {
  const ctx = useContext(HelpContext)
  const items = ids.map((i) => ({ id: i, entry: ctx?.source(i) })).filter((x): x is { id: string; entry: HelpEntry } => !!x.entry)
  if (items.length === 0) return null
  return (
    <section aria-label={title}>
      <p className="mb-1 font-medium text-neutral-900">{title}</p>
      <dl className="flex flex-col gap-1.5">
        {items.map(({ id, entry }) => (
          <div key={id}>
            {entry.title && <dt className="font-medium text-neutral-800">{entry.title}</dt>}
            <dd className="leading-snug">{entry.body}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function Hit({ entry, onShow }: { entry: HelpEntry; onShow?: () => void }) {
  return (
    <article className="rounded-md border border-neutral-200 p-2.5">
      <div className="flex items-start justify-between gap-2">
        {entry.title && <p className="font-medium text-neutral-900">{entry.title}</p>}
        {onShow && (
          <button type="button" onClick={onShow} className="shrink-0 rounded border border-neutral-200 px-2 py-0.5 text-xs text-primary-700 hover:bg-primary-50">
            Show
          </button>
        )}
      </div>
      <p className="leading-snug">{entry.body}</p>
      {entry.next && <p className="mt-1 leading-snug"><span className="font-medium text-neutral-900">Next: </span>{entry.next}</p>}
    </article>
  )
}
