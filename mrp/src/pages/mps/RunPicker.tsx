// Version picker for the Production Plan page.
//
// Runs are grouped by `horizon_start_month` (the 18-month window's first
// month). Within a group several versions can exist and exactly one plan is
// in force globally — `is_default`, the version purchasing works from.
//
// **The group only moves forward.** Once a newer group is released, every
// run of an older group becomes `superseded`: still readable forever (a
// plan is an audit record), never activatable again, because its window is
// missing the newest month of demand and making it live would leave a month
// of materials unbought. The picker says so rather than hiding those runs —
// a planner who cannot find last month's plan assumes the system lost it.
import { useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, Lock } from 'lucide-react'
import { Badge, Button } from '@uniops/shell'
import { cn } from '@/lib/utils'
import type { MpsRunSummary } from './mpsApi'

function statusVariant(run: MpsRunSummary): 'success' | 'warning' | 'neutral' {
  if (run.is_default) return 'success'
  if (run.status === 'draft') return 'warning'
  return 'neutral'
}

function statusLabel(run: MpsRunSummary): string {
  if (run.is_default) return 'Active'
  if (run.status === 'draft') return 'Draft'
  if (run.status === 'superseded') return 'Superseded'
  return 'Released'
}

function formatStamp(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString()
}

export function RunPicker({
  runs,
  selectedId,
  onSelect,
  disabled,
}: {
  runs: MpsRunSummary[]
  selectedId: string | null
  onSelect: (runId: string) => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [anchor, setAnchor] = useState<DOMRect | null>(null)

  const groups = useMemo(() => {
    const byMonth = new Map<string, MpsRunSummary[]>()
    for (const run of runs) {
      const bucket = byMonth.get(run.horizon_start_month) ?? []
      bucket.push(run)
      byMonth.set(run.horizon_start_month, bucket)
    }
    // `runs` arrives already ordered by the API (newest group first, newest
    // version first inside it); a Map preserves insertion order, so the
    // sections come out in that same order without re-sorting here and
    // risking a different answer than the server's.
    return [...byMonth.entries()]
  }, [runs])

  const selected = runs.find((r) => r.id === selectedId) ?? null

  return (
    <>
      <button
        type="button"
        disabled={disabled || runs.length === 0}
        onClick={(e) => {
          setAnchor(e.currentTarget.getBoundingClientRect())
          setOpen((v) => !v)
        }}
        className="flex min-h-[44px] items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 text-sm text-neutral-800 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <span className="font-mono">{selected ? selected.run_no : 'Select a plan'}</span>
        {selected && <Badge variant={statusVariant(selected)}>{statusLabel(selected)}</Badge>}
        <ChevronDown aria-hidden className="h-4 w-4 text-neutral-400" />
      </button>

      {/* Rendered through a portal, fixed to the trigger: the page's matrix
          is a scroll container with overflow hidden, which would clip an
          in-flow dropdown (the module's standing convention for overlays). */}
      {open && anchor && createPortal(
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className="fixed z-50 max-h-[60vh] w-80 overflow-y-auto rounded-lg border border-neutral-200 bg-white py-1 shadow-lg"
            style={{ top: anchor.bottom + 4, left: anchor.left }}
          >
            {groups.map(([month, versions]) => (
              <div key={month}>
                <p className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-neutral-400">
                  Horizon from {month}
                </p>
                {versions.map((run) => (
                  <button
                    key={run.id}
                    type="button"
                    onClick={() => { onSelect(run.id); setOpen(false) }}
                    className={cn(
                      'flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-neutral-50',
                      run.id === selectedId && 'bg-primary-50',
                    )}
                  >
                    {run.id === selectedId
                      ? <Check aria-hidden className="h-3.5 w-3.5 shrink-0 text-primary-600" />
                      : <span className="h-3.5 w-3.5 shrink-0" />}
                    <span className="flex-1 truncate font-mono text-xs">{run.run_no}</span>
                    {run.status === 'superseded' && (
                      <Lock aria-hidden className="h-3 w-3 shrink-0 text-neutral-400" />
                    )}
                    <Badge variant={statusVariant(run)}>{statusLabel(run)}</Badge>
                    <span className="w-20 shrink-0 text-right text-xs text-neutral-400">
                      {formatStamp(run.released_at ?? run.created_at)}
                    </span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </>,
        document.body,
      )}
    </>
  )
}

export function SetActiveButton({
  run,
  activeGroup,
  onActivate,
  pending,
  disabled,
}: {
  run: MpsRunSummary | null
  /** `horizon_start_month` of the plan currently in force, or null when
   *  nothing has been released yet. */
  activeGroup: string | null
  onActivate: () => void
  pending: boolean
  disabled?: boolean
}) {
  if (!run || run.is_default || run.status === 'draft') return null

  const wrongGroup = activeGroup !== null && run.horizon_start_month !== activeGroup
  return (
    <Button
      type="button"
      size="sm"
      variant="secondary"
      className="min-h-[44px]"
      // Rendered disabled rather than hidden when the group is wrong: a
      // button that vanishes reads as a broken page, and the planner needs
      // to know the rule, not just be blocked by it.
      disabled={disabled || pending || wrongGroup}
      title={wrongGroup
        ? 'The plan group only moves forward — versions can be switched within a group, never across one.'
        : 'Make this the plan purchasing works from'}
      onClick={onActivate}
    >
      Set as active
    </Button>
  )
}
