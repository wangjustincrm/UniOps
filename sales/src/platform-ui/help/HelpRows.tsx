import { ChevronLeft, Play, Sparkles, X } from 'lucide-react'
import type { HelpRow } from './rows'
import { GROUPS } from './tourOrder'

const btn = 'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-600'

/** The drawer's tours: the page's whole tour, and only its new stops (when there are any). */
export function TourButtons({ total, fresh, onAll, onNew }: { total: number; fresh: number; onAll: () => void; onNew: () => void }) {
  if (total === 0) return null
  return (
    <div role="group" aria-label="Tours" className="flex flex-col gap-2 rounded-lg border border-neutral-200 p-2.5">
      <button type="button" onClick={onAll} className={`${btn} justify-between bg-primary-600 text-white hover:bg-primary-600/90`}>
        <span className="inline-flex items-center gap-1.5"><Play className="h-4 w-4" aria-hidden />Show me around this page</span>
        <span className="tabular-nums opacity-80">{total}</span>
      </button>
      {fresh > 0 && (
        <button type="button" onClick={onNew} className={`${btn} justify-between border border-primary-200 bg-primary-50 text-primary-800 hover:bg-primary-100`}>
          <span className="inline-flex items-center gap-1.5"><Sparkles className="h-4 w-4" aria-hidden />What's new here</span>
          <span className="tabular-nums">{fresh}</span>
        </button>
      )}
    </div>
  )
}

function Row({ r, onShow }: { r: HelpRow; onShow: (r: HelpRow) => void }) {
  return (
    <li data-help-row={r.slot} className="flex items-start justify-between gap-2 py-1.5">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-1.5 font-medium text-neutral-900">
          {r.title}
          {!r.seen && <span className="rounded-full bg-primary-100 px-1.5 text-[10px] font-semibold uppercase tracking-wide text-primary-800">New</span>}
        </p>
        <p className="leading-snug text-neutral-600">{r.body}</p>
      </div>
      {r.anchor && (
        <button type="button" onClick={() => onShow(r)} aria-label={`Show ${r.title}`}
          className="shrink-0 rounded border border-neutral-200 px-2 py-0.5 text-xs text-primary-700 hover:bg-primary-50">Show</button>
      )}
    </li>
  )
}

/** Tips as rows, by group (GROUPS' order): a page's own flat; a shared group (the same on every list or
 *  record) folded once all of it is seen, with how many are. */
export function StopRows({ rows, onShow, labels }: { rows: HelpRow[]; onShow: (r: HelpRow) => void; labels?: Record<string, string> }) {
  const groups = Object.keys(GROUPS).filter((g) => rows.some((r) => r.group === g))
  const other = [...new Set(rows.map((r) => r.group))].filter((g) => !GROUPS[g])
  return (
    <div className="flex flex-col gap-3">
      {[...groups, ...other].map((g) => {
        const list = rows.filter((r) => r.group === g)
        const seen = list.filter((r) => r.seen).length
        const items = <ul className="divide-y divide-neutral-100">{list.map((r) => <Row key={r.slot} r={r} onShow={onShow} />)}</ul>
        const label = labels?.[g] ?? GROUPS[g]?.label ?? g
        return GROUPS[g]?.shared ? (
          <details key={g} open={seen < list.length} aria-label={label} className="rounded-md border border-neutral-200 px-2.5 py-1.5">
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-neutral-500">
              {label} · {seen === list.length ? `${seen} ✓` : `${list.length - seen} new`}
            </summary>
            {items}
          </details>
        ) : (
          <section key={g} aria-label={label}>
            <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">{label}</p>
            {items}
          </section>
        )
      })}
    </div>
  )
}

/** Every tip of the app in the drawer: by group, seen or new, with Show for those on this page. */
export function TipsCatalog({ rows, title, onBack, onShow }: { rows: HelpRow[]; title: string; onBack: () => void; onShow: (r: HelpRow) => void }) {
  return (
    <section aria-label={title} className="flex flex-col gap-3">
      <button type="button" onClick={onBack} className="inline-flex items-center gap-1 self-start text-sm text-primary-700 hover:underline">
        <ChevronLeft className="h-4 w-4" aria-hidden />Back
      </button>
      <p className="font-semibold text-neutral-900">{title}</p>
      <StopRows rows={rows} onShow={onShow} labels={{ page: 'On pages and records' }} />
    </section>
  )
}

/** Beside the page's ?: a first visit ever, "New here? Show me" (the whole tour); later, "N new on this
 *  page · Show" (only those); nothing new: nothing. × counts them as seen. */
export function NewHint({ firstEver, fresh, total, onAll, onNew, onDismiss }: {
  firstEver: boolean; fresh: number; total: number; onAll: () => void; onNew: () => void; onDismiss: () => void
}) {
  if (total === 0 || fresh === 0) return null
  return (
    <span data-help-new className="inline-flex items-center gap-1 rounded-full border border-primary-200 bg-primary-50 pl-2 text-xs text-primary-800">
      {firstEver
        ? <button type="button" onClick={onAll} className="font-medium hover:underline">New here? Show me</button>
        : <button type="button" onClick={onNew} className="font-medium hover:underline">{fresh} new on this page · Show</button>}
      <button type="button" aria-label="Dismiss" onClick={onDismiss} className="rounded-full p-1 text-primary-700 hover:bg-primary-100">
        <X className="h-3 w-3" aria-hidden />
      </button>
    </span>
  )
}
