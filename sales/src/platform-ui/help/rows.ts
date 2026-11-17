import { GROUPS, type SlotDef } from './tourOrder'
import type { PlannedStop } from './tourStops'
import type { HelpEntry } from './types'

/** One tip in the help drawer or the tips catalog: a tour stop (by slot) with its text, its group, whether
 *  this viewer has seen it, and the help id to light when it is on the page now (null: not on it). */
export interface HelpRow { slot: string; title: string; body: string; group: string; v: number; seen: boolean; anchor: string | null; key: string }

/** The id a stop is seen under: a shared one (the same on every list or record) by its slot, so seen once
 *  is seen everywhere; a page's own (e.g. its status badge, a record's next step) by its scope and slot,
 *  so another page's is still new. */
export const seenKey = (slot: string, group: string | undefined, scope: string) => (GROUPS[group ?? 'page']?.shared ? slot : `${scope}:${slot}`)

interface Sources {
  scope: string
  text: (id: string) => HelpEntry | undefined
  slot: (slot: string) => SlotDef | undefined
  isSeen: (id: string, v?: number) => boolean
}

const row = (slot: string, help: string, anchor: string | null, s: Sources): HelpRow | null => {
  const t = s.text(slot) ?? s.text(help)
  if (!t) return null
  const def = s.slot(slot)
  const v = def?.v ?? 1
  const key = seenKey(slot, def?.group, s.scope)
  return { slot, title: t.title ?? slot, body: t.body, group: def?.group ?? 'page', v, seen: s.isSeen(key, v), anchor, key }
}

/** A page's tips: its stops in tour order (those shown now can be lit), then `extra` ids that are tips
 *  without a place on the page (e.g. how to sort, from "Using lists"). */
export function pageRows(plan: PlannedStop[], extra: string[], s: Sources): HelpRow[] {
  const have = new Set(plan.map((p) => p.slot))
  return [
    ...plan.map((p) => row(p.slot, p.help, p.shown ? p.help : null, s)),
    ...extra.filter((id) => !have.has(id)).map((id) => row(id, id, null, s)),
  ].filter((r): r is HelpRow => !!r)
}

/** Every tip of the app (the tips catalog): every slot in the table that has a text, by its order. */
export function catalogRows(slots: [string, SlotDef][], onPage: Map<string, string>, s: Sources): HelpRow[] {
  return [...slots].sort((a, b) => a[1].order - b[1].order)
    .map(([slot]) => row(slot, slot, onPage.get(slot) ?? null, s))
    .filter((r): r is HelpRow => !!r)
}
