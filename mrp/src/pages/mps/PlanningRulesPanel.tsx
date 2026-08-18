// The rules this plan was built under, beside the plan.
//
// Everything here is decided in the engine (mrp-api/app/services/mps_engine.py)
// and then invisible: a planner sees a batch land in week 32 and cannot tell
// whether that was the lead time, the minimum lot, a shelf-life gate or a full
// week pushing it. Written down next to the matrix, the layout stops being
// something to reverse-engineer.
//
// ★ It reads the RUN's OWN snapshot values, not the current settings. Lead
// weeks, frozen months and the week start are all frozen onto a run when it is
// generated precisely so that changing a setting cannot silently redraw a plan
// somebody is already buying against — so a panel quoting today's settings
// would describe a plan that does not exist. Where a number is shown here, it
// is the number this version was actually built with.
import { useEffect, useState } from 'react'
import { ChevronRight, Info, X } from 'lucide-react'
import type { MpsRunDetail } from './mpsApi'

const STORAGE_KEY = 'mrp.planningRules.open'

/** 0 = Monday, so this plant's Saturday start is 5. */
const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
                   'Saturday', 'Sunday']

function Rule({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <li className="border-t border-neutral-100 px-3 py-2.5 first:border-t-0">
      <p className="text-xs font-semibold text-neutral-800">{title}</p>
      <p className="mt-0.5 text-[11px] leading-relaxed text-neutral-600">{children}</p>
    </li>
  )
}

export function PlanningRulesPanel({ run }: { run: MpsRunDetail }) {
  // Open by default: a collapsed panel of rules nobody has read yet is a panel
  // nobody will read. Remembered after that, because somebody who has closed it
  // once has made their decision.
  const [open, setOpen] = useState(() => {
    try {
      return window.localStorage.getItem(STORAGE_KEY) !== '0'
    } catch {
      return true
    }
  })

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, open ? '1' : '0')
    } catch {
      // A browser refusing storage is not a reason to break the page.
    }
  }, [open])

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={false}
        title="Show the rules this plan was built under"
        className="flex h-fit shrink-0 items-center gap-1 self-start rounded-lg border border-neutral-200 bg-white px-2 py-3 text-[11px] font-medium text-neutral-500 hover:bg-neutral-50"
      >
        <Info aria-hidden className="h-3.5 w-3.5" />
        <span className="[writing-mode:vertical-rl]">Planning rules</span>
      </button>
    )
  }

  const startDay = DAY_NAMES[run.week_start_dow] ?? `day ${run.week_start_dow}`
  const endDay = DAY_NAMES[(run.week_start_dow + 6) % 7] ?? ''
  const leadWeeks = run.production_lead_weeks

  return (
    <aside className="w-72 shrink-0 self-start rounded-lg border border-neutral-200 bg-white">
      <div className="flex items-center justify-between border-b border-neutral-200 px-3 py-2">
        <h2 className="flex items-center gap-1.5 text-xs font-semibold text-neutral-900">
          <Info aria-hidden className="h-3.5 w-3.5 text-neutral-400" />
          How this plan was built
        </h2>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="Hide the planning rules"
          className="text-neutral-400 hover:text-neutral-700"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <ul>
        {/* The title states this version's actual start day rather than the
            plant's usual Saturday: a heading that contradicts the body below it
            is worse than no heading. */}
        <Rule title={`Weeks run ${startDay} to ${endDay}`}>
          The week grid is frozen onto the version, so changing the setting later cannot
          redraw a plan somebody is already buying against.
        </Rule>

        <Rule title={`Production is scheduled ${leadWeeks} week${leadWeeks === 1 ? '' : 's'} early`}>
          A demand week's output is placed <strong>{leadWeeks}</strong> week
          {leadWeeks === 1 ? '' : 's'} ahead of it, so the goods exist by the time they
          are needed.
        </Rule>

        <Rule title="A short run is rounded up to the minimum batch">
          Starting the line for a token quantity is not worth the changeover, so a
          requirement below a product's minimum lot is <strong>rounded up to it</strong>.
          The extra is carried forward and draws down later months — demand is never
          pulled earlier to fill a batch.
        </Rule>

        <Rule title="A rounded batch is made whole, in one week">
          Splitting it puts both halves back under the minimum, which is the thing being
          avoided. If no week in its own month can take it, the search goes{' '}
          <strong>backwards first</strong> (build early), then forwards (
          <span className="text-warning-700">late — flagged amber</span>), and only when
          the whole horizon is full does it become a shortfall.
        </Rule>

        <Rule title="Shelf life limits how early it can be built">
          Pre-building is bounded by real calendar days of shelf life, not by weeks. A
          minimum-lot surplus that would outlive its shelf life is still produced — the
          batch is worth more than the waste — but it is flagged rather than hidden.
        </Rule>

        <Rule title="Capacity is per week, and a maintenance week is closed">
          Each week has its own ceiling on output and SKU count. A week shut for
          maintenance is stepped over entirely and its production moves to the others.
        </Rule>

        <Rule title={
          run.frozen_until_month
            ? `Frozen through ${run.frozen_until_month}`
            : 'Nothing is frozen in this version'
        }>
          {run.frozen_months > 0 ? (
            <>
              The first <strong>{run.frozen_months}</strong> month
              {run.frozen_months === 1 ? '' : 's'} from the current one — materials for
              them are already bought — are copied verbatim from the plan in force and
              are not re-planned. Everything after that is free to move.
            </>
          ) : (
            <>Every week in this version was planned from scratch.</>
          )}
        </Rule>

        <Rule title="One plan is in force at a time">
          Exactly one version is active across the whole factory: the one purchasing buys
          against, and the one the next version inherits its frozen months from. Plan
          groups only move forward — releasing a newer anchor supersedes the older ones
          for good.
        </Rule>
      </ul>

      <p className="flex items-start gap-1.5 border-t border-neutral-100 px-3 py-2 text-[11px] text-neutral-400">
        <ChevronRight aria-hidden className="mt-0.5 h-3 w-3 shrink-0" />
        <span>
          The numbers above are this version's own, recorded when it was generated —
          not the current settings.
        </span>
      </p>
    </aside>
  )
}
