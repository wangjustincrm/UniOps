import type { HelpEntry } from './types'

export interface HelpHit { id: string; entry: HelpEntry }

const norm = (s: string) => s.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')

/** The texts that hold every word of `query` (in the title, the text or the tasks): those whose title
 *  holds them first, then the rest, each in catalog order. An empty query finds nothing. */
export function searchHelp(all: HelpHit[], query: string, limit = 20): HelpHit[] {
  const words = norm(query).split(/\s+/).filter(Boolean)
  if (words.length === 0) return []
  const scored: { hit: HelpHit; title: boolean }[] = []
  for (const hit of all) {
    const title = norm(hit.entry.title ?? '')
    const text = norm([hit.entry.title, hit.entry.body, hit.entry.next, ...(hit.entry.tasks ?? [])].filter(Boolean).join(' '))
    if (words.every((w) => text.includes(w))) scored.push({ hit, title: words.every((w) => title.includes(w)) })
  }
  return [...scored.filter((s) => s.title), ...scored.filter((s) => !s.title)].slice(0, limit).map((s) => s.hit)
}
