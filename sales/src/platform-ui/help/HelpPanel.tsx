import * as RD from '@radix-ui/react-dialog'
import { CircleHelp, Search, X } from 'lucide-react'
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { findAnchor } from './anchor'
import { ManagerContext, ScopeContext } from './tourContext'
import { planStops, type PlannedStop } from './tourStops'
import { useScopePlan } from './useScopePlan'
import { catalogRows, pageRows, seenKey, type HelpRow } from './rows'
import { NewHint, StopRows, TipsCatalog, TourButtons } from './HelpRows'
import { seenStore, useSeen } from './seen'
import { DEFAULT_SLOTS } from './tourOrder'
import { Tour } from './Tour'
import { HelpContext } from './context'
import { searchHelp } from './search'
import { useHelp } from './useHelp'
import type { HelpEntry, HelpId, TourStep } from './types'

/** A page's help (the user, 2026-11-17: designed first, built from small parts): a ? beside its title
 *  (or the "?" key, through TourManager) opens a drawer (full width on a phone):
 *  - search over every help text, each result on this page with a Show;
 *  - the page's text, then its tours (TourButtons): the whole tour, and "What's new here" (only the stops
 *    this viewer hasn't seen, per stop not per page: a tool seen on one list isn't new on the next);
 *  - its tips (StopRows): its stops in tour order with New / Show, then `sections`' ids (tips without a
 *    place, e.g. how to sort), the shared groups folded once seen; its common tasks;
 *  - "All <app> tips" (TipsCatalog, in the drawer), "Replay all tips", "Mark all as seen";
 *  - in development, the Tour inspector.
 *  Beside the ?, NewHint: a first visit ever, "New here? Show me"; later "N new on this page · Show".
 *  The tour is the page entry's own `tour`, else the stops registered in its TourScope (slot order). */
export function PageHelp({ id, page, sections }: { id: HelpId; page: string; sections?: { title: string; ids: string[] }[] }) {
  const entry = useHelp(id)
  const ctx = useContext(HelpContext)
  const scope = useContext(ScopeContext)
  const manager = useContext(ManagerContext)
  const seen = useSeen()
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<'page' | 'catalog'>('page')
  const [local, setLocal] = useState<TourStep[] | null>(null)  // without a TourManager (a test, another app)
  const plan = useScopePlan(open)  // current: re-planned as stops come and go, and when the drawer opens
  const trigger = useRef<HTMLButtonElement>(null)
  const all = useMemo(() => (ctx?.catalog ? ctx.catalog() : []), [ctx])
  const hits = useMemo(() => searchHelp(all, q), [all, q])
  const scopeId = scope?.id ?? id
  const sources = useMemo(() => ({
    scope: scopeId,
    text: (h: string) => ctx?.source(h),
    slot: (sl: string) => manager?.slot(sl) ?? DEFAULT_SLOTS[sl],
    isSeen: (h: string, v?: number) => (seen[h] ?? 0) >= (v ?? 1),
  }), [scopeId, ctx, manager, seen])
  const extra = useMemo(() => (sections ?? []).flatMap((x) => x.ids), [sections])
  const rows = useMemo(() => pageRows(plan, extra, sources), [plan, extra, sources])
  // A tour stop (the slot table's `tour`, default on) is in the tours; a tip only (tour: false) is a row
  // with Show, seen once read in the drawer like a tip with no place on the page.
  const inTour = (sl: string) => sources.slot(sl)?.tour !== false
  const shownPlan = plan.filter((p) => p.shown && inTour(p.slot))
  const keyOf = (sl: string) => seenKey(sl, sources.slot(sl)?.group, scopeId)
  const fresh = shownPlan.filter((p) => !sources.isSeen(keyOf(p.slot), sources.slot(p.slot)?.v))
  const firstEver = Object.keys(seen).length === 0
  const catalog = manager ? catalogRows(manager.slots(), new Map(shownPlan.map((p) => [p.slot, p.help])), sources) : []
  const versioned = (sl: string) => ({ id: keyOf(sl), v: sources.slot(sl)?.v })

  const run = useCallback((steps: TourStep[]) => {
    setOpen(false)
    const back = () => trigger.current?.focus()  // the tour ends where it started
    if (manager) manager.start(steps, null, back)
    else setLocal(steps)
  }, [manager])
  const toSteps = (list: PlannedStop[]) => list.map((p) => ({ anchor: p.help, text: p.text, slot: p.slot, seen: keyOf(p.slot) }))
  const tourAll = () => run(entry?.tour ?? toSteps(planStops(scope?.stops() ?? [], manager?.order).filter((p) => p.shown && inTour(p.slot))))
  const tourNew = () => run(toSteps(planStops(scope?.stops() ?? [], manager?.order)
    .filter((p) => p.shown && inTour(p.slot) && !seenStore.isSeen(keyOf(p.slot), sources.slot(p.slot)?.v))))
  const showOne = (r: HelpRow) => { if (r.anchor) run([{ anchor: r.anchor, text: r.slot, slot: r.slot, seen: r.key }]) }

  // A tip with no place on the page (e.g. how to sort) is seen by being read: shown in the open drawer
  // (its group is open while it is new) or in the catalog for a second.
  const unreadText = (view === 'catalog' ? catalog : rows).filter((r) => (!r.anchor || !inTour(r.slot)) && !r.seen)
  useEffect(() => {
    if (!open || q.trim() || unreadText.length === 0) return
    const t = setTimeout(() => seenStore.mark(unreadText.map((r) => ({ id: r.key, v: r.v }))), 1000)
    return () => clearTimeout(t)
  })
  useEffect(() => manager?.registerHelp({ trigger: () => trigger.current, open: () => setOpen(true) }), [manager])
  if (!entry) return null
  const total = entry.tour ? entry.tour.length : shownPlan.length
  const appTips = `All ${manager?.appName ? `${manager.appName} ` : ''}tips`
  return (
    <>
    {local && <Tour steps={local} onEnd={() => { setLocal(null); trigger.current?.focus() }}
      onShown={(st) => { if (st.slot) seenStore.mark([{ id: st.seen ?? st.slot, v: sources.slot(st.slot)?.v }]) }} />}
    <RD.Root open={open} onOpenChange={(o) => { setOpen(o); if (!o) { setQ(''); setView('page') } }}>
      <RD.Trigger asChild>
        <button ref={trigger} type="button" data-help={id} aria-label={`Help: ${page}`} aria-keyshortcuts="?"
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-neutral-500 hover:bg-neutral-100 hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
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
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-neutral-500" aria-hidden />
              <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search help (a word, a status, a document…)"
                className="h-9 w-full rounded-md border border-neutral-200 bg-white pl-8 pr-2 text-sm placeholder:text-neutral-500 focus:outline-none focus:ring-2 focus:ring-primary-500" />
            </label>
            {q.trim() ? (
              <section aria-label="Search results" className="flex flex-col gap-2">
                {hits.length === 0 ? <p className="text-neutral-500">Nothing found for “{q.trim()}”.</p>
                  : hits.map((h) => <Hit key={h.id} entry={h.entry} onShow={findAnchor(h.id) ? () => run([{ anchor: h.id, slot: sources.slot(h.id) ? h.id : undefined }]) : undefined} />)}
              </section>
            ) : view === 'catalog' ? (
              <TipsCatalog rows={catalog} title={`${appTips} (${catalog.length} · ${catalog.filter((r) => !r.seen).length} new)`}
                onBack={() => setView('page')} onShow={showOne} />
            ) : (
              <section aria-label="This page" className="flex flex-col gap-4">
                <p className="leading-relaxed">{entry.body}</p>
                {entry.about && (
                  <div>
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">{entry.about.title}</p>
                    <p className="leading-relaxed">{entry.about.text}</p>
                  </div>
                )}
                <TourButtons total={total} fresh={entry.tour ? 0 : fresh.length} onAll={tourAll} onNew={tourNew} />
                {rows.length > 0 && <StopRows rows={rows} onShow={showOne} />}
                {entry.tasks && entry.tasks.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">Common tasks</p>
                    <ul className="list-disc space-y-1 pl-5">{entry.tasks.map((t) => <li key={t}>{t}</li>)}</ul>
                  </div>
                )}
                {manager && (
                  <div className="flex flex-col gap-1 border-t border-neutral-100 pt-3 text-sm">
                    <button type="button" onClick={() => setView('catalog')} className="self-start font-medium text-primary-700 hover:underline">
                      {appTips} ({catalog.length} · {catalog.filter((r) => !r.seen).length} new) ›
                    </button>
                    <span className="flex flex-wrap gap-x-3 text-xs">
                      <button type="button" onClick={() => seenStore.reset()} className="text-neutral-600 hover:underline">Replay all tips</button>
                      <button type="button" onClick={() => seenStore.mark([...catalog, ...rows].map((r) => ({ id: r.key, v: r.v })))} className="text-neutral-600 hover:underline">Mark all as seen</button>
                    </span>
                    <p className="text-xs text-neutral-500">Tip: press ? on any page to open its help.</p>
                  </div>
                )}
                {import.meta.env.DEV && <Inspector plan={plan} text={(h) => !!ctx?.source(h)} />}
              </section>
            )}
          </div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
    <NewHint firstEver={firstEver} fresh={entry.tour ? 0 : fresh.length} total={total} onAll={tourAll} onNew={tourNew}
      onDismiss={() => seenStore.mark(fresh.map((p) => versioned(p.slot)))} />
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
  const seen = useSeen()
  const me = useRef<HTMLButtonElement>(null)
  const plan = useScopePlan().filter((p) => p.shown && manager?.slot(p.slot)?.tour !== false)
  if (!manager || plan.length === 0) return null
  const keyOf = (sl: string) => seenKey(sl, manager.slot(sl)?.group, scope?.id ?? 'record')
  const fresh = plan.some((p) => (seen[keyOf(p.slot)] ?? 0) < (manager.slot(p.slot)?.v ?? 1))
  return (
    <button ref={me} type="button" aria-label={fresh ? 'Show me around (new)' : 'Show me around'} title="Show me around"
      onClick={() => {
        const steps = planStops(scope?.stops() ?? [], manager.order).filter((s) => s.shown && manager.slot(s.slot)?.tour !== false)
          .map((s) => ({ anchor: s.help, text: s.text, slot: s.slot, seen: keyOf(s.slot) }))
        manager.start(steps, me.current?.closest('[role="dialog"]'), () => me.current?.focus())
      }}
      className={className ?? 'relative inline-flex h-7 w-7 items-center justify-center rounded-md text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600'}>
      <CircleHelp className="h-4 w-4" aria-hidden />
      {fresh && <span aria-hidden className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-primary-600" />}
    </button>
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
