import type { Stop } from './tourContext'
import { DEFAULT_SLOTS } from './tourOrder'

export interface PlannedStop { key: string; slot: string; order: number; help: string; el: HTMLElement; shown: boolean }

const isShown = (el: HTMLElement) => { const r = el.getBoundingClientRect(); return el.isConnected && r.width > 0 && r.height > 0 }
const docOrder = (a: HTMLElement, b: HTMLElement) => (a === b ? 0 : a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1)

/** A scope's stops in tour order: one per key (its first shown element, else its first), by its slot's
 *  place, then page order; `shown` says whether it can be shown now. */
export function planStops(stops: Stop[], order: (slot: string) => number = (s) => DEFAULT_SLOTS[s]?.order ?? 999): PlannedStop[] {
  const byKey = new Map<string, Stop[]>()
  for (const s of stops) byKey.set(s.key, [...(byKey.get(s.key) ?? []), s])
  return [...byKey.entries()].map(([key, list]) => {
    const sorted = [...list].sort((a, b) => docOrder(a.el, b.el))
    const pick = sorted.find((s) => isShown(s.el)) ?? sorted[0]
    return { key, slot: pick.slot, order: order(pick.slot), help: pick.help, el: pick.el, shown: isShown(pick.el) }
  }).sort((a, b) => a.order - b.order || docOrder(a.el, b.el))
}
