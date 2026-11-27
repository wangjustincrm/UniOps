import type { ActionOptions } from './useAction'

// THE action bar (user 2026-11-27, "keep it clear and user friendly"): every action a record offers — its own and
// the built-in ones (Open in its tab, Copy link, Refresh, Print, History) — is declared once, with where it shows
// and how important it is, and laid out by one rule: at most two buttons (the most important actions that fit
// now), everything else in "⋯"; on a phone everything in "⋯". Not the person's to use: not shown. Not fitting this
// record: greyed with why where the person would expect it, else hidden (the declaration says which), so a pair
// like Mark settled / Undo settled never shows together.

export interface BarItem extends Pick<ActionOptions, 'done' | 'errorMessages' | 'failPrefix' | 'invalidates' | 'after' | 'refreshes'> {
  id: string
  label: string
  /** Its icon before the label (an export: actionIcon('export')). */
  icon?: import('react').ReactNode
  /** A button in the header when it fits now: the lower, the more important. None: only ever in "⋯". */
  primary?: number
  /** Its place in "⋯" within its group (default: as declared). */
  order?: number
  /** The record's own actions come first in "⋯", then the built-in ones. */
  group?: 'record' | 'builtin'
  tone?: 'default' | 'destructive'
  /** Shown only to whom holds it. */
  permission?: string
  /** true, or why it does not fit this record now. */
  fit?: true | string
  /** Not fitting: greyed with why (default), or not shown at all. */
  whenNotFit?: 'greyed' | 'hidden'
  /** A second line in "⋯" (what it does). */
  hint?: string
  /** What it sends out of the company (the server's words). */
  sends?: string
  /** It opens a dialog or form: the menu leaves the focus to it. */
  opensDialog?: boolean
  /** It changes the record (default: the record's own actions do, the built-in ones not): not offered read-only. */
  changes?: boolean
  /** Runs it; a promise runs as an action (its failure, and `done`, said in the status row). */
  onSelect: () => unknown | Promise<unknown>
}

export interface Arranged {
  buttons: BarItem[]
  menu: (BarItem & { separated?: boolean; disabledReason?: string })[]
}

export const MAX_BUTTONS = 2

/** Who may see what, and where: the buttons (at most `max`, the most important that fit now) and the "⋯" menu
 *  (the record's own first, in their order; then the built-in ones after a line). */
export function arrange(items: BarItem[], opts: { can: (key: string) => boolean; phone?: boolean; readOnly?: boolean; max?: number }): Arranged {
  const max = opts.phone ? 0 : (opts.max ?? MAX_BUTTONS)
  const fits = (i: BarItem) => (i.fit ?? true) === true
  const changes = (i: BarItem) => i.changes ?? (i.group ?? 'record') === 'record'
  const shown = items.filter((i) => (!i.permission || opts.can(i.permission)) && (fits(i) || i.whenNotFit !== 'hidden')
    && !(opts.readOnly && changes(i)))
  const buttons = shown.filter((i) => i.primary !== undefined && fits(i))
    .map((i, n) => ({ i, n })).sort((a, b) => a.i.primary! - b.i.primary! || a.n - b.n).slice(0, max).map(({ i }) => i)
  const rest = shown.filter((i) => !buttons.includes(i)).map((i, n) => ({ i, n }))
  const byOrder = (a: { i: BarItem; n: number }, b: { i: BarItem; n: number }) => (a.i.order ?? a.n) - (b.i.order ?? b.n) || a.n - b.n
  // A destructive action is last of its group: it is never what the eye lands on first.
  const destructiveLast = (a: { i: BarItem }, b: { i: BarItem }) => Number(a.i.tone === 'destructive') - Number(b.i.tone === 'destructive')
  const own = rest.filter(({ i }) => (i.group ?? 'record') === 'record').sort(byOrder).sort(destructiveLast).map(({ i }) => i)
  const builtin = rest.filter(({ i }) => i.group === 'builtin').sort(byOrder).map(({ i }) => i)
  const menu = [...own, ...builtin].map((i, n) => ({
    ...i, separated: n > 0 && n === own.length,
    disabledReason: fits(i) ? undefined : (i.fit as string),
  }))
  return { buttons, menu }
}
