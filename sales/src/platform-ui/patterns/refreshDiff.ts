// What a refresh changed (the user, 2026-11-03: "I can not see any changes"):
// the rows before and after, matched by key; a row is changed when its
// version / updated_at moved, or — without either — when any of its own
// fields did (a stable text of them). The totals say what came beyond the page.

export type Key = string | number

export interface RowsDiff {
  added: Key[]
  updated: Key[]
  gone: Key[]
  /** New rows the page does not show (the total grew by more than the page did). */
  beyond: number
}

function stable(v: unknown): string {
  if (v === null || typeof v !== 'object') return JSON.stringify(v) ?? 'undefined'
  if (Array.isArray(v)) return `[${v.map(stable).join(',')}]`
  const o = v as Record<string, unknown>
  return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${stable(o[k])}`).join(',')}}`
}

/** A row's signature: its version / updated_at when it has them, else all of its fields. */
export function signature(row: unknown): string {
  const r = row as Record<string, unknown>
  if (r && typeof r === 'object' && ('version' in r || 'updated_at' in r)) return `${r.version ?? ''}|${r.updated_at ?? ''}`
  return stable(row)
}

export function diffRows<T>(before: T[], after: T[], key: (row: T) => Key, totals?: { before: number; after: number }): RowsDiff {
  const old = new Map(before.map((r) => [key(r), signature(r)]))
  const now = new Set(after.map(key))
  const added: Key[] = [], updated: Key[] = []
  for (const r of after) {
    const k = key(r)
    const was = old.get(k)
    if (was === undefined) added.push(k)
    else if (was !== signature(r)) updated.push(k)
  }
  const gone = before.map(key).filter((k) => !now.has(k))
  const grew = totals ? totals.after - totals.before : 0
  return { added, updated, gone, beyond: Math.max(0, grew - (added.length - gone.length)) }
}

export const changed = (d: RowsDiff) => d.added.length + d.updated.length + d.gone.length + d.beyond > 0

/** "2 new · 1 updated · 1 gone · 3 new (not on this page)", or null when nothing changed. */
export function describeDiff(d: RowsDiff): string | null {
  const parts = [
    d.added.length && `${d.added.length} new`,
    d.updated.length && `${d.updated.length} updated`,
    d.gone.length && `${d.gone.length} gone`,
    d.beyond && `${d.beyond} new (not on this page)`,
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : null
}

/** A record's fields that changed (by name, "status" for fulfillment_status…), and the tabs whose counts moved. */
export function describeRecordChange(before: Record<string, unknown> | undefined, after: Record<string, unknown> | undefined,
  countsBefore: Record<string, number | undefined>, countsAfter: Record<string, number | undefined>,
  labels: Record<string, string> = {}): string | null {
  const noise = new Set(['version', 'updated_at', 'updated_by', 'updated_by_name'])
  const fields = before && after
    ? [...new Set([...Object.keys(before), ...Object.keys(after)])]
      .filter((k) => !noise.has(k) && stable(before[k]) !== stable(after[k])).map((k) => k.replace(/_/g, ' '))
    : []
  const counts = Object.keys(countsAfter).filter((k) => countsBefore[k] !== countsAfter[k] && countsBefore[k] !== undefined)
    .map((k) => `${labels[k] ?? k} (${countsBefore[k]}→${countsAfter[k] ?? 0})`)
  const all = [...counts, ...fields.filter((f) => !counts.some((c) => c.toLowerCase().startsWith(f)))]
  return all.length ? all.slice(0, 5).join(', ') + (all.length > 5 ? ` and ${all.length - 5} more` : '') : null
}
