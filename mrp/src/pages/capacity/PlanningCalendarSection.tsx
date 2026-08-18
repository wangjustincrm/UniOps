// Planning Calendar — design §5.4's top-of-page section: which of the three
// week definitions (`week_calendar.py`'s `WEEK_MODES`) new MPS runs get
// generated under. Reads/writes `mrp_planning_params.week_calendar_mode`
// via `GET/PUT /params` (app/api/v1/params.py) — a flat key-value store,
// see capacityApi.ts's header note.
//
// **Changing this setting is explicitly NOT a re-bucketing operation.**
// `MpsRun.week_calendar_mode` is snapshotted at generate() time (mps.py's
// module docstring) and never re-read afterwards — a released run stays on
// whichever mode it was generated under forever, and even a draft run's
// `recalculate` reuses ITS OWN stored mode, not this live setting. So this
// form's only real effect is "which mode does the NEXT `POST /mps/runs`
// pick up" — the warning copy below says that plainly rather than letting
// a planner assume flipping this dropdown reshapes an existing plan.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import {
  capacityApi, WEEK_CALENDAR_MODES, WEEK_CALENDAR_MODE_DESCRIPTION, WEEK_START_DOWS,
  WEEK_START_DOW_LABEL, type WeekCalendarMode, type WeekStartDow,
} from './capacityApi'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

const MODE_LABEL: Record<WeekCalendarMode, string> = {
  iso_thursday: 'ISO / Thursday (default)',
  iso_first_day: 'ISO / First Day',
  month_fixed: 'Month Fixed (1, 8, 15, 22, 29)',
}

const DEFAULT_MODE: WeekCalendarMode = 'iso_thursday'
const DEFAULT_START_DOW: WeekStartDow = 0

function isWeekStartDow(v: unknown): v is WeekStartDow {
  return typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= 6
}

function isWeekCalendarMode(v: unknown): v is WeekCalendarMode {
  return typeof v === 'string' && (WEEK_CALENDAR_MODES as string[]).includes(v)
}

export function PlanningCalendarSection({ canWrite }: { canWrite: boolean }) {
  const queryClient = useQueryClient()
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [savedNote, setSavedNote] = useState<string | null>(null)

  const paramsQuery = useQuery({
    queryKey: ['mrp-params'],
    queryFn: () => capacityApi.getParams(),
  })
  const rawMode = paramsQuery.data?.week_calendar_mode
  const currentMode: WeekCalendarMode = isWeekCalendarMode(rawMode) ? rawMode : DEFAULT_MODE
  const [selected, setSelected] = useState<WeekCalendarMode | null>(null)
  // Only diverge from the server value once the planner has touched the
  // select — before that, always reflect whatever GET /params returned
  // (including the "not yet set" default), so a stale local value can
  // never linger after a refetch.
  const displayedMode = selected ?? currentMode

  const mutation = useMutation({
    mutationFn: (mode: WeekCalendarMode) => capacityApi.setParam('week_calendar_mode', mode),
    onSuccess: async (_, mode) => {
      setSubmitError(null)
      setSavedNote(`Saved — new runs will generate under ${MODE_LABEL[mode]}. Runs already generated keep their own mode.`)
      await queryClient.invalidateQueries({ queryKey: ['mrp-params'] })
    },
    onError: (err) => {
      setSubmitError(errMsg(err, 'Could not save the planning calendar mode — please retry.'))
    },
  })

  function handleSave() {
    if (selected === null || selected === currentMode) return
    setSavedNote(null)
    mutation.mutate(selected)
  }

  // ── Week start day ────────────────────────────────────────────────────
  // Orthogonal to the mode above: the mode decides which month a straddling
  // week belongs to, this decides which weekday a week begins on. Saving it
  // also moves the maintenance weeks onto the new grid server-side, and the
  // response says how many moved — a silent move is nearly as bad as none.
  const rawDow = paramsQuery.data?.week_start_dow
  const currentDow: WeekStartDow = isWeekStartDow(rawDow) ? rawDow : DEFAULT_START_DOW
  const [selectedDow, setSelectedDow] = useState<WeekStartDow | null>(null)
  const displayedDow = selectedDow ?? currentDow
  const [dowError, setDowError] = useState<string | null>(null)
  const [dowNote, setDowNote] = useState<string | null>(null)

  const dowMutation = useMutation({
    mutationFn: (dow: WeekStartDow) => capacityApi.setWeekStartDow(dow),
    onSuccess: async (result, dow) => {
      setDowError(null)
      const moved = result.exceptions_shifted
      setDowNote(
        `Saved — new runs will plan ${WEEK_START_DOW_LABEL[dow]}-start weeks.`
        + (moved > 0
          ? ` ${moved} maintenance week${moved === 1 ? '' : 's'} moved onto the new grid.`
          : ''),
      )
      await queryClient.invalidateQueries({ queryKey: ['mrp-params'] })
      await queryClient.invalidateQueries({ queryKey: ['capacity-exceptions'] })
    },
    onError: (err) => {
      setDowNote(null)
      setDowError(errMsg(err, 'Could not save the week start day — please retry.'))
    },
  })

  function handleSaveDow() {
    if (selectedDow === null || selectedDow === currentDow) return
    setDowNote(null)
    dowMutation.mutate(selectedDow)
  }

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-neutral-900">Planning Calendar</h2>
      <p className="mt-1 text-xs text-neutral-500">
        Which week definition new MPS runs use. Changing it only affects plans generated
        <strong> after</strong> this change — released plans, and any run already open, keep the
        mode they were generated under.
      </p>

      <div className="mt-3 flex flex-col gap-2">
        {WEEK_CALENDAR_MODES.map((mode) => (
          <label
            key={mode}
            className="flex min-h-[44px] cursor-pointer items-start gap-2 rounded-md border border-neutral-200 px-3 py-2 text-sm has-[:checked]:border-primary-300 has-[:checked]:bg-primary-50"
          >
            <input
              type="radio"
              name="week-calendar-mode"
              value={mode}
              checked={displayedMode === mode}
              onChange={() => { setSelected(mode); setSavedNote(null) }}
              disabled={!canWrite || paramsQuery.isLoading || mutation.isPending}
              className="mt-0.5 h-4 w-4 border-neutral-300 text-primary-600 focus:ring-primary-500"
            />
            <span>
              <span className="block font-medium text-neutral-800">{MODE_LABEL[mode]}</span>
              <span className="block text-xs text-neutral-500">{WEEK_CALENDAR_MODE_DESCRIPTION[mode]}</span>
            </span>
          </label>
        ))}
      </div>

      {canWrite && (
        <div className="mt-3 flex items-center gap-2">
          <Button
            type="button" size="sm" className="min-h-[44px]"
            onClick={handleSave}
            disabled={selected === null || selected === currentMode || mutation.isPending}
          >
            {mutation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save
          </Button>
          {savedNote && <p role="status" className="text-xs text-success-700">{savedNote}</p>}
        </div>
      )}

      {submitError && (
        <p role="alert" className="mt-2 flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {submitError}
        </p>
      )}

      <div className="mt-5 border-t border-neutral-200 pt-4">
        <h3 className="text-sm font-semibold text-neutral-900">Week starts on</h3>
        <p className="mt-1 text-xs text-neutral-500">
          Which weekday a production week begins on. Only affects the ISO week modes —
          Month Fixed always starts on the 1st. Maintenance weeks already booked move onto
          the new grid automatically; plans already generated keep their own grid.
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="week-start-dow">Week starts on</label>
          <select
            id="week-start-dow"
            value={displayedDow}
            onChange={(e) => {
              setSelectedDow(Number(e.target.value) as WeekStartDow)
              setDowNote(null)
            }}
            disabled={!canWrite || paramsQuery.isLoading || dowMutation.isPending}
            className="min-h-[44px] rounded-md border border-neutral-300 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:bg-neutral-50"
          >
            {WEEK_START_DOWS.map((dow) => (
              <option key={dow} value={dow}>{WEEK_START_DOW_LABEL[dow]}</option>
            ))}
          </select>

          {canWrite && (
            <Button
              type="button" size="sm" className="min-h-[44px]"
              onClick={handleSaveDow}
              disabled={selectedDow === null || selectedDow === currentDow || dowMutation.isPending}
            >
              {dowMutation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Save
            </Button>
          )}
          {dowNote && <p role="status" className="text-xs text-success-700">{dowNote}</p>}
        </div>

        {dowError && (
          <p role="alert" className="mt-2 flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
            <AlertTriangle className="h-4 w-4 shrink-0" /> {dowError}
          </p>
        )}
      </div>
    </section>
  )
}
